"""
API handlers for TestRift server.

All /api/* endpoints for test results analysis and data access.
"""

import asyncio
import logging
import os

from aiohttp import web

from .config import (
    CONFIG,
    CONFIG_PATH_USED,
    get_config_fingerprint,
    get_config_hash,
)
from .utils import (
    get_run_path,
    get_case_log_path,
    get_case_stack_path,
    read_jsonl,
    validate_run_id,
    validate_group_hash_value,
    TC_ID_FIELD,
    TC_FULL_NAME_FIELD,
)
from . import database

logger = logging.getLogger(__name__)


# --- Test Results Analyzer API ---

async def api_test_runs_handler(request):
    """Get test runs with filtering capabilities."""
    try:
        # Parse query parameters
        limit = int(request.query.get('limit', 100))
        offset = int(request.query.get('offset', 0))
        status = request.query.get('status')

        # Parse metadata filters
        metadata_filters = {}
        for key, value in request.query.items():
            if key.startswith('metadata.'):
                metadata_key = key[9:]  # Remove 'metadata.' prefix
                metadata_filters[metadata_key] = value

        group_hash = request.query.get('group') or request.query.get('group_hash')
        if group_hash and not validate_group_hash_value(group_hash):
            return web.json_response({
                "success": False,
                "error": "Invalid group hash"
            }, status=400)

        # Get test runs from database
        runs = await database.db.get_test_runs(
            limit=limit,
            offset=offset,
            status_filter=status,
            metadata_filters=metadata_filters if metadata_filters else None,
            group_hash=group_hash
        )

        return web.json_response({
            "success": True,
            "data": runs,
            "pagination": {
                "limit": limit,
                "offset": offset,
                "count": len(runs)
            }
        })

    except Exception as e:
        logger.error(f"Error in api_test_runs_handler: {e}")
        return web.json_response({
            "success": False,
            "error": str(e)
        }, status=500)


async def api_test_run_details_handler(request):
    """Get detailed information about a specific test run."""
    try:
        run_id = request.match_info["run_id"]

        # Get test run details
        run = await database.db.get_test_run_by_id(run_id)
        if not run:
            return web.json_response({
                "success": False,
                "error": "Test run not found"
            }, status=404)

        # Get test cases for this run
        test_cases = await database.db.get_test_cases_for_run(run_id)

        # Get metadata for this run
        user_metadata = await database.db.get_user_metadata_for_run(run_id)
        group_metadata = await database.db.get_group_metadata_for_run(run_id)

        return web.json_response({
            "success": True,
            "data": {
                "run": run,
                "test_cases": test_cases,
                "user_metadata": user_metadata,
                "group": {
                    "name": run.get("group_name"),
                    "hash": run.get("group_hash"),
                    "metadata": group_metadata
                }
            }
        })

    except Exception as e:
        logger.error(f"Error in api_test_run_details_handler: {e}")
        return web.json_response({
            "success": False,
            "error": str(e)
        }, status=500)


async def api_test_results_for_runs_handler(request):
    """Get test results for multiple runs efficiently."""
    try:
        run_ids_param = request.query.get('run_ids', '')
        if not run_ids_param:
            return web.json_response({
                "success": False,
                "error": "run_ids parameter is required"
            }, status=400)

        # Parse run IDs (comma-separated)
        run_ids = [run_id.strip() for run_id in run_ids_param.split(',') if run_id.strip()]

        if not run_ids:
            return web.json_response({
                "success": False,
                "error": "No valid run IDs provided"
            }, status=400)

        # Get test results for all runs in one efficient query
        raw_test_results = await database.db.get_test_results_for_runs(run_ids)

        enriched_results = {}
        for run_id, cases in raw_test_results.items():
            enriched_cases = []
            for case in cases:
                case_copy = dict(case)
                # Get the full name and tc_id from the database
                full_name = case_copy.get('tc_full_name')
                tc_id = case_copy.get('tc_id')

                if full_name:
                    case_copy[TC_FULL_NAME_FIELD] = full_name
                if tc_id:
                    case_copy[TC_ID_FIELD] = tc_id
                else:
                    case_copy[TC_ID_FIELD] = ""

                enriched_cases.append(case_copy)

            enriched_results[run_id] = enriched_cases

        return web.json_response({
            "success": True,
            "data": enriched_results
        })

    except Exception as e:
        logger.error(f"Error in api_test_results_for_runs_handler: {e}")
        return web.json_response({
            "success": False,
            "error": f"Internal server error: {str(e)}"
        }, status=500)


