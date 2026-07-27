"""
API handlers for TestRift server.

All /api/* endpoints for test results analysis and data access.
"""

import asyncio
import logging
import os
import re
from datetime import datetime, timezone

from aiohttp import web

from .config import (
    CONFIG,
    CONFIG_PATH_USED,
    get_config_fingerprint,
    get_config_hash,
)
from .models import TestRunData
from .utils import (
    get_run_path,
    get_case_log_path,
    get_case_stack_path,
    find_test_case_by_tc_id,
    read_jsonl,
    read_mplog,
    validate_run_id,
    validate_group_hash_value,
    TC_ID_FIELD,
    TC_FULL_NAME_FIELD,
)
from . import database
from .summary_profiles import select_profile_from_database, resolve_run_set
from .ai_analysis import create_collection_report

logger = logging.getLogger(__name__)

TARGET_KEY_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
PURPOSES = {"nightly", "release", "feature", "manual", "sanity", "rerun"}


async def _json_body(request):
    body = await request.json()
    if not isinstance(body, dict):
        raise ValueError("JSON body must be an object")
    return body


def _validate_key(value, field="key"):
    if not isinstance(value, str) or not TARGET_KEY_PATTERN.fullmatch(value):
        raise ValueError(f"{field} must contain lowercase letters, digits, and hyphens")
    return value


