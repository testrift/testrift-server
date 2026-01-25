"""
HTTP handlers for TestRift server.

Page handlers, static file handlers, attachment handlers, and ZIP export.
"""

import json
import logging
import zipfile
from datetime import datetime, UTC

import aiofiles
import msgpack
from aiohttp import web
from jinja2 import Environment, FileSystemLoader

from .config import (
    TEMPLATES_DIR,
    STATIC_DIR,
    DATA_DIR,
    ATTACHMENTS_ENABLED,
    ATTACHMENT_MAX_SIZE,
)
from .utils import (
    get_run_path,
    get_merged_log_path,
    get_attachments_dir,
    get_attachment_path,
    read_meta_msgpack,
    sanitize_filename,
    validate_run_id,
    validate_test_case_id,
    validate_group_hash_value,
    find_test_case_by_tc_id,
    get_run_and_test_case_by_tc_id,
    META_FILE,
    TC_ID_FIELD,
    TC_FULL_NAME_FIELD,
)
from .models import TestRunData, TestCaseData
from .protocol_utils import decode_log_entries
from . import database

logger = logging.getLogger(__name__)

# Common headers for no-cache responses
NO_CACHE_HEADERS = {
    'Cache-Control': 'no-cache, no-store, must-revalidate',
    'Pragma': 'no-cache',
    'Expires': '0'
}

# Template environment
env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))


def render_template(template_name, **context):
    """Render a Jinja2 template with the given context."""
    template = env.get_template(template_name)
    return template.render(**context)