async def api_test_results_over_time_handler(request):
    """Get test results aggregated over time for trending analysis."""
    try:
        days_back = int(request.query.get('days_back', 30))

        # Parse metadata filters
        metadata_filters = {}
        for key, value in request.query.items():
            if key.startswith('metadata.'):
                metadata_key = key[9:]  # Remove 'metadata.' prefix
                metadata_filters[metadata_key] = value

        group_hash = request.query.get('group') or request.query.get('group_hash')
        if group_hash and not validate_group_hash_value(group_hash):
            return web.json_response({
                "success": False,
                "error": "Invalid group hash"
            }, status=400)

        # Get test runs over time (individual runs, not aggregated by date)
        results = await database.db.get_test_runs_over_time(
            days_back=days_back,
            metadata_filters=metadata_filters if metadata_filters else None,
            group_hash=group_hash
        )

        # Log the results
        logger.info(f"API test-runs-over-time: {len(results)} test runs")
        for result in results[:3]:  # Show first 3 runs
            logger.info(f"  Run: {result.get('run_id')[:8]}..., Passed: {result.get('passed_tests')}, Failed: {result.get('failed_tests')}, Skipped: {result.get('skipped_tests')}")

        return web.json_response({
            "success": True,
            "data": results
        })

    except Exception as e:
        logger.error(f"Error in api_test_results_over_time_handler: {e}")
        return web.json_response({
            "success": False,
            "error": str(e)
        }, status=500)


async def api_test_case_history_handler(request):
    """Get execution history for a specific test case."""
    try:
        tc_full_name = request.query.get('tc_full_name')
        if not tc_full_name:
            return web.json_response({
                "success": False,
                "error": "tc_full_name parameter is required"
            }, status=400)

        limit = int(request.query.get('limit', 50))

        # Parse metadata filters
        metadata_filters = {}
        for key, value in request.query.items():
            if key.startswith('metadata.'):
                metadata_key = key[9:]  # Remove 'metadata.' prefix
                metadata_filters[metadata_key] = value

        group_hash = request.query.get('group') or request.query.get('group_hash')
        if group_hash and not validate_group_hash_value(group_hash):
            return web.json_response({
                "success": False,
                "error": "Invalid group hash"
            }, status=400)

        # Get test case history
        history = await database.db.get_test_case_history(
            tc_full_name=tc_full_name,
            limit=limit,
            metadata_filters=metadata_filters if metadata_filters else None,
            group_hash=group_hash
        )

        return web.json_response({
            "success": True,
            "data": history
        })

    except Exception as e:
        logger.error(f"Error in api_test_case_history_handler: {e}")
        return web.json_response({
            "success": False,
            "error": str(e)
        }, status=500)


async def api_test_case_history_with_links_handler(request):
    """Get test case history with log file existence check."""
    try:
        tc_full_name = request.query.get('tc_full_name')
        if not tc_full_name:
            return web.json_response({
                "success": False,
                "error": "tc_full_name is required"
            }, status=400)

        limit = int(request.query.get('limit', 10))
        current_run_id = request.query.get('current_run_id')  # Exclude current run

        group_hash = request.query.get('group')
        if group_hash and not validate_group_hash_value(group_hash):
            return web.json_response({
                "success": False,
                "error": "Invalid group hash"
            }, status=400)

        # Get test case history
        history = await database.db.get_test_case_history(
            tc_full_name=tc_full_name,
            limit=limit + 1,  # Get one extra to account for current run exclusion
            group_hash=group_hash
        )

        # Filter out current run and check log existence
        result = []
        for item in history:
            run_id = item.get('run_id')
            if current_run_id and run_id == current_run_id:
                continue

            # Check if run directory exists (logs may be merged after run finishes)
            tc_id = item.get('tc_id')
            item['has_log'] = tc_id and get_run_path(run_id).exists()

            result.append(item)

            if len(result) >= limit:
                break

        return web.json_response({
            "success": True,
            "data": result
        })

    except Exception as e:
        logger.error(f"Error in api_test_case_history_with_links_handler: {e}")
        import traceback
        traceback.print_exc()
        return web.json_response({
            "success": False,
            "error": str(e)
        }, status=500)


