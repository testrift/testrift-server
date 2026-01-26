"""
WebSocket server for TestRift.

Handles NUnit client connections, UI client connections, and log streaming.
"""

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timedelta, UTC

import msgpack
from aiohttp import web

from .config import DEFAULT_RETENTION_DAYS
from .protocol import (
    MSG_RUN_STARTED,
    MSG_RUN_STARTED_RESPONSE,
    MSG_TEST_CASE_STARTED,
    MSG_LOG_BATCH,
    MSG_EXCEPTION,
    MSG_TEST_CASE_FINISHED,
    MSG_RUN_FINISHED,
    MSG_BATCH,
    MSG_HEARTBEAT,
    MSG_METRICS,
    STATUS_RUNNING,
    STATUS_PASSED,
    STATUS_FAILED,
    STATUS_SKIPPED,
    STATUS_ABORTED,
    STATUS_FINISHED,
    DIR_TX,
    DIR_RX,
    PHASE_TEARDOWN,
    F_TYPE,
    F_RUN_ID,
    F_RUN_NAME,
    F_STATUS,
    F_TIMESTAMP,
    F_TC_FULL_NAME,
    F_TC_ID,
    F_MESSAGE,
    F_COMPONENT,
    F_CHANNEL,
    F_DIR,
    F_PHASE,
    F_ENTRIES,
    F_EVENTS,
    F_EVENT_TYPE,
    F_EXCEPTION_TYPE,
    F_STACK_TRACE,
    F_IS_ERROR,
    F_USER_METADATA,
    F_GROUP,
    F_RETENTION_DAYS,
    F_LOCAL_RUN,
    F_ERROR,
    F_RUN_URL,
    F_METRICS,
    F_CPU,
    F_MEMORY,
    F_GROUP_URL,
    F_GROUP_HASH,
)
from .protocol_utils import normalize_message
from .utils import (
    get_run_path,
    get_case_log_path,
    validate_run_id,
    validate_test_case_id,
    validate_custom_run_id,
    normalize_group_payload,
    compute_group_hash,
    find_test_case_by_tc_id,
    write_meta_msgpack,
    read_meta_msgpack,
    get_merged_log_path,
    TC_ID_FIELD,
    TC_FULL_NAME_FIELD,
)
from .models import TestRunData, TestCaseData
from . import database

logger = logging.getLogger(__name__)