def log_event(event: str, **fields):
    """Log an event with timestamp."""
    record = {"event": event, **fields, "ts": datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"}
    logger.info(json.dumps(record))


# --- Page handlers ---

async def build_run_index_entries(runs_from_db):
    """Build run index entries for the index page."""
    runs_index = []
    for run in runs_from_db:
        run_id = run['run_id']

        # Get user metadata for this run (already in correct format)
        user_metadata = await database.db.get_user_metadata_for_run(run_id)
        group_metadata = await database.db.get_group_metadata_for_run(run_id)
        group_name = run.get('group_name')
        group_hash = run.get('group_hash')
        group_info = None
        if group_name or group_hash or group_metadata:
            group_info = {
                'name': group_name,
                'hash': group_hash,
                'metadata': group_metadata
            }

        # Check if files exist on disk
        run_path = get_run_path(run_id)
        files_exist = run_path.exists()

        # Build run info for template
        # Note: aborted_count is combined into error_count for display
        aborted_count = run.get('aborted_count', 0)
        error_count = run.get('error_count', 0)
        run_info = {
            'run_id': run_id,
            'run_name': run.get('run_name'),
            'status': run.get('status'),
            'start_time': run.get('start_time'),
            'end_time': run.get('end_time'),
            'retention_days': run.get('retention_days'),
            'passed_count': run.get('passed_count', 0),
            'failed_count': run.get('failed_count', 0),
            'skipped_count': run.get('skipped_count', 0),
            'aborted_count': aborted_count,  # Keep for template logic that combines it
            'error_count': error_count + aborted_count,  # Combine aborted into error
            'user_metadata': user_metadata,
            'group': group_info,
            'group_hash': group_hash,  # Also include at top level for easy access
            'files_exist': files_exist
        }

        # Apply test summary logic for finished runs with error precedence
        if run_info['status'] and run_info['status'].lower() == 'finished':
            if run_info['error_count'] > 0:
                run_info['status'] = 'Error'
            elif run_info['failed_count'] > 0:
                run_info['status'] = 'Failed'
            elif run_info['passed_count'] > 0:
                run_info['status'] = 'Passed'
            # Keep 'Finished' as fallback if no test results
        elif run_info['status'] and run_info['status'].lower() == 'aborted':
            run_info['status'] = 'Aborted'

        runs_index.append(run_info)

    # Sort by start time descending (database should already sort, but ensure it)
    runs_index.sort(key=lambda r: r.get("start_time") or "", reverse=True)

    return runs_index


async def index_handler(request):
    """Serve Test Runs index with embedded JavaScript for live updates."""
    # Get all runs from database
    runs_from_db = await database.db.get_test_runs(limit=1000)
    runs_index = await build_run_index_entries(runs_from_db)

    html = render_template('index.html', runs_index=runs_index, group_context=None)

    return web.Response(text=html, content_type="text/html", headers=NO_CACHE_HEADERS)


async def group_runs_handler(request):
    """Serve runs filtered by group hash."""
    group_hash = request.match_info.get("group_hash")
    if not validate_group_hash_value(group_hash):
        return web.Response(status=400, text="Invalid group hash")

    runs_from_db = await database.db.get_test_runs(limit=1000, group_hash=group_hash)
    if not runs_from_db:
        return web.Response(status=404, text="No runs found for this group")

    runs_index = await build_run_index_entries(runs_from_db)
    first_run_id = runs_from_db[0]['run_id']
    group_metadata = await database.db.get_group_metadata_for_run(first_run_id)
    group_name = runs_from_db[0].get('group_name')
    group_context = {
        "hash": group_hash,
        "name": group_name,
        "metadata": group_metadata
    }

    html = render_template('index.html', runs_index=runs_index, group_context=group_context)

    return web.Response(text=html, content_type="text/html", headers=NO_CACHE_HEADERS)


async def test_run_index_handler(request):
    """Serve the test run page with all test case metadata."""
    run_id = request.match_info["run_id"]

    # Validate run_id to prevent path traversal
    if not validate_run_id(run_id):
        return web.Response(status=400, text="Invalid run ID")

    run_path = get_run_path(run_id)
    files_exist = run_path.exists()

    # First try to get the run from WebSocket server's in-memory data
    ws_server = request.app["ws_server"]
    run = ws_server.test_runs.get(run_id)
    live_run = False

    group_info = None

    if run:
        # Use in-memory data, but only consider it "live" if it's actually running
        live_run = (run.status == "running")
        test_cases_dict = {tc_id: tc.to_dict() for tc_id, tc in run.test_cases.items()}

        # Count test results for multiple badges
        passed_count = 0
        failed_count = 0
        skipped_count = 0
        error_count = 0

        for tc in run.test_cases.values():
            status_val = tc.status.lower()
            if status_val in ['passed', 'failed', 'skipped', 'aborted', 'error']:
                if status_val == 'passed':
                    passed_count += 1
                elif status_val == 'failed':
                    failed_count += 1
                elif status_val == 'skipped':
                    skipped_count += 1
                elif status_val == 'aborted':
                    # Aborted tests are counted as failed for display purposes
                    failed_count += 1
                elif status_val == 'error':
                    error_count += 1

        # Determine status display with error precedence
        if run.status.lower() == 'finished':
            if error_count > 0:
                status = 'Error'
            elif failed_count > 0:
                status = 'Failed'
            elif passed_count > 0:
                status = 'Passed'
            else:
                status = 'Finished'  # Fallback if no test results
        elif run.status.lower() == 'aborted':
            status = 'Aborted'
        else:
            status = run.status

        start_time = run.start_time
        end_time = run.end_time
        user_metadata = run.user_metadata
        retention_days = run.retention_days
        run_name = run.run_name
        abort_reason = run.abort_reason
        if run.group or run.group_hash:
            group_info = {
                "name": run.group.get("name") if run.group else None,
                "hash": run.group_hash,
                "metadata": (run.group or {}).get("metadata", {}) if run.group else {}
            }
    else:
        # Fall back to database for completed runs
        run_data = await database.db.get_test_run_by_id(run_id)

        if not run_data:
            return web.Response(status=404, text="Run not found")

        # Get test cases from database
        test_cases_list = await database.db.get_test_cases_for_run(run_id)

        # Get user metadata from database (already in correct format)
        user_metadata = await database.db.get_user_metadata_for_run(run_id)
        group_metadata = await database.db.get_group_metadata_for_run(run_id)
        group_name = run_data.get("group_name")
        group_hash = run_data.get("group_hash")
        if group_name or group_hash or group_metadata:
            group_info = {
                "name": group_name,
                "hash": group_hash,
                "metadata": group_metadata
            }

        # Convert test cases list to dict format expected by template
        storage_lookup = {}
        abort_reason = None
        if files_exist:
            disk_run = TestRunData.load_from_disk(run_id)
            if disk_run:
                storage_lookup = {tc.full_name: tc.tc_id for tc in disk_run.test_cases.values()}
                abort_reason = disk_run.abort_reason

        test_cases_dict = {}
        for tc in test_cases_list:
            full_name = tc['tc_full_name']
            test_cases_dict[full_name] = {
                TC_ID_FIELD: storage_lookup.get(full_name),
                TC_FULL_NAME_FIELD: full_name,
                'status': tc['status'],
                'start_time': tc.get('start_time'),
                'end_time': tc.get('end_time')
            }

        # Get run_name from database
        run_name = run_data.get('run_name')

        # Count test results from database data
        passed_count = run_data.get('passed_count', 0)
        failed_count = run_data.get('failed_count', 0)
        skipped_count = run_data.get('skipped_count', 0)
        error_count = run_data.get('error_count', 0)

        # Determine status display with error precedence
        run_status = run_data.get("status")
        if run_status and run_status.lower() == 'finished':
            if error_count > 0:
                status = 'Error'
            elif failed_count > 0:
                status = 'Failed'
            elif passed_count > 0:
                status = 'Passed'
            else:
                status = 'Finished'  # Fallback if no test results
        elif run_status and run_status.lower() == 'aborted':
            status = 'Aborted'
        else:
            status = run_status

        start_time = run_data.get("start_time")
        end_time = run_data.get("end_time")
        retention_days = run_data.get("retention_days")

    html = render_template(
        'test_run.html',
        run_id=run_id,
        run_name=run_name,
        status=status,
        abort_reason=abort_reason,
        start_time=start_time,
        end_time=end_time,
        user_metadata=user_metadata,
        group=group_info,
        retention_days=retention_days,
        test_cases=test_cases_dict,
        live_run=live_run,
        passed_count=passed_count,
        failed_count=failed_count,
        skipped_count=skipped_count,
        error_count=error_count,
        files_exist=files_exist,
        server_mode=True
    )

    # Add cache control headers to prevent caching of live runs
    headers = NO_CACHE_HEADERS if live_run else {}

    return web.Response(text=html, content_type="text/html", headers=headers)


async def test_case_log_handler(request):
    """Serve the test case log page."""
    run_id = request.match_info["run_id"]
    tc_id = request.match_info["test_case_id"]

    # Validate run_id and tc_id to prevent path traversal
    if not validate_run_id(run_id) or not validate_test_case_id(tc_id):
        return web.Response(status=400, text="Invalid run ID or test case ID")

    ws_server = request.app["ws_server"]
    run = ws_server.test_runs.get(run_id)
    live_run = False
    test_case = None

    if run:
        # Consider it live if the run is running OR if the specific test case is running
        live_run = (run.status == "running")
        test_case = find_test_case_by_tc_id(run, tc_id)
        if test_case is None:
            return web.Response(status=404, text="Test case not found")
        # Also consider it live if this specific test case is running
        if test_case.status == "running":
            live_run = True
        logger.info(
            "Live run detection - Run in memory: %s, Run status: %s, Test case %s status: %s, Live: %s",
            run_id,
            run.status,
            test_case.id,
            test_case.status,
            live_run,
        )
    else:
        run = TestRunData.load_from_disk(run_id)
        if run is None:
            return web.Response(status=404, text="Run not found")
        test_case = find_test_case_by_tc_id(run, tc_id)
        if test_case is None:
            return web.Response(status=404, text="Test case not found")
        if not test_case.load_log_from_disk():
            return web.Response(status=404, text="Log not found")

        # Check if this test case is still running by checking if it has recent log activity
        if test_case.status == "running":
            import time
            current_time = time.time()
            recent_logs = False
            for log_entry in test_case.logs:
                try:
                    log_time = datetime.fromisoformat(log_entry.get("timestamp", "").replace("Z", "+00:00")).timestamp()
                    if current_time - log_time < 30:  # Within last 30 seconds
                        recent_logs = True
                        break
                except:
                    pass

            if recent_logs:
                live_run = True
                logger.info(f"Live run detection - Run from disk: {run_id}, Test case status: {test_case.status}, Recent logs: {recent_logs}, Live: {live_run}")
            else:
                logger.info(f"Live run detection - Run from disk: {run_id}, Test case status: {test_case.status}, No recent logs, Live: {live_run}")
        else:
            logger.info(f"Live run detection - Run from disk: {run_id}, Test case status: {test_case.status}, Live: {live_run}")

    # Get group_hash for history feature
    group_hash = None
    run_dict = run.to_dict()
    if hasattr(run, 'group_hash'):
        group_hash = run.group_hash
    elif 'group_hash' in run_dict:
        group_hash = run_dict.get('group_hash')

    # For non-live runs, decode compact protocol entries for template embedding
    # Live runs send raw entries via WebSocket where JS decodes them
    if live_run:
        decoded_logs = []
    else:
        # Use the run's string table for interned component/channel strings
        string_table = getattr(run, 'string_table', None) or {}
        decoded_logs = decode_log_entries(test_case.logs, string_table) if test_case.logs else []

    html = render_template(
        'test_case_log.html',
        run_id=run_id,
        run_name=run.run_name,
        test_case_id=tc_id,
        run=run,
        run_meta=run_dict,
        test_case=test_case,
        tc_meta=test_case.to_dict(),
        logs=decoded_logs,
        stack_traces=[] if live_run else test_case.stack_traces,
        live_run=live_run,
        server_mode=True,  # Always True when served from live server
        attachments=None,  # Attachments loaded via API in server mode
        group_hash=group_hash
    )

    # Add cache control headers to prevent caching of live test case logs
    headers = NO_CACHE_HEADERS if live_run else {}

    return web.Response(text=html, content_type="text/html", headers=headers)


# --- ZIP Export Handler ---

async def zip_export_handler(request):
    """Export a test run as a ZIP file."""
    run_id = request.match_info["run_id"]

    # Validate run_id to prevent path traversal
    if not validate_run_id(run_id):
        return web.Response(status=400, text="Invalid run ID")

    run_path = get_run_path(run_id)

    try:
        if not run_path.exists():
            raise FileNotFoundError("Run not found")

        zip_name = f"{run_id}.zip"
        zip_path = run_path / zip_name

        # Remove existing zip if exists
        if zip_path.exists():
            zip_path.unlink()

        # Create zip archive with all HTML pages and logs embedded
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            # Add test run index page (static mode via unified template)
            meta = read_meta_msgpack(run_id)
            if meta is None:
                raise FileNotFoundError("meta.msgpack not found")
            run = TestRunData.from_dict(run_id, meta)
            test_cases_dict = {tc_id: tc.to_dict() for tc_id, tc in run.test_cases.items()}
            # Count test results for multiple badges
            passed_count = 0
            failed_count = 0
            skipped_count = 0

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
                        failed_count += 1

            run_html = render_template(
                'test_run.html',
                run_id=run_id,
                run_name=meta.get('run_name'),
                status=run.status,
                start_time=run.start_time,
                end_time=run.end_time,
                user_metadata=meta.get("user_metadata", {}),
                retention_days=meta.get("retention_days"),
                test_cases=test_cases_dict,
                live_run=False,
                server_mode=False,
                passed_count=passed_count,
                failed_count=failed_count,
                skipped_count=skipped_count,
                error_count=0,
                abort_reason=None,
                files_exist=True,  # Files always exist for ZIP export
                group=None  # No group info needed for static ZIP export
            )
            zf.writestr("index.html", run_html)

            # Add shared status badges CSS
            status_badges_css_path = STATIC_DIR / "status-badges.css"
            if status_badges_css_path.exists():
                with open(status_badges_css_path, "r", encoding="utf-8") as f:
                    status_badges_css_content = f.read()
                zf.writestr("static/status-badges.css", status_badges_css_content)

            # Add classifications CSS/JS for badges and icons
            classifications_css_path = STATIC_DIR / "classifications.css"
            if classifications_css_path.exists():
                with open(classifications_css_path, "r", encoding="utf-8") as f:
                    classifications_css_content = f.read()
                zf.writestr("static/classifications.css", classifications_css_content)

            classifications_js_path = STATIC_DIR / "classifications.js"
            if classifications_js_path.exists():
                with open(classifications_js_path, "r", encoding="utf-8") as f:
                    classifications_js_content = f.read()
                zf.writestr("static/classifications.js", classifications_js_content)

            # Add CSS file for test case logs
            css_path = STATIC_DIR / "test_case_log.css"
            if css_path.exists():
                with open(css_path, "r", encoding="utf-8") as f:
                    css_content = f.read()
                zf.writestr("static/test_case_log.css", css_content)

            # Add JavaScript files for test case logs
            js_path = STATIC_DIR / "at_syntax.js"
            if js_path.exists():
                with open(js_path, "r", encoding="utf-8") as f:
                    js_content = f.read()
                zf.writestr("static/at_syntax.js", js_content)

            # Add main test case log JavaScript file
            tc_js_path = STATIC_DIR / "test_case_log.js"
            if tc_js_path.exists():
                with open(tc_js_path, "r", encoding="utf-8") as f:
                    tc_js_content = f.read()
                zf.writestr("static/test_case_log.js", tc_js_content)

            # Add each test case log page (static mode via unified template)
            # Get the string table for decoding interned strings
            string_table = getattr(run, 'string_table', None) or {}

            for tc_full_name, tc in run.test_cases.items():
                case_slug = tc.tc_id

                # Load logs using the model's method (handles both individual and merged files)
                tc.load_log_from_disk()
                raw_logs = tc.logs
                logs = decode_log_entries(raw_logs, string_table) if raw_logs else []

                # Stack traces are loaded by load_log_from_disk when reading from merged file
                stack_traces = tc.stack_traces or []

                # Collect attachment information for this test case
                attachments = []
                attachments_dir = get_attachments_dir(run_id, tc_id=tc.tc_id)
                if attachments_dir.exists():
                    for attachment_file in attachments_dir.iterdir():
                        if attachment_file.is_file():
                            attachments.append({
                                "filename": attachment_file.name,
                                "size": attachment_file.stat().st_size,
                                "modified_time": datetime.fromtimestamp(attachment_file.stat().st_mtime, UTC).isoformat() + "Z"
                            })

                log_html = render_template(
                    'test_case_log.html',
                    run_id=run_id,
                    run_name=meta.get('run_name'),
                    test_case_id=tc.tc_id,
                    run_meta=meta,
                    tc_meta=tc.to_dict(),
                    logs=logs,
                    stack_traces=stack_traces,
                    attachments=attachments,
                    live_run=False,
                    server_mode=False
                )
                zf.writestr(f"log/{case_slug}.html", log_html)

                # Add attachments for this test case
                attachments_dir = get_attachments_dir(run_id, tc_id=tc.tc_id)
                if attachments_dir.exists():
                    for attachment_file in attachments_dir.iterdir():
                        if attachment_file.is_file():
                            with open(attachment_file, "rb") as f:
                                attachment_data = f.read()
                            zf.writestr(f"attachments/{case_slug}/{attachment_file.name}", attachment_data)

        headers = {
            "Content-Disposition": f"attachment; filename={zip_name}"
        }
        return web.FileResponse(path=zip_path, headers=headers)
    except FileNotFoundError as e:
        log_event("zip_export_missing", run_id=run_id, error=str(e))
        return web.Response(status=404, text=f"Export failed: {str(e)}. Try re-running the export after the test finishes.")
    except Exception as e:
        log_event("zip_export_error", run_id=run_id, error=str(e))
        return web.Response(status=500, text="Export failed due to a server error. Please try again later.")


# --- Static file handlers ---

async def static_handler(request):
    """Serve static files under /testRun/."""
    rel_path = request.match_info["tail"]

    # Validate path to prevent directory traversal
    if not rel_path or '..' in rel_path or rel_path.startswith('/'):
        return web.Response(status=400, text="Invalid path")

    # Normalize path and ensure it stays within DATA_DIR
    try:
        full_path = (DATA_DIR / rel_path).resolve()
        data_dir_resolved = DATA_DIR.resolve()

        # Ensure the resolved path is within DATA_DIR
        if not str(full_path).startswith(str(data_dir_resolved)):
            return web.Response(status=403, text="Access denied")

    except (OSError, ValueError):
        return web.Response(status=400, text="Invalid path")

    if not full_path.exists() or not full_path.is_file():
        return web.Response(status=404, text="Not Found")
    return web.FileResponse(path=full_path)


async def static_file_handler(request):
    """Serve static files from static directory."""
    static_path = request.match_info["path"]

    # Validate path to prevent directory traversal
    if not static_path or '..' in static_path or static_path.startswith('/'):
        return web.Response(status=400, text="Invalid path")

    # Normalize path and ensure it stays within static directory
    try:
        static_dir = STATIC_DIR
        full_path = (static_dir / static_path).resolve()
        static_dir_resolved = static_dir.resolve()

        # Ensure the resolved path is within static directory
        if not str(full_path).startswith(str(static_dir_resolved)):
            return web.Response(status=403, text="Access denied")

    except (OSError, ValueError):
        return web.Response(status=400, text="Invalid path")

    if not full_path.exists() or not full_path.is_file():
        return web.Response(status=404, text="Static file not found")
    return web.FileResponse(path=full_path)


# --- Attachment handlers ---

async def upload_attachment_handler(request):
    """Handle attachment uploads for test cases."""
    # Check if attachments are enabled
    if not ATTACHMENTS_ENABLED:
        return web.Response(status=403, text="Attachment upload is disabled")

    run_id = request.match_info["run_id"]
    tc_id = request.match_info["test_case_id"]

    # Validate run_id and tc_id to prevent path traversal
    if not validate_run_id(run_id) or not validate_test_case_id(tc_id):
        return web.Response(status=400, text="Invalid run ID or test case ID")

    # Verify the test run exists
    run_path = get_run_path(run_id)
    if not run_path.exists():
        return web.Response(status=404, text="Test run not found")

    run_obj, test_case = get_run_and_test_case_by_tc_id(request.app, run_id, tc_id)
    if not test_case:
        logger.error(f"Attachment upload failed to resolve test case tc_id {tc_id} in run {run_id}")
        return web.Response(status=404, text="Test case not found")

    tc_id_val = test_case.tc_id

    try:
        # Parse multipart form data
        reader = await request.multipart()

        attachment_files = []
        while True:
            part = await reader.next()
            if part is None:
                break

            if part.name == 'attachment':
                # Read the file content
                filename = part.filename
                if not filename:
                    continue

                # Validate and sanitize filename
                if not isinstance(filename, str) or len(filename) == 0:
                    continue

                # Sanitize the filename to prevent path traversal
                sanitized_filename = sanitize_filename(filename)

                # Additional validation for file size (configurable limit)
                content_length = 0
                max_size = ATTACHMENT_MAX_SIZE

                # Create attachments directory
                attachments_dir = get_attachments_dir(run_id, tc_id=tc_id_val)
                attachments_dir.mkdir(parents=True, exist_ok=True)

                # Save the file with size validation
                file_path = get_attachment_path(run_id, sanitized_filename, tc_id=tc_id_val)
                async with aiofiles.open(file_path, 'wb') as f:
                    while True:
                        chunk = await part.read_chunk(8192)  # 8KB chunks
                        if not chunk:
                            break
                        content_length += len(chunk)
                        if content_length > max_size:
                            # Delete the file if it exceeds size limit
                            await f.close()
                            if file_path.exists():
                                file_path.unlink()
                            max_size_mb = max_size // (1024 * 1024)
                            return web.Response(status=413, text=f"File too large (max {max_size_mb}MB)")
                        await f.write(chunk)

                attachment_files.append({
                    "filename": filename,
                    "size": file_path.stat().st_size,
                    "upload_time": datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"
                })

                log_event("attachment_uploaded", run_id=run_id, test_case_id=test_case.id,
                         filename=filename, size=file_path.stat().st_size)

        return web.json_response({
            "success": True,
            "attachments": attachment_files
        })

    except Exception as e:
        log_event("attachment_upload_error", run_id=run_id, test_case_id=tc_id, error=str(e))
        return web.Response(status=500, text=f"Upload failed: {str(e)}")


async def download_attachment_handler(request):
    """Handle attachment downloads for test cases."""
    run_id = request.match_info["run_id"]
    tc_id = request.match_info["test_case_id"]
    filename = request.match_info["filename"]

    # Validate run_id and tc_id to prevent path traversal
    if not validate_run_id(run_id) or not validate_test_case_id(tc_id):
        return web.Response(status=400, text="Invalid run ID or test case ID")

    # Validate filename
    if not filename or not isinstance(filename, str):
        return web.Response(status=400, text="Invalid filename")

    # Sanitize filename to ensure it's safe
    sanitized_filename = sanitize_filename(filename)
    if sanitized_filename != filename:
        return web.Response(status=400, text="Invalid filename characters")

    # Verify the test run exists
    run_path = get_run_path(run_id)
    if not run_path.exists():
        return web.Response(status=404, text="Test run not found")

    run_obj, test_case = get_run_and_test_case_by_tc_id(request.app, run_id, tc_id)
    if not test_case:
        logger.error(f"Attachment download failed to resolve test case tc_id {tc_id} in run {run_id}")
        return web.Response(status=404, text="Test case not found")

    tc_id_val = test_case.tc_id

    # Get the attachment file path
    file_path = get_attachment_path(run_id, filename, tc_id=tc_id_val)

    if not file_path.exists() or not file_path.is_file():
        return web.Response(status=404, text="Attachment not found")

    # Set appropriate headers for file download
    headers = {
        "Content-Disposition": f"attachment; filename={filename}",
        "Content-Type": "application/octet-stream"
    }

    return web.FileResponse(path=file_path, headers=headers)


async def list_attachments_handler(request):
    """List all attachments for a test case."""
    run_id = request.match_info["run_id"]
    tc_id = request.match_info["test_case_id"]

    # Validate run_id and tc_id to prevent path traversal
    if not validate_run_id(run_id) or not validate_test_case_id(tc_id):
        return web.Response(status=400, text="Invalid run ID or test case ID")

    # Verify the test run exists
    run_path = get_run_path(run_id)
    if not run_path.exists():
        return web.Response(status=404, text="Test run not found")

    run_obj, test_case = get_run_and_test_case_by_tc_id(request.app, run_id, tc_id)
    if not test_case:
        logger.error(f"Attachment listing failed to resolve test case tc_id {tc_id} in run {run_id}")
        return web.Response(status=404, text="Test case not found")

    tc_id_val = test_case.tc_id

    # Get attachments directory
    attachments_dir = get_attachments_dir(run_id, tc_id=tc_id_val)

    attachments = []
    if attachments_dir.exists():
        for file_path in attachments_dir.iterdir():
            if file_path.is_file():
                attachments.append({
                    "filename": file_path.name,
                    "size": file_path.stat().st_size,
                    "modified_time": datetime.fromtimestamp(file_path.stat().st_mtime, UTC).replace(tzinfo=None).isoformat() + "Z"
                })

    return web.json_response({"attachments": attachments})


# --- Health check ---

async def health_handler(request):
    """Health check endpoint."""
    return web.json_response({"status": "ok"})


# --- Analyzer page handlers ---

async def failures_handler(request):
    """Serve the failure top list page."""
    try:
        html = render_template('failures.html')
        return web.Response(text=html, content_type="text/html", headers=NO_CACHE_HEADERS)

    except Exception as e:
        logger.error(f"Error in failures_handler: {e}")
        return web.Response(status=500, text=f"Error loading failures page: {str(e)}")


async def analyzer_handler(request):
    """Serve the test results analyzer page."""
    try:
        html = render_template('analyzer.html')
        return web.Response(text=html, content_type="text/html", headers=NO_CACHE_HEADERS)

    except Exception as e:
        logger.error(f"Error in analyzer_handler: {e}")
        return web.Response(status=500, text=f"Error loading analyzer page: {str(e)}")


async def matrix_handler(request):
    """Serve the test results matrix page."""
    try:
        html = render_template('matrix.html')
        return web.Response(text=html, content_type="text/html", headers=NO_CACHE_HEADERS)

    except Exception as e:
        logger.error(f"Error in matrix_handler: {e}")
        return web.Response(status=500, text=f"Error loading matrix page: {str(e)}")


# --- Route Registration ---

def get_routes():
    """Return list of routes for HTTP handlers."""
    routes = [
        web.get("/", index_handler),
        web.get("/groups/{group_hash}", group_runs_handler),
        web.get("/testRun/{run_id}/index.html", test_run_index_handler),
        web.get("/testRun/{run_id}/log/{test_case_id}.html", test_case_log_handler),
        web.get("/testRun/{tail:.*}", static_handler),
        web.get("/static/{path:.*}", static_file_handler),
        web.get("/export/{run_id}.zip", zip_export_handler),
        web.get("/health", health_handler),
        web.get("/analyzer", analyzer_handler),
        web.get("/matrix", matrix_handler),
        web.get("/failures", failures_handler),
    ]

    # Add attachment routes only if enabled
    if ATTACHMENTS_ENABLED:
        routes.extend([
            web.post("/api/attachments/{run_id}/{test_case_id}/upload", upload_attachment_handler),
            web.get("/api/attachments/{run_id}/{test_case_id}/list", list_attachments_handler),
            web.get("/api/attachments/{run_id}/{test_case_id}/download/{filename}", download_attachment_handler),
        ])

    return routes