async def api_metadata_keys_handler(request):
    """Get all available metadata keys."""
    try:
        keys = await database.db.get_all_metadata_keys()
        return web.json_response({
            "success": True,
            "data": keys
        })

    except Exception as e:
        logger.error(f"Error in api_metadata_keys_handler: {e}")
        return web.json_response({
            "success": False,
            "error": str(e)
        }, status=500)


async def api_metadata_values_handler(request):
    """Get unique values for a specific metadata key."""
    try:
        key = request.query.get('key')
        if not key:
            return web.json_response({
                "success": False,
                "error": "key parameter is required"
            }, status=400)

        values = await database.db.get_unique_metadata_values(key)
        return web.json_response({
            "success": True,
            "data": values
        })

    except Exception as e:
        logger.error(f"Error in api_metadata_values_handler: {e}")
        return web.json_response({
            "success": False,
            "error": str(e)
        }, status=500)


async def api_group_details_handler(request):
    """Return metadata for a specific group hash."""
    group_hash = request.match_info.get("group_hash")
    if not validate_group_hash_value(group_hash):
        return web.json_response({
            "success": False,
            "error": "Invalid group hash"
        }, status=400)

    runs = await database.db.get_test_runs(limit=1, group_hash=group_hash)
    if not runs:
        return web.json_response({
            "success": False,
            "error": "Group not found"
        }, status=404)

    run = runs[0]
    metadata = await database.db.get_group_metadata_for_run(run["run_id"])

    return web.json_response({
        "success": True,
        "data": {
            "hash": group_hash,
            "name": run.get("group_name"),
            "metadata": metadata
        }
    })


