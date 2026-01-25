"""
Cleanup tasks for TestRift server.

Handles retention-based cleanup and abandoned run detection.
"""

import asyncio
import logging
import shutil
from datetime import datetime, UTC

from .config import DATA_DIR
from .utils import get_run_path, now_utc_iso
from . import database

logger = logging.getLogger(__name__)


async def cleanup_abandoned_running_runs():
    """Clean up runs that were left in running state due to server restart."""
    logger.info("Checking for abandoned running runs on server startup...")

    try:
        # Get all runs that might have running test cases
        all_runs = await database.db.get_test_runs(limit=10000)

        # Filter for runs that are either running or aborted
        running_or_aborted_runs = [run for run in all_runs if run.get('status') in ('running', 'aborted')]

        for run in running_or_aborted_runs:
            run_id = run['run_id']
            run_status = run.get('status')
            logger.info(f"Found abandoned {run_status} run: {run_id}")

            # Get test cases for this run from database
            test_cases = await database.db.get_test_cases_for_run(run_id)

            # Find the last test case event time
            last_tc_event_time = None
            aborted_count = 0

            # Abort any running test cases
            for tc in test_cases:
                if tc.get('status') == 'running':
                    start_time = tc.get('start_time')

                    # Update test case to aborted in database
                    try:
                        await database.log_test_case_finished(
                            run_id,
                            tc['tc_full_name'],
                            'aborted'
                        )
                        aborted_count += 1

                        # Track the latest event time
                        if start_time and (not last_tc_event_time or start_time > last_tc_event_time):
                            last_tc_event_time = start_time
                    except Exception as e:
                        logger.error(f"Error aborting test case {tc['tc_full_name']}: {e}")

            # Only update run status if it's still running
            if run_status == 'running' and aborted_count > 0:
                # Set run end time
                run_end_time = last_tc_event_time if last_tc_event_time else now_utc_iso()

                # Update run status to aborted in database
                try:
                    await database.log_test_run_finished(run_id, run_end_time, 'aborted')
                    logger.info(f"Aborted run {run_id}: {aborted_count} test cases marked as aborted")
                    _log_event("run_aborted_on_startup", run_id=run_id, aborted_test_cases=aborted_count)
                except Exception as e:
                    logger.error(f"Error aborting run {run_id}: {e}")
            elif aborted_count > 0:
                logger.info(f"Updated {aborted_count} test cases to aborted status for already-aborted run {run_id}")

    except Exception as e:
        logger.error(f"Error during cleanup_abandoned_running_runs: {e}")


async def cleanup_runs_sweep():
    """Sweep through runs and delete those past retention."""
    now = datetime.now(UTC)

    try:
        # Get all runs from database
        all_runs = await database.db.get_test_runs(limit=100000)

        for run in all_runs:
            run_id = run['run_id']
            retention_days = run.get('retention_days')
            start_time_str = run.get('start_time')

            should_delete = False
            reason = None

            # Check if run files should be deleted based on retention_days
            if retention_days and start_time_str:
                try:
                    start_time = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
                    # Make start_time timezone-aware if it's not
                    if start_time.tzinfo is None:
                        start_time = start_time.replace(tzinfo=UTC)
                    age_days = (now - start_time).days
                    if age_days > int(retention_days):
                        should_delete = True
                        reason = "expired_retention_days"
                except Exception as e:
                    logger.error(f"Error calculating age for run {run_id}: {e}")

            if should_delete:
                _log_event("run_files_deleted", run_id=run_id, reason=reason)

                # Delete from filesystem only (keep database records for historical analysis)
                run_path = get_run_path(run_id)
                if run_path.exists():
                    try:
                        shutil.rmtree(run_path)
                        logger.info(f"Deleted filesystem data for run {run_id} (keeping database metadata)")
                    except Exception as e:
                        logger.error(f"Error deleting filesystem data for run {run_id}: {e}")

    except Exception as e:
        logger.error(f"Error during cleanup_runs_sweep: {e}")


async def cleanup_old_runs():
    """Background task that periodically cleans up old runs."""
    while True:
        await cleanup_runs_sweep()
        await asyncio.sleep(3600)  # Run every hour


def _log_event(event: str, **fields):
    """Log an event with timestamp."""
    import json
    record = {"event": event, **fields, "ts": datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"}
    logger.info(json.dumps(record))