def _validate_display_name(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("display_name cannot be blank")
    return value.strip()


def _validation_error(error):
    return web.json_response({"success": False, "error": str(error)}, status=400)


async def _context_run_ids(request):
    """Return shared Run-set IDs when a Target or Collection context is requested."""
    target_key = request.query.get("target")
    collection_key = request.query.get("collection")
    if not target_key and not collection_key:
        return None
    requested_at = request.query.get("at")
    run_set = await resolve_run_set(
        database.db,
        target_key=target_key,
        collection_key=collection_key,
        profile_id=int(request.query["profile_id"]) if request.query.get("profile_id") else None,
        requested_at=datetime.fromisoformat(requested_at.replace("Z", "+00:00")) if requested_at else None,
    )
    return run_set["run_ids"]


async def api_targets_handler(request):
    if request.method == "GET":
        targets = await database.db.list_targets(request.query.get("needs_setup") == "true")
        return web.json_response({"success": True, "data": targets})
    try:
        body = await _json_body(request)
        key = _validate_key(body.get("key"), "key")
        target = await database.db.get_or_create_target(key, _validate_display_name(body.get("display_name", key)))
        return web.json_response({"success": True, "data": target}, status=201)
    except (ValueError, TypeError) as error:
        return _validation_error(error)


async def api_target_handler(request):
    key = request.match_info["key"]
    if request.method == "GET":
        target = await database.db.get_target(key)
        return web.json_response({"success": True, "data": target}) if target else web.json_response({"success": False, "error": "Target not found"}, status=404)
    if request.method == "DELETE":
        if request.query.get("cascade") != "true":
            return _validation_error("Target deletion requires cascade=true")
        deleted = await database.db.delete_target(key)
        return web.json_response({"success": True}) if deleted else web.json_response({"success": False, "error": "Target not found"}, status=404)
    try:
        body = await _json_body(request)
        state = body.get("setup_state")
        if state not in {"needs_setup", "ready"}:
            raise ValueError("setup_state must be needs_setup or ready")
        target = await database.db.update_target(key, _validate_display_name(body.get("display_name")), state)
        return web.json_response({"success": True, "data": target}) if target else web.json_response({"success": False, "error": "Target not found"}, status=404)
    except (ValueError, TypeError) as error:
        return _validation_error(error)


async def api_target_complete_setup_handler(request):
    try:
        body = await _json_body(request)
        collection_ids = body.get("collection_ids", [])
        if not isinstance(collection_ids, list) or len(collection_ids) != len(set(collection_ids)):
            raise ValueError("collection_ids must be a unique list")
        target = await database.db.complete_target_setup(
            request.match_info["key"], _validate_display_name(body.get("display_name")), collection_ids
        )
        return web.json_response({"success": True, "data": target}) if target else web.json_response({"success": False, "error": "Target not found"}, status=404)
    except (ValueError, TypeError) as error:
        return _validation_error(error)


async def api_collections_handler(request):
    if request.method == "GET":
        return web.json_response({"success": True, "data": await database.db.list_collections()})
    try:
        body = await _json_body(request)
        collection_id = await database.db.create_collection(
            _validate_key(body.get("key"), "key"), _validate_display_name(body.get("display_name")), body.get("description"),
            bool(body.get("ai_summary_enabled", False)), bool(body.get("email_enabled", False)), body.get("recipients", []),
        )
        return web.json_response({"success": True, "data": {"id": collection_id}}, status=201)
    except (ValueError, TypeError) as error:
        return _validation_error(error)
    except Exception as error:
        return web.json_response({"success": False, "error": str(error)}, status=409)


async def api_collection_handler(request):
    key = request.match_info["key"]
    if request.method == "GET":
        collection = await database.db.get_collection(key)
        return web.json_response({"success": True, "data": collection}) if collection else web.json_response({"success": False, "error": "Collection not found"}, status=404)
    if request.method == "DELETE":
        if request.query.get("cascade") != "true":
            return _validation_error("Collection deletion requires cascade=true")
        deleted = await database.db.delete_collection(key)
        return web.json_response({"success": True}) if deleted else web.json_response({"success": False, "error": "Collection not found"}, status=404)
    try:
        body = await _json_body(request)
        collection = await database.db.update_collection(key, {**body, "display_name": _validate_display_name(body.get("display_name"))})
        return web.json_response({"success": True, "data": collection}) if collection else web.json_response({"success": False, "error": "Collection not found"}, status=404)
    except (ValueError, TypeError) as error:
        return _validation_error(error)


async def api_collection_members_handler(request):
    collection = await database.db.get_collection(request.match_info["key"])
    if not collection:
        return web.json_response({"success": False, "error": "Collection not found"}, status=404)
    try:
        target_ids = (await _json_body(request)).get("target_ids")
        if not isinstance(target_ids, list) or len(target_ids) != len(set(target_ids)):
            raise ValueError("target_ids must be a unique list")
        known_targets = {target["id"] for target in await database.db.list_targets()}
        if not set(target_ids).issubset(known_targets):
            raise ValueError("target_ids contains an unknown Target")
        await database.db.replace_collection_membership(collection["id"], target_ids)
        return web.json_response({"success": True})
    except (ValueError, TypeError) as error:
        return _validation_error(error)


def _profile_values(body):
    name = _validate_display_name(body.get("name"))
    purpose = body.get("purpose")
    window_hours = body.get("window_hours")
    if purpose not in PURPOSES:
        raise ValueError("purpose is unsupported")
    if not isinstance(window_hours, int) or window_hours <= 0:
        raise ValueError("window_hours must be a positive integer")
    selectors = body.get("selectors", [])
    if not isinstance(selectors, list) or any(
        not isinstance(selector, dict) or not isinstance(selector.get("source_role"), str) or not selector["source_role"].strip()
        or not isinstance(selector.get("branch"), str) or not selector["branch"].strip()
        for selector in selectors
    ):
        raise ValueError("selectors must contain nonblank source_role and exact branch")
    return name, purpose, window_hours, selectors


async def api_collection_profiles_handler(request):
    collection = await database.db.get_collection(request.match_info["key"])
    if not collection:
        return web.json_response({"success": False, "error": "Collection not found"}, status=404)
    try:
        body = await _json_body(request)
        name, purpose, window_hours, selectors = _profile_values(body)
        profile_id = await database.db.create_summary_profile(collection["id"], name, purpose, window_hours, bool(body.get("is_primary", False)))
        await database.db.replace_summary_profile_sources(profile_id, [(item["source_role"], item["branch"], item.get("target_id")) for item in selectors])
        return web.json_response({"success": True, "data": await database.db.get_summary_profile(profile_id)}, status=201)
    except (ValueError, TypeError) as error:
        return _validation_error(error)
    except Exception as error:
        return web.json_response({"success": False, "error": str(error)}, status=409)


async def api_profile_handler(request):
    profile_id = int(request.match_info["profile_id"])
    if request.method == "GET":
        profile = await database.db.get_summary_profile(profile_id)
        return web.json_response({"success": True, "data": profile}) if profile else web.json_response({"success": False, "error": "Profile not found"}, status=404)
    if request.method == "DELETE":
        if request.query.get("cascade") != "true":
            return _validation_error("Profile deletion requires cascade=true")
        deleted = await database.db.delete_summary_profile(profile_id)
        return web.json_response({"success": True}) if deleted else web.json_response({"success": False, "error": "Profile not found"}, status=404)
    try:
        body = await _json_body(request)
        name, purpose, window_hours, selectors = _profile_values(body)
        updated = await database.db.update_summary_profile(profile_id, {**body, "name": name, "purpose": purpose, "window_hours": window_hours})
        if not updated:
            return web.json_response({"success": False, "error": "Profile not found"}, status=404)
        await database.db.replace_summary_profile_sources(profile_id, [(item["source_role"], item["branch"], item.get("target_id")) for item in selectors])
        return web.json_response({"success": True, "data": await database.db.get_summary_profile(profile_id)})
    except (ValueError, TypeError) as error:
        return _validation_error(error)


async def api_collection_summary_handler(request):
    collection = await database.db.get_collection(request.match_info["key"])
    if not collection:
        return web.json_response({"success": False, "error": "Collection not found"}, status=404)
    profile_id = request.query.get("profile_id") or next((profile["id"] for profile in collection["profiles"] if profile["is_primary"]), None)
    if not profile_id:
        return _validation_error("A profile_id or primary profile is required")
    try:
        requested_at = datetime.fromisoformat(request.query.get("at", "").replace("Z", "+00:00"))
        selections = await select_profile_from_database(database.db, int(profile_id), requested_at.astimezone(timezone.utc))
        return web.json_response({"success": True, "data": [selection.__dict__ for selection in selections]})
    except (ValueError, TypeError) as error:
        return _validation_error(error)


async def api_collection_report_handler(request):
    collection = await database.db.get_collection(request.match_info["key"])
    if not collection:
        return web.json_response({"success": False, "error": "Collection not found"}, status=404)
    try:
        body = await _json_body(request)
        profile_id = int(body["profile_id"])
        if profile_id not in {profile["id"] for profile in collection["profiles"]}:
            raise ValueError("Profile does not belong to Collection")
        requested_at = datetime.fromisoformat(body["requested_at"].replace("Z", "+00:00"))
        report = await create_collection_report(database.db, profile_id, requested_at)
        return web.json_response({"success": True, "data": report}, status=201)
    except (KeyError, ValueError, TypeError) as error:
        return _validation_error(error)


async def api_run_set_handler(request):
    """Resolve a reproducible Target or Collection/profile Run set for views."""
    try:
        requested_at = request.query.get("at")
        run_set = await resolve_run_set(
            database.db,
            target_key=request.query.get("target"),
            collection_key=request.query.get("collection"),
            profile_id=int(request.query["profile_id"]) if request.query.get("profile_id") else None,
            requested_at=datetime.fromisoformat(requested_at.replace("Z", "+00:00")) if requested_at else None,
        )
        return web.json_response({"success": True, "data": run_set})
    except (ValueError, TypeError) as error:
        return _validation_error(error)


# --- Test Results Analyzer API ---

async def api_test_runs_handler(request):
    """Get test runs with filtering capabilities."""
    try:
        # Parse query parameters
        limit = int(request.query.get('limit', 100))
        offset = int(request.query.get('offset', 0))
        status = request.query.get('status')
        target_key = request.query.get('target')
        purpose = request.query.get('purpose')
        source_role = request.query.get('source_role')
        source_branch = request.query.get('source_branch')
        revision = request.query.get('revision')
        start_at = request.query.get('start_at')
        end_at = request.query.get('end_at')
        collection_key = request.query.get('collection')

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
            group_hash=group_hash,
            target_key=target_key,
            purpose=purpose,
            source_role=source_role,
            source_branch=source_branch,
            revision=revision,
            start_at=start_at,
            end_at=end_at,
            collection_key=collection_key,
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

        run_ids = await _context_run_ids(request)
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
            group_hash=group_hash,
            run_ids=run_ids,
        )

        # Log the results
        logger.debug(f"API test-runs-over-time: {len(results)} test runs")
        for result in results[:3]:  # Show first 3 runs
            logger.debug(f"  Run: {result.get('run_id')[:8]}..., Passed: {result.get('passed_tests')}, Failed: {result.get('failed_tests')}, Skipped: {result.get('skipped_tests')}")

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

        run_ids = await _context_run_ids(request)
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
                metadata_filters=metadata_filters if metadata_filters else None,
                run_ids=run_ids,
            )

            # Cache loaded runs to avoid re-loading the same run multiple times
            run_cache = {}

            def _load_stack_traces_for_case(case):
                """Load stack traces for a test case, handling both live and finished runs."""
                run_id = case['run_id']
                tc_id = case.get('tc_id')
                if not tc_id:
                    return []

                # First try individual stack file (works for in-progress/aborted runs)
                try:
                    stack_path = get_case_stack_path(run_id, tc_id=tc_id)
                    if stack_path.exists():
                        return read_mplog(stack_path)
                except Exception:
                    pass

                # For finished runs, load via run model (reads from merged file)
                if run_id not in run_cache:
                    try:
                        run_cache[run_id] = TestRunData.load_from_disk(run_id)
                    except Exception:
                        run_cache[run_id] = None

                run = run_cache[run_id]
                if run is None:
                    return []

                tc = find_test_case_by_tc_id(run, tc_id)
                if tc is None:
                    return []

                if not tc.stack_traces and tc.log_offset is not None:
                    tc.load_log_from_disk()

                return tc.stack_traces or []

            # Group by first line of stack trace (symptom)
            symptom_map = {}
            for case in failed_cases:
                traces = _load_stack_traces_for_case(case)
                symptom = None
                stack_trace_sample = None

                if traces:
                    first_trace = traces[0]
                    stack_lines = first_trace.get('stack_trace', [])
                    if stack_lines and len(stack_lines) > 0:
                        # Use first line of stack trace as symptom
                        symptom = stack_lines[0].strip() if isinstance(stack_lines[0], str) else str(stack_lines[0])
                        # Store full trace for sample
                        stack_trace_sample = '\n'.join(stack_lines[:10])  # First 10 lines

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
                metadata_filters=metadata_filters if metadata_filters else None,
                run_ids=run_ids,
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