async def api_failures_toplist_handler(request):
    """Get top failing test cases or symptoms."""
    try:
        mode = request.query.get('mode', 'by_test_case')
        days_back = int(request.query.get('days', 30))
        top_n = int(request.query.get('top', 20))

        # Parse metadata filters
        metadata_filters = {}
        for key, value in request.query.items():
            if key.startswith('metadata.'):
                metadata_key = key[9:]  # Remove 'metadata.' prefix
                metadata_filters[metadata_key] = value

        group_hash = request.query.get('group')
        if group_hash and not validate_group_hash_value(group_hash):
            return web.json_response({
                "success": False,
                "error": "Invalid group hash"
            }, status=400)

        if mode == 'by_symptom':
            # Get failed test cases and analyze by stack trace
            failed_cases = await database.db.get_failed_test_cases(
                days_back=days_back,
                limit=1000,  # Get more to analyze symptoms
                group_hash=group_hash,
                metadata_filters=metadata_filters if metadata_filters else None
            )

            # Group by first line of stack trace (symptom)
            symptom_map = {}
            for case in failed_cases:
                # Load stack trace from file
                tc_id = case.get('tc_id')
                try:
                    if tc_id:
                        stack_path = get_case_stack_path(case['run_id'], tc_id=tc_id)
                    else:
                        stack_path = get_case_stack_path(case['run_id'], case['tc_full_name'])
                except Exception:
                    stack_path = None
                symptom = None
                stack_trace_sample = None

                if stack_path and stack_path.exists():
                    try:
                        traces = read_jsonl(stack_path)
                        if traces and len(traces) > 0:
                            first_trace = traces[0]
                            stack_lines = first_trace.get('stack_trace', [])
                            if stack_lines and len(stack_lines) > 0:
                                # Use first line of stack trace as symptom
                                symptom = stack_lines[0].strip() if isinstance(stack_lines[0], str) else str(stack_lines[0])
                                # Store full trace for sample
                                stack_trace_sample = '\n'.join(stack_lines[:10])  # First 10 lines
                    except Exception as e:
                        logger.error(f"Error reading stack trace: {e}")

                if not symptom:
                    symptom = "No stack trace available"

                if symptom not in symptom_map:
                    symptom_map[symptom] = {
                        'symptom': symptom,
                        'failure_count': 0,
                        'affected_test_cases': {},  # Dict: tc_full_name -> {run_id, time}
                        'last_failure': None,
                        'last_failure_run_id': None,
                        'last_failure_test_case': None,
                        'stack_trace_sample': stack_trace_sample
                    }

                symptom_map[symptom]['failure_count'] += 1

                # Track last failure and count for each test case
                tc_full_name = case['tc_full_name']
                tc_id = case.get('tc_id', '')
                case_time = case.get('start_time')
                if tc_full_name not in symptom_map[symptom]['affected_test_cases']:
                    symptom_map[symptom]['affected_test_cases'][tc_full_name] = {
                        'run_id': case['run_id'],
                        'tc_id': tc_id,
                        'time': case_time,
                        'count': 1
                    }
                else:
                    symptom_map[symptom]['affected_test_cases'][tc_full_name]['count'] += 1
                    if case_time and case_time > (symptom_map[symptom]['affected_test_cases'][tc_full_name].get('time') or ''):
                        symptom_map[symptom]['affected_test_cases'][tc_full_name]['run_id'] = case['run_id']
                        symptom_map[symptom]['affected_test_cases'][tc_full_name]['tc_id'] = tc_id
                        symptom_map[symptom]['affected_test_cases'][tc_full_name]['time'] = case_time

                # Track overall last failure for the symptom
                if case_time:
                    current_last = symptom_map[symptom]['last_failure']
                    if not current_last or case_time > current_last:
                        symptom_map[symptom]['last_failure'] = case_time
                        symptom_map[symptom]['last_failure_run_id'] = case['run_id']
                        symptom_map[symptom]['last_failure_test_case'] = case['tc_full_name']
                        symptom_map[symptom]['last_failure_tc_id'] = tc_id
                        if stack_trace_sample:
                            symptom_map[symptom]['stack_trace_sample'] = stack_trace_sample

            # Convert to list and sort
            results = list(symptom_map.values())
            for r in results:
                # Convert affected_test_cases dict to list of objects with tc_id and count
                affected_list = []
                for tc_full_name, info in r['affected_test_cases'].items():
                    run_id = info['run_id']
                    tc_id = info.get('tc_id', '')
                    count = info.get('count', 1)
                    # Check if run directory exists (logs may be merged after run finishes)
                    has_log = tc_id and get_run_path(run_id).exists()
                    affected_list.append({
                        TC_ID_FIELD: tc_id,
                        TC_FULL_NAME_FIELD: tc_full_name,
                        'last_failure_run_id': run_id if has_log else None,
                        'failure_count': count
                    })
                # Sort by failure count descending
                affected_list.sort(key=lambda x: x['failure_count'], reverse=True)
                r['affected_test_cases'] = affected_list

                # Also check if the overall last failure log exists
                if r['last_failure_run_id'] and r['last_failure_test_case']:
                    last_tc_id = r.get('last_failure_tc_id', '')
                    # Check if run directory exists (logs may be merged after run finishes)
                    has_last_log = last_tc_id and get_run_path(r['last_failure_run_id']).exists()
                    if has_last_log:
                        r['last_failure_test_case'] = {
                            TC_ID_FIELD: last_tc_id,
                            TC_FULL_NAME_FIELD: r['last_failure_test_case']
                        }
                    else:
                        r['last_failure_run_id'] = None
                        r['last_failure_test_case'] = None

            results.sort(key=lambda x: x['failure_count'], reverse=True)
            results = results[:top_n]

            return web.json_response({
                "success": True,
                "data": results
            })
        else:
            # By test case name
            results = await database.db.get_failure_counts_by_test_case(
                days_back=days_back,
                top_n=top_n,
                group_hash=group_hash,
                metadata_filters=metadata_filters if metadata_filters else None
            )

            # Check if log files exist for each result while enriching identifiers
            for r in results:
                full_name = r.get('tc_full_name')
                tc_id = r.get('last_failure_tc_id', '')
                if full_name:
                    r[TC_FULL_NAME_FIELD] = full_name
                if tc_id:
                    r[TC_ID_FIELD] = tc_id
                else:
                    r[TC_ID_FIELD] = ""

                if r.get('last_failure_run_id') and tc_id:
                    # Check if run directory exists (logs may be merged after run finishes)
                    if not get_run_path(r['last_failure_run_id']).exists():
                        r['last_failure_run_id'] = None

            return web.json_response({
                "success": True,
                "data": results
            })

    except Exception as e:
        logger.error(f"Error in api_failures_toplist_handler: {e}")
        import traceback
        traceback.print_exc()
        return web.json_response({
            "success": False,
            "error": str(e)
        }, status=500)