def log_event(event: str, **fields):
    """Log an event with timestamp."""
    record = {"event": event, **fields, "ts": datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"}
    logger.info(json.dumps(record))


async def send_msgpack(ws, data):
    """Send MessagePack-encoded data over WebSocket."""
    packed = msgpack.packb(data, use_bin_type=True)
    await ws.send_bytes(packed)


class WebSocketServer:
    """Manages WebSocket connections for NUnit clients and UI clients."""

    def __init__(self):
        self.test_runs: dict[str, TestRunData] = {}  # run_id -> TestRunData
        self.ui_clients = set()  # websockets for UI clients

    async def get_unique_run_name(self, base_name: str, group_hash: str = None) -> str:
        """
        Ensure run_name is unique within a group by appending a counter if needed.
        E.g., "My Run" -> "My Run", "My Run 1", "My Run 2", etc.
        Names are scoped per group - the same name can exist in different groups.
        """
        # Check both in-memory runs and database
        existing_names = set()

        # Check in-memory runs (filter by group_hash)
        for run in self.test_runs.values():
            if run.run_name and run.group_hash == group_hash:
                existing_names.add(run.run_name)

        # Check database (filter by group_hash)
        try:
            db_names = await database.db.get_run_names_starting_with(base_name, group_hash)
            existing_names.update(db_names)
        except Exception as e:
            logger.error(f"Error checking existing run names: {e}")

        # If base_name doesn't exist, use it
        if base_name not in existing_names:
            return base_name

        # Find the next available counter
        counter = 1
        while True:
            candidate = f"{base_name} {counter}"
            if candidate not in existing_names:
                return candidate
            counter += 1

    async def handle_ws(self, request):
        """Main WebSocket handler that routes to appropriate sub-handler."""
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        path = request.path
        if path == "/ws/nunit":
            await self.handle_nunit_ws(ws)
        elif path == "/ws/ui":
            await self.handle_ui_ws(ws)
        else:
            # Try matching /ws/logs/{run_id}/{test_case_id}
            match = re.match(r"^/ws/logs/([^/]+)/([^/]+)$", path)
            if match:
                run_id = match.group(1)
                test_case_id = match.group(2)
                await self.handle_log_stream(ws, run_id, test_case_id)
            else:
                await ws.close()

        return ws

    async def handle_nunit_ws(self, ws):
        """Handle WebSocket connection from NUnit test client."""
        run = None
        last_activity = datetime.now(UTC)

        # Helper function to mark run as aborted
        async def mark_run_aborted(reason):
            nonlocal run
            if run is None:
                logger.debug(f"mark_run_aborted called but run is None, ignoring (reason={reason})")
                return
            if run.status != "running":
                logger.debug(f"mark_run_aborted called but run {run.id} is already {run.status}, ignoring (reason={reason})")
                return
            logger.info(f"Marking run {run.id} as aborted: {reason}")
            run.status = "aborted"
            run.abort_reason = reason
            run.end_time = datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"
            run.update_last()

            # Mark all running test cases as aborted
            aborted_test_cases = []
            for tc_id, test_case in run.test_cases.items():
                if test_case.status == "running":
                    logger.info(f"Marking test case {tc_id} as aborted")
                    test_case.status = "aborted"
                    test_case.end_time = datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"
                    aborted_test_cases.append(tc_id)

            # Save to disk
            current_meta = read_meta_msgpack(run.id) or {}
            run_data = run.to_dict()
            if "deletes_at" in current_meta:
                run_data["deletes_at"] = current_meta["deletes_at"]
            write_meta_msgpack(run.id, run_data)

            # Calculate updated counts after aborting test cases
            passed_count = 0
            failed_count = 0
            skipped_count = 0
            aborted_count = 0

            for tc in run.test_cases.values():
                if tc.status.lower() in ['passed', 'failed', 'skipped', 'aborted']:
                    status = tc.status.lower()
                    if status == 'passed':
                        passed_count += 1
                    elif status == 'failed':
                        failed_count += 1
                    elif status == 'skipped':
                        skipped_count += 1
                    elif status == 'aborted':
                        aborted_count += 1

            # Broadcast test case updates for all aborted test cases and log to database
            for tc_full_name in aborted_test_cases:
                test_case = run.test_cases[tc_full_name]
                tc_meta = test_case.to_dict()

                # Log test case as aborted in database
                try:
                    await database.log_test_case_finished(run.id, tc_full_name, 'aborted')
                except Exception as db_error:
                    logger.error(f"Database logging error for aborted test case {tc_full_name}: {db_error}")

                # Broadcast UI update
                await self.broadcast_ui({
                    "type": "test_case_finished",
                    "run_id": run.id,
                    "test_case_id": test_case.tc_id,
                    "test_case_full_name": tc_full_name,
                    "tc_meta": tc_meta,
                    "counts": {
                        "passed": passed_count,
                        "failed": failed_count,
                        "skipped": skipped_count,
                        "aborted": aborted_count
                    }
                })

            # Log run finished to database
            try:
                await database.log_test_run_finished(run.id, "aborted")
            except Exception as db_error:
                logger.error(f"Database logging error for run_aborted: {db_error}")

            # Broadcast run finished event
            await self.broadcast_ui({"type": "run_finished", "run": run_data})

            # Remove aborted run from memory
            if run.id in self.test_runs:
                del self.test_runs[run.id]
                logger.info(f"Removed aborted run {run.id} from memory")

        # Background task to monitor connection timeout
        async def monitor_connection():
            nonlocal run, last_activity
            iteration = 0
            while True:
                try:
                    await asyncio.sleep(5)  # Check every 5 seconds
                    iteration += 1

                    # Check if run is already finished - no need to monitor anymore
                    if run is None or run.status != "running":
                        logger.debug(f"Monitor[{iteration}]: run is finished or None, stopping monitor")
                        break

                    if ws.closed:
                        logger.info(f"Monitor[{iteration}]: WebSocket is closed, aborting run")
                        await mark_run_aborted("WebSocket closed")
                        break

                    # Try to send a ping frame to test the connection
                    try:
                        await ws.ping()
                        # Ping/pong success doesn't count as "activity" - only client messages do
                        if run:
                            time_since_activity = (datetime.now(UTC) - last_activity).total_seconds()
                            logger.debug(f"Monitor[{iteration}]: ping OK, run={run.id}, time_since_activity={time_since_activity:.1f}s")
                    except Exception as e:
                        # Ping failed - socket is closing. Don't abort immediately.
                        # The receive loop will either:
                        # 1. Process run_finished that's already in the queue -> run finishes normally
                        # 2. Timeout on inactivity -> run gets aborted by timeout handler
                        logger.info(f"Monitor[{iteration}]: WebSocket ping failed ({e}), stopping monitor")
                        break

                    if run and run.status == "running":
                        time_since_activity = (datetime.now(UTC) - last_activity).total_seconds()
                        if time_since_activity > 30:  # 30 second timeout
                            logger.warning(f"Monitor[{iteration}]: WebSocket watchdog triggered: no activity for {time_since_activity:.1f}s (run_id={run.id if run else 'unknown'})")
                            await mark_run_aborted("Connection timeout")
                            break
                except asyncio.CancelledError:
                    logger.debug(f"Monitor[{iteration}]: cancelled")
                    break
                except Exception as e:
                    logger.info(f"Monitor[{iteration}]: error: {e}")
                    break

        monitor_task = asyncio.create_task(monitor_connection())

        # Per-connection string table for interned strings
        string_table = {}

        try:
            logger.info(f"Starting NUnit WebSocket connection monitoring")
            async for msg in ws:
                last_activity = datetime.now(UTC)
                logger.info(f"Received message from NUnit client: {msg.type}")

                if msg.type == web.WSMsgType.CLOSE:
                    logger.info(f"NUnit WebSocket connection closed normally for run {run.id if run else 'unknown'}")
                    if run and run.status == "running":
                        await mark_run_aborted("WebSocket closed before run_finished was sent")
                    break
                elif msg.type == web.WSMsgType.ERROR:
                    logger.info(f"NUnit WebSocket connection error: {ws.exception()}")
                    if run and run.status == "running":
                        await mark_run_aborted("WebSocket error before run_finished was sent")
                    break

                if msg.type == web.WSMsgType.BINARY:
                    try:
                        raw_message = msgpack.unpackb(msg.data, raw=False)
                        data = normalize_message(raw_message, string_table)
                        msg_type = data.get("type")
                    except Exception as e:
                        logger.error(f"Error parsing MessagePack message: {e}")
                        continue

                    if msg_type == "run_started":
                        run = await self._handle_run_started(ws, data, string_table)

                    elif msg_type == "batch":
                        await self._handle_batch(data, run, raw_message)

                    elif msg_type == "heartbeat":
                        # Client heartbeat - just acknowledge receipt, activity is tracked by message receipt
                        logger.debug(f"Heartbeat received for run {data.get('run_id', 'unknown')}")

                    elif msg_type == "metrics":
                        await self._handle_metrics(data, run)

                    elif msg_type == "test_case_started":
                        await self._handle_test_case_started(data, run)

                    elif msg_type == "log_batch":
                        await self._handle_log_batch(data, run, raw_message)

                    elif msg_type == "exception":
                        await self._handle_exception(data, run)

                    elif msg_type == "test_case_finished":
                        await self._handle_test_case_finished(data, run)

                    elif msg_type == "run_finished":
                        await self._handle_run_finished(data, run)
                        run = None  # Clear run reference after finished

        except Exception as e:
            logger.error(f"NUnit WebSocket connection error: {e}")
            if run and run.status == "running":
                await mark_run_aborted("WebSocket connection exception")
        finally:
            logger.info(f"Cleaning up NUnit WebSocket connection for run {run.id if run else 'unknown'}")

            if run and run.status == "running":
                unfinished_cases = [tc for tc in run.test_cases.values() if tc.status == "running"]
                if unfinished_cases:
                    logger.info(f"Run {run.id} still has {len(unfinished_cases)} running test cases when WebSocket closed, marking as aborted")
                    await mark_run_aborted("WebSocket closed while run was still running")
                else:
                    logger.info(f"Run {run.id} has no running test cases; finalizing as finished after WebSocket close")
                    await self._handle_run_finished({"run_id": run.id, "status": "finished"}, run)
                    run = None

            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass

    async def _handle_run_started(self, ws, data, string_table):
        """Handle run_started message from NUnit client."""
        try:
            # Check if client provided a custom run_id
            client_run_id = data.get("run_id")
            validation_error = None

            if client_run_id:
                # Validate the custom run ID
                is_valid, error_msg = validate_custom_run_id(client_run_id)
                if not is_valid:
                    validation_error = error_msg
                else:
                    # Check if run_id already exists
                    if client_run_id in self.test_runs:
                        validation_error = f"Run ID '{client_run_id}' is already in use"
                    else:
                        try:
                            existing_run = await database.db.get_test_run_by_id(client_run_id)
                            if existing_run:
                                validation_error = f"Run ID '{client_run_id}' is already in use"
                        except Exception as db_check_error:
                            logger.error(f"Error checking database for run_id: {db_check_error}")
                            validation_error = "Error validating run ID"

                if validation_error:
                    error_response = {
                        F_TYPE: MSG_RUN_STARTED_RESPONSE,
                        F_ERROR: validation_error
                    }
                    await send_msgpack(ws, error_response)
                    return None

                run_id = client_run_id
            else:
                run_id = uuid.uuid4().hex[:12]

            retention_days = data.get("retention_days", DEFAULT_RETENTION_DAYS)
            local_run = data.get("local_run", False)
            user_metadata = data.get("user_metadata", {})
            raw_group = data.get("group")
            group_payload = normalize_group_payload(raw_group)
            group_hash = compute_group_hash(group_payload) if group_payload else None

            # Get or generate run_name
            run_name = data.get("run_name")
            if not run_name:
                run_name = datetime.now(UTC).strftime("Run %Y-%m-%d %H:%M:%S")

            run_name = await self.get_unique_run_name(run_name, group_hash)
            start_time = data.get("start_time")

            if run_id in self.test_runs:
                self.test_runs.pop(run_id)
            run = TestRunData(run_id, retention_days, local_run, user_metadata, group_payload, group_hash, run_name)

            if start_time:
                run.start_time = start_time

            # Compute deletes_at for server-side retention
            try:
                days = int(retention_days) if retention_days is not None else None
            except Exception:
                days = None
            if days:
                deletes_at = (datetime.now(UTC) + timedelta(days=days)).replace(tzinfo=None).isoformat() + "Z"
            else:
                deletes_at = None

            self.test_runs[run_id] = run

            # Store reference to the string table so it gets updated as messages arrive
            run.string_table = string_table

            # Create folder and save meta
            run_path = get_run_path(run_id)
            run_path.mkdir(parents=True, exist_ok=True)
            meta_dict = run.to_dict()
            if deletes_at:
                meta_dict["deletes_at"] = deletes_at
            write_meta_msgpack(run_id, meta_dict)

            log_event("run_started", run_id=run_id, run_name=run_name, retention_days=retention_days, deletes_at=deletes_at, user_metadata=user_metadata)

            # Log to database
            try:
                await database.log_test_run_started(
                    run_id,
                    retention_days,
                    local_run,
                    user_metadata,
                    run_name=run_name,
                    group_name=group_payload["name"] if group_payload else None,
                    group_hash=group_hash,
                    group_metadata=(group_payload or {}).get("metadata")
                )
            except Exception as db_error:
                logger.error(f"Database logging error for run_started: {db_error}")

            # Broadcast to UI clients
            await self.broadcast_ui({"type": "run_started", "run": meta_dict})

            # Send response to NUnit client (using optimized protocol)
            response = {
                F_TYPE: MSG_RUN_STARTED_RESPONSE,
                F_RUN_ID: run_id,
                F_RUN_NAME: run_name,
                F_RUN_URL: f"/testRun/{run_id}/index.html"
            }
            if group_hash:
                response[F_GROUP_HASH] = group_hash
                response[F_GROUP_URL] = f"/groups/{group_hash}"
            await send_msgpack(ws, response)

            return run

        except Exception as e:
            logger.error(f"Error in run_started: {e}")
            import traceback
            traceback.print_exc()
            return None

    async def _handle_batch(self, data, run, raw_message):
        """Handle batch message containing multiple events for high-throughput scenarios."""
        try:
            run_id = data.get("run_id")
            events = data.get("events", [])
            raw_events = raw_message.get(F_EVENTS, []) if isinstance(raw_message, dict) else []

            if not run_id:
                logger.info("Error: run_id missing from batch message")
                return

            if not run:
                run = self.test_runs.get(run_id)

            if not run:
                logger.info(f"Error: Run '{run_id}' not found for batch message")
                return

            if len(raw_events) != len(events):
                raise ValueError("Batch event count mismatch between raw and decoded payloads")

            # Process events in order
            for event, raw_event in zip(events, raw_events):
                if raw_event is None:
                    raise ValueError("Missing raw event payload for compact log storage")
                event_type = event.get("event_type")
                # Inject run_id into event for handler compatibility
                event["run_id"] = run_id

                if event_type == "test_case_started":
                    await self._handle_test_case_started(event, run)
                elif event_type == "log_batch":
                    await self._handle_log_batch(event, run, raw_event)
                elif event_type == "exception":
                    await self._handle_exception(event, run)
                elif event_type == "test_case_finished":
                    await self._handle_test_case_finished(event, run)
                else:
                    logger.warning(f"Unknown event_type in batch: {event_type}")

            log_event("batch", run_id=run_id, event_count=len(events))

        except Exception as e:
            logger.error(f"Error in batch: {e}")
            import traceback
            traceback.print_exc()

    async def _handle_test_case_started(self, data, run):
        """Handle test_case_started message."""
        try:
            run_id = data.get("run_id")
            tc_full_name = data.get("tc_full_name")
            tc_id = data.get("tc_id")

            if not run_id:
                logger.info("Error: run_id missing from test_case_started message")
                return

            if not tc_full_name:
                logger.info("Error: tc_full_name missing from test_case_started message")
                return

            if not tc_id:
                logger.info("Error: tc_id missing from test_case_started message")
                return

            if not validate_test_case_id(tc_id):
                logger.info(f"Error: Invalid tc_id '{tc_id}' - must be alphanumeric with hyphens")
                return

            run = self.test_runs.get(run_id)
            if not run:
                logger.info(f"Error: Run '{run_id}' not found for test_case_started message")
                return

            # Replace HTML entities with actual quotes
            tc_full_name = tc_full_name.replace("&quot;", '"')
            tc_meta = dict(data.get("tc_meta", {}) or {})

            tc_meta[TC_ID_FIELD] = tc_id
            tc_meta[TC_FULL_NAME_FIELD] = tc_full_name

            test_case_obj = TestCaseData(run, tc_full_name, tc_meta)
            run.test_cases[tc_full_name] = test_case_obj
            run.test_cases_by_tc_id[test_case_obj.tc_id] = test_case_obj
            run.update_last()

            # Ensure log file exists
            log_path = get_case_log_path(run.id, tc_id=test_case_obj.tc_id)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            if not log_path.exists():
                with open(log_path, "w", encoding="utf-8") as f:
                    pass

            log_event("test_case_started", run_id=run.id, test_case_id=tc_full_name)

            # Log to database
            try:
                await database.log_test_case_started(run.id, tc_full_name, tc_id, tc_meta.get("start_time"))
            except Exception as db_error:
                logger.error(f"Database logging error for test_case_started: {db_error}")

            # Update meta on disk
            current_meta = read_meta_msgpack(run.id) or {}
            run_data = run.to_dict()
            if "deletes_at" in current_meta:
                run_data["deletes_at"] = current_meta["deletes_at"]
            write_meta_msgpack(run.id, run_data)

            # Calculate counts
            passed_count, failed_count, skipped_count, aborted_count = self._count_test_statuses(run)

            # Broadcast targeted test_case_started event
            await self.broadcast_ui({
                "type": "test_case_started",
                "run_id": run.id,
                "test_case_id": tc_id,
                "test_case_full_name": tc_full_name,
                "tc_meta": tc_meta,
                "counts": {
                    "passed": passed_count,
                    "failed": failed_count,
                    "skipped": skipped_count,
                    "aborted": aborted_count
                }
            })

        except Exception as e:
            logger.error(f"Error in test_case_started: {e}")
            import traceback
            traceback.print_exc()

    async def _handle_log_batch(self, data, run, raw_message=None):
        """Handle log_batch message."""
        try:
            run_id = data.get("run_id")
            tc_id = data.get("tc_id")

            if not run_id:
                logger.info("Error: run_id missing from log_batch message")
                return

            if not tc_id:
                logger.info("Error: tc_id missing from log_batch message")
                return

            run = self.test_runs.get(run_id)
            if not run:
                logger.info(f"Error: Run '{run_id}' not found for log_batch message")
                return

            test_case = run.test_cases_by_tc_id.get(tc_id)
            if not test_case:
                logger.info(f"Error: Test case with tc_id '{tc_id}' not found in run '{run_id}'")
                return

            if raw_message is None or not isinstance(raw_message, dict):
                raise ValueError("Missing raw log_batch payload for compact storage")

            # Get raw entries directly - no decoding needed on server
            raw_entries = raw_message.get(F_ENTRIES, []) or []

            run.update_last()
            await test_case.add_log_entries(raw_entries)
            log_event("log_batch", run_id=run.id, tc_id=tc_id, count=len(raw_entries))

        except Exception as e:
            logger.error(f"Error in log_batch: {e}")
            import traceback
            traceback.print_exc()

    async def _handle_exception(self, data, run):
        """Handle exception message."""
        try:
            run_id = data.get("run_id")
            tc_id = data.get("tc_id")

            if not run_id or not tc_id:
                logger.info("Error: run_id or tc_id missing from exception message")
                return

            run = self.test_runs.get(run_id)
            if not run:
                logger.info(f"Error: Run '{run_id}' not found for exception message")
                return

            test_case = run.test_cases_by_tc_id.get(tc_id)
            if not test_case:
                logger.info(f"Error: Test case with tc_id '{tc_id}' not found in run '{run_id}'")
                return

            timestamp = data.get("timestamp") or datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"
            message_text = data.get("message", "")
            exception_type = data.get("exception_type", "")
            stack_trace_value = data.get("stack_trace") or []
            is_error = bool(data.get("is_error", False))

            trace_entry = {
                "timestamp": timestamp,
                "message": message_text,
                "exception_type": exception_type,
                "stack_trace": stack_trace_value,
                "is_error": is_error,
            }

            await test_case.add_stack_trace(trace_entry)
            run.update_last()

            # Persist updated metadata to disk
            current_meta = read_meta_msgpack(run.id) or {}
            run_data = run.to_dict()
            if "deletes_at" in current_meta:
                run_data["deletes_at"] = current_meta["deletes_at"]
            write_meta_msgpack(run.id, run_data)

            log_event("exception", run_id=run.id, test_case_id=test_case.full_name)

        except Exception as e:
            logger.error(f"Error in exception handling: {e}")
            import traceback
            traceback.print_exc()

    async def _handle_test_case_finished(self, data, run):
        """Handle test_case_finished message."""
        try:
            run_id = data.get("run_id")
            tc_id = data.get("tc_id")

            if not run_id:
                logger.info("Error: run_id missing from test_case_finished message")
                return

            if not tc_id:
                logger.info("Error: tc_id missing from test_case_finished message")
                return

            run = self.test_runs.get(run_id)
            if not run:
                logger.info(f"Error: Run '{run_id}' not found for test_case_finished message")
                return

            test_case = run.test_cases_by_tc_id.get(tc_id)
            if not test_case:
                logger.info(f"Error: Test case with tc_id '{tc_id}' not found in run '{run_id}'")
                return

            # Validate and set status
            status = data.get("status", "").lower()
            if status in ['passed', 'failed', 'skipped', 'aborted', 'error']:
                test_case.status = status
                test_case.end_time = datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"
            else:
                logger.info(f"Error: Invalid test status '{data.get('status')}' for test case {test_case.full_name}, ignoring test case")
                return

            tc_meta = test_case.to_dict()
            passed_count, failed_count, skipped_count, aborted_count = self._count_test_statuses(run)

            # Broadcast targeted test_case_updated event
            await self.broadcast_ui({
                "type": "test_case_updated",
                "run_id": run.id,
                "test_case_id": test_case.tc_id,
                "test_case_full_name": test_case.full_name,
                "tc_meta": tc_meta,
                "counts": {
                    "passed": passed_count,
                    "failed": failed_count,
                    "skipped": skipped_count,
                    "aborted": aborted_count
                }
            })

            run.update_last()

            # Update meta on disk
            current_meta = read_meta_msgpack(run.id) or {}
            run_data = run.to_dict()
            if "deletes_at" in current_meta:
                run_data["deletes_at"] = current_meta["deletes_at"]
            write_meta_msgpack(run.id, run_data)

            log_event("test_case_finished", run_id=run.id, tc_id=tc_id, status=test_case.status)

            # Log to database
            try:
                await database.log_test_case_finished(run.id, test_case.full_name, test_case.status)
            except Exception as db_error:
                logger.error(f"Database logging error for test_case_finished: {db_error}")

            # Broadcast targeted test_case_finished event
            await self.broadcast_ui({
                "type": "test_case_finished",
                "run_id": run.id,
                "test_case_id": test_case.tc_id,
                "test_case_full_name": test_case.full_name,
                "tc_meta": tc_meta,
                "counts": {
                    "passed": passed_count,
                    "failed": failed_count,
                    "skipped": skipped_count,
                    "aborted": aborted_count
                }
            })

        except Exception as e:
            logger.error(f"Error in test_case_finished: {e}")
            import traceback
            traceback.print_exc()

    async def _handle_metrics(self, data, run):
        """Handle metrics message with CPU and memory samples."""
        try:
            if not run:
                logger.debug("Metrics received but no active run")
                return

            metrics = data.get("metrics", [])
            if not metrics:
                return

            # Append metrics to run's metrics list
            for sample in metrics:
                ts = sample.get("ts") or sample.get("timestamp")
                cpu = sample.get("cpu", 0)
                mem = sample.get("mem") or sample.get("memory", 0)
                net = sample.get("net", 0)
                ni = sample.get("ni")

                metric_entry = {
                    "ts": ts,
                    "cpu": cpu,
                    "mem": mem,
                    "net": net
                }
                if ni:
                    metric_entry["ni"] = ni
                run.metrics.append(metric_entry)

            logger.debug(f"Received {len(metrics)} metrics samples for run {run.id}, total: {len(run.metrics)}")

            # Broadcast metrics to UI clients
            await self.broadcast_ui({
                "type": "metrics",
                "run_id": run.id,
                "metrics": metrics
            })

        except Exception as e:
            logger.error(f"Error handling metrics: {e}")

    async def _handle_run_finished(self, data, run):
        """Handle run_finished message."""
        try:
            run_id = data.get("run_id")

            if not run_id:
                logger.info("Error: run_id missing from run_finished message")
                return

            run = self.test_runs.get(run_id)
            if not run:
                logger.info(f"Error: Run '{run_id}' not found for run_finished message")
                return

            # Check for any test cases still in "running" state
            aborted_test_cases = []
            for tc_full_name, test_case in run.test_cases.items():
                if test_case.status == "running":
                    logger.info(f"Test case {tc_full_name} was still running when run_finished received, marking as aborted")
                    test_case.status = "aborted"
                    test_case.end_time = datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"
                    aborted_test_cases.append(tc_full_name)

                    try:
                        await database.log_test_case_finished(run.id, tc_full_name, 'aborted')
                    except Exception as db_error:
                        logger.error(f"Database logging error for aborted test case {tc_full_name}: {db_error}")

            # Broadcast updates for aborted test cases
            if aborted_test_cases:
                passed_count, failed_count, skipped_count, aborted_count = self._count_test_statuses(run)

                for tc_full_name in aborted_test_cases:
                    test_case = run.test_cases[tc_full_name]
                    tc_meta = test_case.to_dict()
                    await self.broadcast_ui({
                        "type": "test_case_finished",
                        "run_id": run.id,
                        "test_case_id": test_case.tc_id,
                        "test_case_full_name": tc_full_name,
                        "tc_meta": tc_meta,
                        "counts": {
                            "passed": passed_count,
                            "failed": failed_count,
                            "skipped": skipped_count,
                            "aborted": aborted_count
                        }
                    })

            run.status = data.get("status", "finished")
            run.end_time = datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"
            run.update_last()

            # Merge all test case logs into a single .mplog file
            await self._merge_logs_for_run(run)

            # Update meta on disk with offsets
            current_meta = read_meta_msgpack(run.id) or {}
            run_data = run.to_dict()
            if "deletes_at" in current_meta:
                run_data["deletes_at"] = current_meta["deletes_at"]
            write_meta_msgpack(run.id, run_data)

            log_event("run_finished", run_id=run.id, status=run.status)

            # Log to database
            try:
                await database.log_test_run_finished(run.id, run.status)
            except Exception as db_error:
                logger.error(f"Database logging error for run_finished: {db_error}")

            # Broadcast to UI
            await self.broadcast_ui({"type": "run_finished", "run": run_data})

            # Remove finished run from memory
            if run_id in self.test_runs:
                del self.test_runs[run_id]
                logger.info(f"Removed finished run {run_id} from memory")

        except Exception:
            logger.exception("Error in run_finished")

    async def _merge_logs_for_run(self, run):
        """Merge all individual test case .mplog files into a single logs.mplog file.

        Updates each test case in run.test_cases with log_offset and log_count
        for efficient retrieval from the merged file.
        """
        import struct
        from .utils import (
            get_case_log_path,
            get_case_stack_path,
            read_mplog_raw,
            CASE_STORAGE_DIR_NAME,
        )

        run_path = get_run_path(run.id)
        merged_path = get_merged_log_path(run.id)
        cases_dir = run_path / CASE_STORAGE_DIR_NAME

        try:
            with open(merged_path, "wb") as merged_file:
                for tc_full_name, test_case in run.test_cases.items():
                    tc_id = test_case.tc_id

                    # Record starting offset for this test case
                    log_start_offset = merged_file.tell()

                    # Merge log entries
                    log_path = get_case_log_path(run.id, tc_id=tc_id)
                    log_entry_count = 0
                    if log_path.exists():
                        raw_entries = read_mplog_raw(log_path)
                        for _, raw_data in raw_entries:
                            merged_file.write(raw_data)
                            log_entry_count += 1

                    # Merge stack traces (exceptions)
                    stack_path = get_case_stack_path(run.id, tc_id=tc_id)
                    stack_entry_count = 0
                    if stack_path.exists():
                        raw_entries = read_mplog_raw(stack_path)
                        for _, raw_data in raw_entries:
                            merged_file.write(raw_data)
                            stack_entry_count += 1

                    # Store offsets in test case for meta
                    test_case.log_offset = log_start_offset
                    test_case.log_count = log_entry_count
                    test_case.stack_count = stack_entry_count

            # Clean up individual log files after successful merge (preserve attachments)
            if cases_dir.exists():
                self._cleanup_case_log_files(cases_dir, run.id)

            logger.info(f"Merged logs for run {run.id} into {merged_path}")

        except Exception as e:
            logger.error(f"Error merging logs for run {run.id}: {e}")

    def _cleanup_case_log_files(self, cases_dir, run_id):
        """Clean up individual log files while preserving attachments.

        Deletes _log.mplog and _stack.mplog files from cases directory,
        and removes the cases directory if completely empty.
        Preserves tc_id subdirectories that contain attachments.
        """
        # Delete all log/stack files in cases_dir (they're flat files, not in subdirs)
        for log_file in list(cases_dir.glob("*_log.mplog")):
            try:
                log_file.unlink()
                logger.debug(f"Deleted log file {log_file}")
            except Exception as e:
                logger.warning(f"Failed to delete log file {log_file}: {e}")

        for stack_file in list(cases_dir.glob("*_stack.mplog")):
            try:
                stack_file.unlink()
                logger.debug(f"Deleted stack file {stack_file}")
            except Exception as e:
                logger.warning(f"Failed to delete stack file {stack_file}: {e}")

        # Remove cases_dir if completely empty (no attachment subdirectories)
        try:
            if cases_dir.exists() and not any(cases_dir.iterdir()):
                cases_dir.rmdir()
                logger.info(f"Cleaned up cases directory for run {run_id}")
            else:
                logger.info(f"Cleaned up log files for run {run_id}, preserved attachments")
        except Exception as e:
            logger.warning(f"Failed to remove cases directory: {e}")

    def _count_test_statuses(self, run):
        """Count test case statuses for a run."""
        passed_count = 0
        failed_count = 0
        skipped_count = 0
        aborted_count = 0

        for tc in run.test_cases.values():
            if tc.status.lower() in ['passed', 'failed', 'skipped', 'aborted']:
                status = tc.status.lower()
                if status == 'passed':
                    passed_count += 1
                elif status == 'failed':
                    failed_count += 1
                elif status == 'skipped':
                    skipped_count += 1
                elif status == 'aborted':
                    aborted_count += 1

        return passed_count, failed_count, skipped_count, aborted_count

    async def handle_ui_ws(self, ws):
        """Handle WebSocket connection from UI client."""
        self.ui_clients.add(ws)
        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    # UI clients currently send no commands
                    pass
                elif msg.type == web.WSMsgType.ERROR:
                    logger.error("UI ws connection closed with exception %s", ws.exception())
        finally:
            self.ui_clients.remove(ws)

    async def handle_log_stream(self, ws, run_id, test_case_id):
        """Handle WebSocket connection for live log streaming."""
        logger.info(f"WebSocket log stream request: run_id={run_id}, test_case_storage_id={test_case_id}")

        if not validate_run_id(run_id) or not validate_test_case_id(test_case_id):
            logger.info(f"Invalid run_id or test_case_id: {run_id}, {test_case_id}")
            await send_msgpack(ws, {"type": "error", "message": "Invalid run ID or test case ID"})
            await ws.close()
            return

        test_run = self.test_runs.get(run_id)
        if not test_run:
            logger.info(f"Test run not found in memory: {run_id}")
            await send_msgpack(ws, {"type": "error", "message": "Test run not found"})
            await ws.close()
            return

        test_case = find_test_case_by_tc_id(test_run, test_case_id)
        if not test_case:
            logger.info(f"Couldn't find test case {test_case_id} in test run {run_id}")
            await send_msgpack(ws, {"type": "error", "message": "Test case not found"})
            await ws.close()
            return

        logger.info(f"WebSocket log stream established for {run_id}/{test_case_id}")

        # Send the string table first so UI can decode interned strings
        try:
            if test_run.string_table:
                await send_msgpack(ws, {
                    "type": "string_table",
                    "strings": test_run.string_table
                })
        except Exception as e:
            logger.error(f"Error sending string table: {e}")

        # Send all existing logs + exceptions first, then subscribe to new ones
        try:
            initial_items = []

            # Existing log entries
            for existing_log in test_case.logs:
                ts = existing_log.get("timestamp", "")
                initial_items.append((ts, existing_log))

            # Existing exceptions/stack traces
            for trace in getattr(test_case, "stack_traces", []) or []:
                ts = trace.get("timestamp", "")
                payload = {"type": "exception", **trace}
                initial_items.append((ts, payload))

            # Sort by timestamp
            initial_items.sort(key=lambda x: x[0] or "")

            logger.info(f"Replaying {len(initial_items)} log entries for {run_id}/{test_case_id}")
            for _, item in initial_items:
                await send_msgpack(ws, item)
            logger.info(f"Finished replaying log entries for {run_id}/{test_case_id}")

        except Exception as e:
            logger.error(f"Error sending existing logs: {e}")
            await send_msgpack(ws, {"type": "error", "message": "Error sending existing logs"})
            await ws.close()
            return

        # Subscribe to future log entries
        queue = asyncio.Queue()
        test_case.subscribers.append(queue)

        try:
            while True:
                entry = await queue.get()
                await send_msgpack(ws, entry)
        except Exception:
            pass
        finally:
            test_case.subscribers.remove(queue)

    async def broadcast_ui(self, message):
        """Broadcast a message to all connected UI clients."""
        packed = msgpack.packb(message, use_bin_type=True)
        dead = []
        for ws in self.ui_clients:
            try:
                await ws.send_bytes(packed)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.ui_clients.remove(ws)