# --- Commit/Diff Endpoints for testrift-collector ---

async def api_group_last_commits_handler(request):
    """Get last commit SHAs for all repos from the most recent run in a group."""
    try:
        group_hash = request.match_info["group_hash"]

        if not validate_group_hash_value(group_hash):
            return web.json_response({
                "success": False,
                "error": "Invalid group hash"
            }, status=400)

        commits = await database.db.get_last_commits_for_group(group_hash)

        return web.json_response({
            "success": True,
            "commits": commits
        })

    except Exception as e:
        logger.error(f"Error in api_group_last_commits_handler: {e}")
        return web.json_response({
            "success": False,
            "error": str(e)
        }, status=500)


async def api_run_commit_baselines_handler(request):
    try:
        baselines = await database.db.get_commit_baselines_for_run(request.match_info["run_id"])
        return web.json_response({"success": True, "baselines": baselines})
    except ValueError as error:
        return web.json_response({"success": False, "error": str(error)}, status=404)


async def api_run_commits_upload_handler(request):
    """Upload commit diffs for a run."""
    try:
        run_id = request.match_info["run_id"]

        if not validate_run_id(run_id):
            return web.json_response({
                "success": False,
                "error": "Invalid run ID"
            }, status=400)

        # Parse JSON body
        try:
            body = await request.json()
        except Exception:
            return web.json_response({
                "success": False,
                "error": "Invalid JSON body"
            }, status=400)

        diffs = body.get("diffs", [])
        if not isinstance(diffs, list):
            return web.json_response({
                "success": False,
                "error": "'diffs' must be an array"
            }, status=400)

        expected_sources = await database.db.get_run_sources(run_id)
        for diff in diffs:
            if diff.get("name") not in expected_sources or diff.get("current_sha") != expected_sources[diff["name"]]:
                return web.json_response({"success": False, "error": "Diff SHA does not match prepared Run source"}, status=400)

        # Store commit SHAs in database for future queries
        commits_to_store = []
        for diff in diffs:
            if diff.get("name") and diff.get("current_sha"):
                commits_to_store.append({
                    "repo_name": diff["name"],
                    "commit_sha": diff["current_sha"],
                    "repo_url": diff.get("url"),
                })

        if commits_to_store:
            await database.db.insert_run_commits(run_id, commits_to_store)

        # Store full diff data as JSON file in run directory
        run_path = get_run_path(run_id)
        run_path.mkdir(parents=True, exist_ok=True)

        diffs_file = run_path / "commits.json"
        import json
        import aiofiles
        async with aiofiles.open(diffs_file, "w", encoding="utf-8") as f:
            await f.write(json.dumps({"diffs": diffs}, indent=2))

        return web.json_response({
            "success": True,
            "stored_commits": len(commits_to_store),
            "stored_diffs": len(diffs)
        })

    except Exception as e:
        logger.error(f"Error in api_run_commits_upload_handler: {e}")
        return web.json_response({
            "success": False,
            "error": str(e)
        }, status=500)