async def api_classifications_for_run_handler(request):
    """Get test case classifications for all TCs in a run."""
    try:
        run_id = request.match_info.get('run_id')
        if not run_id:
            return web.json_response({
                "success": False,
                "error": "run_id is required"
            }, status=400)

        if not validate_run_id(run_id):
            return web.json_response({
                "success": False,
                "error": "Invalid run_id"
            }, status=400)

        # Get run details to find group_hash
        run_data = await database.db.get_test_run_by_id(run_id)
        if not run_data:
            return web.json_response({
                "success": False,
                "error": "Run not found"
            }, status=404)

        group_hash = run_data.get('group_hash')

        # Get classifications for all test cases in the run
        classifications = await database.db.get_classifications_for_run(run_id, group_hash)

        # Add has_log info to history items
        for tc_id, class_data in classifications.items():
            if 'history' in class_data:
                for hist_item in class_data['history']:
                    hist_run_id = hist_item.get('run_id')
                    # Check if run directory exists (logs may be merged after run finishes)
                    hist_item['has_log'] = bool(hist_run_id) and get_run_path(hist_run_id).exists()

        return web.json_response({
            "success": True,
            "data": classifications
        })

    except Exception as e:
        logger.error(f"Error in api_classifications_for_run_handler: {e}")
        import traceback
        traceback.print_exc()
        return web.json_response({
            "success": False,
            "error": str(e)
        }, status=500)


async def api_tc_hover_history_handler(request):
    """Get test case history for hover tooltip."""
    try:
        tc_full_name = request.query.get('tc_full_name')
        if not tc_full_name:
            return web.json_response({
                "success": False,
                "error": "tc_full_name is required"
            }, status=400)

        group_hash = request.query.get('group')
        if group_hash and not validate_group_hash_value(group_hash):
            return web.json_response({
                "success": False,
                "error": "Invalid group hash"
            }, status=400)

        current_run_id = request.query.get('current_run_id')

        # Get current run's start time if we have a run_id
        current_run_start_time = None
        if current_run_id:
            async with database.db.get_connection() as db:
                cursor = await db.execute(
                    "SELECT start_time FROM test_runs WHERE run_id = ?",
                    (current_run_id,)
                )
                row = await cursor.fetchone()
                if row:
                    current_run_start_time = row[0]

        # Get previous results (before current run)
        previous_history = await database.db.get_test_case_classification_data(
            tc_full_name=tc_full_name,
            group_hash=group_hash,
            limit=10,
            current_run_id=current_run_id,
            current_run_start_time=current_run_start_time
        )

        # Get latest results (all runs, including current and future)
        latest_history = await database.db.get_test_case_classification_data(
            tc_full_name=tc_full_name,
            group_hash=group_hash,
            limit=10
        )

        # Helper function to add has_log and format
        def format_history(history_items):
            result = []
            for item in history_items:
                run_id = item.get('run_id')
                tc_id = item.get('tc_id')
                # Check if run directory exists (logs may be merged after run finishes)
                has_log = tc_id and get_run_path(run_id).exists()
                result.append({
                    'status': item['status'],
                    'run_id': run_id,
                    'tc_id': tc_id,
                    'run_name': item.get('run_name'),
                    'run_start_time': item.get('run_start_time'),
                    'has_log': has_log
                })
            return result

        return web.json_response({
            "success": True,
            "data": {
                "previous": format_history(previous_history),
                "latest": format_history(latest_history)
            }
        })

    except Exception as e:
        logger.error(f"Error in api_tc_hover_history_handler: {e}")
        import traceback
        traceback.print_exc()
        return web.json_response({
            "success": False,
            "error": str(e)
        }, status=500)