async def api_run_commits_get_handler(request):
    """Get commit diffs for a run."""
    try:
        run_id = request.match_info["run_id"]

        if not validate_run_id(run_id):
            return web.json_response({
                "success": False,
                "error": "Invalid run ID"
            }, status=400)

        # Try to read from commits.json file
        run_path = get_run_path(run_id)
        diffs_file = run_path / "commits.json"

        if diffs_file.exists():
            import aiofiles
            async with aiofiles.open(diffs_file, "r", encoding="utf-8") as f:
                content = await f.read()
            import json
            data = json.loads(content)
            return web.json_response({
                "success": True,
                "diffs": data.get("diffs", [])
            })

        # Fall back to database records (just the SHA, no full diffs)
        commits = await database.db.get_commits_for_run(run_id)
        return web.json_response({
            "success": True,
            "commits": commits,
            "diffs": []
        })

    except Exception as e:
        logger.error(f"Error in api_run_commits_get_handler: {e}")
        return web.json_response({
            "success": False,
            "error": str(e)
        }, status=500)


# --- AI Analysis API ---

async def api_trigger_analysis_handler(request):
    """Trigger AI failure analysis for a run. POST /api/runs/{run_id}/analyze"""
    try:
        run_id = request.match_info["run_id"]
        if not validate_run_id(run_id):
            return web.json_response({"success": False, "error": "Invalid run ID"}, status=400)

        from .ai_analysis import get_analysis_status, run_failure_analysis
        from .config import AI_ANALYSIS_CONFIG

        status = get_analysis_status(run_id)
        if status.status == "running":
            return web.json_response({"success": True, "status": "already_running"})
        if status.status == "completed":
            return web.json_response({"success": True, "status": "already_completed"})

        if not AI_ANALYSIS_CONFIG.get("enabled", False) and not AI_ANALYSIS_CONFIG.get("openai_api_key"):
            return web.json_response({"success": False, "error": "AI analysis not configured"}, status=400)

        ws_server = request.app.get("ws_server")
        broadcast_fn = ws_server.broadcast_ui if ws_server else None
        asyncio.create_task(run_failure_analysis(run_id, broadcast_fn=broadcast_fn))

        return web.json_response({"success": True, "status": "started"}, status=202)

    except Exception as e:
        logger.error(f"Error in api_trigger_analysis_handler: {e}")
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def api_analysis_status_handler(request):
    """Get analysis status for a run. GET /api/runs/{run_id}/analysis"""
    try:
        run_id = request.match_info["run_id"]
        if not validate_run_id(run_id):
            return web.json_response({"success": False, "error": "Invalid run ID"}, status=400)

        from .ai_analysis import get_analysis_status
        status = get_analysis_status(run_id)

        return web.json_response({
            "success": True,
            "status": status.status,
            "analyzed_count": status.analyzed_count,
            "deduped_count": status.deduped_count,
            "skipped_count": status.skipped_count,
            "total_failures": status.total_failures,
            "error": status.error,
            "completed_at": status.completed_at,
        })

    except Exception as e:
        logger.error(f"Error in api_analysis_status_handler: {e}")
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def api_analysis_for_tc_handler(request):
    """Get analysis result for a specific test case. GET /api/runs/{run_id}/analysis/{tc_full_name}"""
    try:
        run_id = request.match_info["run_id"]
        tc_full_name = request.match_info["tc_full_name"]

        result = await database.db.get_analysis_for_test_case(run_id, tc_full_name)
        if not result:
            return web.json_response({"success": False, "error": "No analysis found"}, status=404)

        import json as json_mod
        refs = []
        if result.get("references_json"):
            try:
                refs = json_mod.loads(result["references_json"])
            except json_mod.JSONDecodeError:
                pass

        return web.json_response({
            "success": True,
            "tc_full_name": tc_full_name,
            "summary": result.get("summary"),
            "summary_html": result.get("summary_html"),
            "references": refs,
            "confidence": result.get("confidence"),
            "category": result.get("category"),
            "model_used": result.get("model_used"),
            "tier_used": result.get("tier_used"),
            "reasoning": result.get("reasoning"),
            "deep_html": result.get("deep_html"),
            "token_count": result.get("token_count"),
            "created_at": result.get("created_at"),
        })

    except Exception as e:
        logger.error(f"Error in api_analysis_for_tc_handler: {e}")
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def api_analysis_summary_handler(request):
    """Get all analysis results for a run. GET /api/runs/{run_id}/analysis/summary"""
    try:
        run_id = request.match_info["run_id"]
        if not validate_run_id(run_id):
            return web.json_response({"success": False, "error": "Invalid run ID"}, status=400)

        analyses = await database.db.get_analyses_for_run(run_id)
        import json as json_mod

        results = []
        for a in analyses:
            refs = []
            if a.get("references_json"):
                try:
                    refs = json_mod.loads(a["references_json"])
                except json_mod.JSONDecodeError:
                    pass

            results.append({
                "tc_full_name": a.get("tc_full_name"),
                "tc_id": a.get("tc_id"),
                "summary": a.get("summary"),
                "summary_html": a.get("summary_html"),
                "references": refs,
                "confidence": a.get("confidence"),
                "category": a.get("category"),
                "model_used": a.get("model_used"),
                "tier_used": a.get("tier_used"),
                "reasoning": a.get("reasoning"),
                "token_count": a.get("token_count"),
                "created_at": a.get("created_at"),
            })

        return web.json_response({"success": True, "analyses": results})

    except Exception as e:
        logger.error(f"Error in api_analysis_summary_handler: {e}")
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def api_trigger_deep_analysis_handler(request):
    """Trigger deep analysis for a single test case. POST /api/runs/{run_id}/analyze/{tc_full_name}/deep"""
    try:
        run_id = request.match_info["run_id"]
        tc_full_name = request.match_info["tc_full_name"]
        if not validate_run_id(run_id):
            return web.json_response({"success": False, "error": "Invalid run ID"}, status=400)

        from .ai_analysis import get_deep_analysis_status, run_deep_analysis
        from .config import AI_ANALYSIS_CONFIG

        status = get_deep_analysis_status(run_id, tc_full_name)
        if status.get("status") == "running":
            return web.json_response({"success": True, "status": "already_running"})

        if not AI_ANALYSIS_CONFIG.get("openai_api_key"):
            return web.json_response({"success": False, "error": "AI analysis not configured"}, status=400)

        asyncio.create_task(run_deep_analysis(run_id, tc_full_name))
        return web.json_response({"success": True, "status": "started"}, status=202)

    except Exception as e:
        logger.error(f"Error in api_trigger_deep_analysis_handler: {e}")
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def api_deep_analysis_status_handler(request):
    """Get deep analysis status. GET /api/runs/{run_id}/analyze/{tc_full_name}/deep"""
    try:
        run_id = request.match_info["run_id"]
        tc_full_name = request.match_info["tc_full_name"]

        from .ai_analysis import get_deep_analysis_status
        status = get_deep_analysis_status(run_id, tc_full_name)
        return web.json_response({"success": True, **status})

    except Exception as e:
        logger.error(f"Error in api_deep_analysis_status_handler: {e}")
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def api_email_recipients_get_handler(request):
    """Get current email recipients. GET /api/settings/email-recipients"""
    try:
        import json as json_mod
        from .config import EMAIL_CONFIG

        db_value = await database.db.get_setting("email_recipients")
        if db_value:
            try:
                addresses = json_mod.loads(db_value)
                source = "database"
            except json_mod.JSONDecodeError:
                addresses = EMAIL_CONFIG.get("to_addresses", [])
                source = "config_file"
        else:
            addresses = EMAIL_CONFIG.get("to_addresses", [])
            source = "config_file"

        return web.json_response({
            "success": True,
            "to_addresses": addresses,
            "source": source,
        })

    except Exception as e:
        logger.error(f"Error in api_email_recipients_get_handler: {e}")
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def api_email_recipients_put_handler(request):
    """Update email recipients. PUT /api/settings/email-recipients"""
    try:
        import json as json_mod
        body = await request.json()
        addresses = body.get("to_addresses", [])

        if not isinstance(addresses, list):
            return web.json_response({"success": False, "error": "to_addresses must be a list"}, status=400)

        await database.db.set_setting("email_recipients", json_mod.dumps(addresses))

        return web.json_response({"success": True, "to_addresses": addresses, "source": "database"})

    except Exception as e:
        logger.error(f"Error in api_email_recipients_put_handler: {e}")
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def api_email_recipients_delete_handler(request):
    """Reset email recipients to config defaults. DELETE /api/settings/email-recipients"""
    try:
        from .config import EMAIL_CONFIG
        await database.db.delete_setting("email_recipients")
        return web.json_response({
            "success": True,
            "to_addresses": EMAIL_CONFIG.get("to_addresses", []),
            "source": "config_file",
        })

    except Exception as e:
        logger.error(f"Error in api_email_recipients_delete_handler: {e}")
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def api_ai_usage_handler(request):
    """Get AI usage and budget status. GET /api/settings/ai-usage"""
    try:
        from .config import AI_ANALYSIS_CONFIG

        month = datetime.now(timezone.utc).strftime("%Y-%m")
        usage = await database.db.get_ai_usage_for_month(month)

        budget = AI_ANALYSIS_CONFIG.get("monthly_budget_usd", 0)
        cost = usage["estimated_cost_usd"] if usage else 0

        return web.json_response({
            "success": True,
            "current_month": month,
            "estimated_cost_usd": cost,
            "monthly_budget_usd": budget,
            "budget_utilization": cost / budget if budget > 0 else 0,
            "warning_threshold": AI_ANALYSIS_CONFIG.get("budget_warning_threshold", 0.8),
            "warning_sent": usage.get("warning_sent", False) if usage else False,
            "prompt_tokens": usage.get("prompt_tokens", 0) if usage else 0,
            "completion_tokens": usage.get("completion_tokens", 0) if usage else 0,
        })

    except Exception as e:
        logger.error(f"Error in api_ai_usage_handler: {e}")
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def api_logs_handler(request):
    """Get recent server log entries. GET /api/logs?after_seq=0&level=INFO&limit=500"""
    try:
        from .log_buffer import log_buffer

        after_seq = int(request.query.get("after_seq", 0))
        level = request.query.get("level", None)
        limit = int(request.query.get("limit", 500))

        entries = log_buffer.get_entries(after_seq=after_seq, level=level, limit=limit)
        return web.json_response({"success": True, "entries": entries})

    except Exception as e:
        logger.error(f"Error in api_logs_handler: {e}")
        return web.json_response({"success": False, "error": str(e)}, status=500)