async def api_run_hover_history_handler(request):
    """Get test run history for hover tooltip within a group."""
    try:
        group_hash = request.match_info.get('group_hash')
        if not group_hash:
            return web.json_response({
                "success": False,
                "error": "group_hash is required"
            }, status=400)

        if not validate_group_hash_value(group_hash):
            return web.json_response({
                "success": False,
                "error": "Invalid group hash"
            }, status=400)

        current_run_id = request.query.get('current_run_id')
        current_run_start_time = None
        if current_run_id:
            async with database.db.get_connection() as db:
                cursor = await db.execute(
                    "SELECT start_time FROM test_runs WHERE run_id = ?",
                    (current_run_id,)
                )
                row = await cursor.fetchone()
                if row:
                    current_run_start_time = row[0]

        # Previous runs: before the current run, exclude current
        previous_history = await database.db.get_test_run_history_in_group(
            group_hash=group_hash,
            limit=10,
            exclude_run_id=current_run_id,
            current_run_start_time=current_run_start_time
        )

        # Latest runs: recent runs excluding current
        latest_history = await database.db.get_test_run_history_in_group(
            group_hash=group_hash,
            limit=10,
            exclude_run_id=current_run_id
        )

        return web.json_response({
            "success": True,
            "data": {
                "previous": previous_history,
                "latest": latest_history
            }
        })

    except Exception as e:
        logger.error(f"Error in api_run_hover_history_handler: {e}")
        import traceback
        traceback.print_exc()
        return web.json_response({
            "success": False,
            "error": str(e)
        }, status=500)


async def api_migrate_data_handler(request):
    """Trigger migration of existing test data from disk to database."""
    try:
        return web.json_response({
            "success": False,
            "error": "Migration module not available in this build."
        }, status=501)

    except Exception as e:
        logger.error(f"Error in api_migrate_data_handler: {e}")
        return web.json_response({
            "success": False,
            "error": str(e)
        }, status=500)


async def api_server_info_handler(request):
    """Returns server identity and config fingerprint for startup checks."""
    try:
        from importlib.metadata import version as _pkg_version
        ver = _pkg_version("testrift-server")
    except Exception:
        ver = "unknown"

    return web.json_response({
        "service": "testrift-server",
        "version": ver,
        "config_path": str(CONFIG_PATH_USED) if CONFIG_PATH_USED else None,
        "config": get_config_fingerprint(CONFIG),
        "config_hash": get_config_hash(CONFIG),
    })


async def api_admin_shutdown_handler(request):
    """Shutdown endpoint used for local auto-restart flows."""
    remote = request.remote or ""
    if remote not in ("127.0.0.1", "::1", "localhost"):
        return web.json_response({"success": False, "error": "forbidden"}, status=403)

    expected = get_config_hash(CONFIG)
    provided = request.headers.get("X-TestRift-Config-Hash")
    if not provided:
        try:
            body = await request.json()
            provided = body.get("config_hash")
        except Exception:
            provided = None

    if provided != expected:
        return web.json_response({"success": False, "error": "config_hash mismatch"}, status=403)

    # Respond first, then hard-exit quickly to ensure the port is released
    loop = asyncio.get_running_loop()
    loop.call_later(0.2, lambda: os._exit(0))
    return web.json_response({"success": True})


# --- Route Registration ---

def get_routes():
    """Return list of routes for API handlers."""
    return [
        web.get("/api/test-runs", api_test_runs_handler),
        web.get("/api/test-runs/{run_id}", api_test_run_details_handler),
        web.get("/api/test-results/for-runs", api_test_results_for_runs_handler),
        web.get("/api/test-results/over-time", api_test_results_over_time_handler),
        web.get("/api/test-case/history", api_test_case_history_handler),
        web.get("/api/test-case/history-with-links", api_test_case_history_with_links_handler),
        web.get("/api/metadata/keys", api_metadata_keys_handler),
        web.get("/api/metadata/values", api_metadata_values_handler),
        web.get("/api/groups/{group_hash}", api_group_details_handler),
        web.get("/api/failures/toplist", api_failures_toplist_handler),
        web.get("/api/classifications/{run_id}", api_classifications_for_run_handler),
        web.get("/api/tc-hover-history", api_tc_hover_history_handler),
        web.get("/api/run-hover-history/{group_hash}", api_run_hover_history_handler),
        web.post("/api/migrate-data", api_migrate_data_handler),
        web.get("/api/server-info", api_server_info_handler),
        web.post("/api/admin/shutdown", api_admin_shutdown_handler),
    ]