# --- Route Registration ---

def get_routes():
    """Return list of routes for API handlers."""
    return [
        web.get("/api/targets", api_targets_handler),
        web.post("/api/targets", api_targets_handler),
        web.get("/api/targets/{key}", api_target_handler),
        web.put("/api/targets/{key}", api_target_handler),
        web.delete("/api/targets/{key}", api_target_handler),
        web.put("/api/targets/{key}/complete-setup", api_target_complete_setup_handler),
        web.get("/api/collections", api_collections_handler),
        web.post("/api/collections", api_collections_handler),
        web.get("/api/collections/{key}", api_collection_handler),
        web.put("/api/collections/{key}", api_collection_handler),
        web.delete("/api/collections/{key}", api_collection_handler),
        web.put("/api/collections/{key}/members", api_collection_members_handler),
        web.post("/api/collections/{key}/profiles", api_collection_profiles_handler),
        web.get("/api/collections/{key}/summary", api_collection_summary_handler),
        web.post("/api/collections/{key}/reports", api_collection_report_handler),
        web.get("/api/run-set", api_run_set_handler),
        web.get("/api/profiles/{profile_id}", api_profile_handler),
        web.put("/api/profiles/{profile_id}", api_profile_handler),
        web.delete("/api/profiles/{profile_id}", api_profile_handler),
        web.get("/api/test-runs", api_test_runs_handler),
        web.get("/api/test-runs/{run_id}", api_test_run_details_handler),
        web.get("/api/test-results/for-runs", api_test_results_for_runs_handler),
        web.get("/api/test-results/over-time", api_test_results_over_time_handler),
        web.get("/api/test-case/history", api_test_case_history_handler),
        web.get("/api/test-case/history-with-links", api_test_case_history_with_links_handler),
        web.get("/api/metadata/keys", api_metadata_keys_handler),
        web.get("/api/metadata/values", api_metadata_values_handler),
        web.get("/api/failures/toplist", api_failures_toplist_handler),
        web.get("/api/classifications/{run_id}", api_classifications_for_run_handler),
        web.get("/api/tc-hover-history", api_tc_hover_history_handler),
        web.get("/api/run-hover-history/{group_hash}", api_run_hover_history_handler),
        web.post("/api/migrate-data", api_migrate_data_handler),
        web.get("/api/server-info", api_server_info_handler),
        web.post("/api/admin/shutdown", api_admin_shutdown_handler),
        web.post("/api/runs/{run_id}/commits", api_run_commits_upload_handler),
        web.get("/api/runs/{run_id}/commits", api_run_commits_get_handler),
        web.get("/api/runs/{run_id}/commit-baselines", api_run_commit_baselines_handler),
        # AI analysis endpoints
        web.post("/api/runs/{run_id}/analyze", api_trigger_analysis_handler),
        web.get("/api/runs/{run_id}/analysis", api_analysis_status_handler),
        web.get("/api/runs/{run_id}/analysis/summary", api_analysis_summary_handler),
        web.get("/api/runs/{run_id}/analysis/{tc_full_name:.+}", api_analysis_for_tc_handler),
        # Deep analysis endpoints
        web.post("/api/runs/{run_id}/analyze/{tc_full_name:.+}/deep", api_trigger_deep_analysis_handler),
        web.get("/api/runs/{run_id}/analyze/{tc_full_name:.+}/deep", api_deep_analysis_status_handler),
        # Settings endpoints
        web.get("/api/settings/email-recipients", api_email_recipients_get_handler),
        web.put("/api/settings/email-recipients", api_email_recipients_put_handler),
        web.delete("/api/settings/email-recipients", api_email_recipients_delete_handler),
        web.get("/api/settings/ai-usage", api_ai_usage_handler),
        # Server log endpoint
        web.get("/api/logs", api_logs_handler),
    ]
