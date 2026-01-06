#!/usr/bin/env python3
"""
Integration tests using Playwright to verify the flow:
WebSocket client -> Server -> UI rendering

These tests:
1. Start the server
2. Send WebSocket messages (simulating NUnit client)
3. Use Playwright to open browser and verify UI displays correctly

Note: These are integration tests (WebSocket -> Server -> UI), not full E2E tests.
Full E2E tests would run actual NUnit tests (NUnit -> WebSocket -> Server -> UI).
"""

import pytest
import pytest_asyncio
import asyncio
import json
import tempfile
import shutil
import subprocess
import time
import signal
import os
import sys
from pathlib import Path
from datetime import datetime, UTC

# Add the server package source directory to the path
repo_root = Path(__file__).resolve().parent.parent
server_src_dir = repo_root / "src"
sys.path.insert(0, str(server_src_dir))

try:
    from playwright.async_api import async_playwright, Page, expect
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    print("Warning: playwright not installed. E2E tests will be skipped.")
    print("Install with: pip install playwright && playwright install chromium")

from aiohttp import ClientSession, WSMsgType
import aiohttp


class TestIntegrationUI:
    """Integration tests with browser automation (WebSocket -> Server -> UI)."""

    @pytest_asyncio.fixture
    async def temp_data_dir(self):
        """Create a temporary data directory for testing."""
        temp_dir = tempfile.mkdtemp()
        data_dir = Path(temp_dir) / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        yield data_dir

        # Cleanup
        shutil.rmtree(temp_dir)

    @pytest_asyncio.fixture
    async def server_process(self, temp_data_dir):
        """Start the server in a subprocess for testing."""
        server_dir = repo_root

        # Use a fixed test port to avoid conflicts
        test_port = 18080

        # Create a dedicated test config file and point the server to it via env var
        test_config_path = temp_data_dir.parent / "testrift_server_test.yaml"
        config_content = f"""
server:
  port: {test_port}
  localhost_only: true

data:
  directory: {temp_data_dir.resolve()}
  default_retention_days: 1
"""
        test_config_path.write_text(config_content)

        # Start server - don't pipe stdout/stderr to avoid blocking
        env = os.environ.copy()
        env['PYTHONPATH'] = str(server_src_dir)
        env['TESTRIFT_SERVER_YAML'] = str(test_config_path)

        # Use DEVNULL or a log file to avoid blocking
        log_file = temp_data_dir.parent / "server_test.log"
        with open(log_file, 'w') as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "testrift_server"],
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=str(server_dir)
            )

        # Wait for server to start with better error handling
        # Check more frequently (every 0.1s) but with shorter total timeout
        max_wait = 50  # 50 * 0.1s = 5 seconds max
        started = False
        last_error = None

        for i in range(max_wait):
            try:
                async with ClientSession() as session:
                    async with session.get(f"http://127.0.0.1:{test_port}/", timeout=aiohttp.ClientTimeout(total=0.5)) as resp:
                        if resp.status == 200:
                            started = True
                            break
            except Exception as e:
                last_error = str(e)
                await asyncio.sleep(0.1)  # Check every 100ms instead of 500ms

            # Check if process died
            if process.poll() is not None:
                # Process exited, read error from log
                try:
                    with open(log_file, 'r') as f:
                        error_output = f.read()
                    raise RuntimeError(f"Server process exited with code {process.returncode}. Log: {error_output}")
                except:
                    raise RuntimeError(f"Server process exited with code {process.returncode}")

        if not started:
            process.terminate()
            try:
                process.wait(timeout=2)
            except:
                process.kill()
            raise RuntimeError(f"Server failed to start after {max_wait} attempts. Last error: {last_error}")

        yield test_port, process

        # Cleanup
        try:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        except:
            pass

    @pytest_asyncio.fixture
    async def browser_page(self):
        """Create a Playwright browser page."""
        if not PLAYWRIGHT_AVAILABLE:
            pytest.skip("Playwright not available")

        async with async_playwright() as p:
            # Try to launch browser - use system Chrome/Edge if Playwright browser not available
            browser = None
            error_msgs = []

            # Try system Chrome first (most reliable)
            for attempt_name, launch_func in [
                ("system Chrome", lambda: p.chromium.launch(channel="chrome", headless=True)),
                ("system Edge", lambda: p.chromium.launch(channel="msedge", headless=True)),
                ("Playwright Chromium", lambda: p.chromium.launch(headless=True)),
            ]:
                try:
                    browser = await launch_func()
                    break
                except Exception as e:
                    error_msgs.append(f"{attempt_name}: {str(e)}")
                    continue

            if not browser:
                raise RuntimeError(
                    f"Failed to launch any browser. Tried:\n" + "\n".join(error_msgs) +
                    "\n\nTo fix: Install system Chrome/Edge, or run: python -m playwright install chromium"
                )

            context = await browser.new_context()
            page = await context.new_page()

            yield page

            await browser.close()

    @pytest.mark.asyncio
    @pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="Playwright not installed")
    async def test_log_message_with_dir_field_displays_badge(self, server_process, browser_page):
        """Test that log messages with dir field display direction badges in UI."""
        port, server_proc = server_process
        page = browser_page

        run_id = f"e2e-test-{int(time.time())}"
        test_case_id = "E2ETest.DirectionBadge"

        # Step 1: Send WebSocket messages simulating NUnit client
        async with ClientSession() as session:
            async with session.ws_connect(f"ws://127.0.0.1:{port}/ws/nunit") as ws:
                # Send run_started (server generates run_id)
                await ws.send_json({
                    "type": "run_started",
                    "user_metadata": {},
                    "retention_days": 1,
                    "local_run": False
                })

                # Wait for server response with run_id
                response = await ws.receive_json()
                if response.get("type") == "run_started_response":
                    run_id = response.get("run_id")
                else:
                    pytest.fail(f"Unexpected response: {response}")

                # Send test_case_started
                await ws.send_json({
                    "type": "test_case_started",
                    "run_id": run_id,
                    "test_case_id": test_case_id
                })

                # Send log_batch with dir field
                await ws.send_json({
                    "type": "log_batch",
                    "run_id": run_id,
                    "test_case_id": test_case_id,
                    "entries": [
                        {
                            "timestamp": datetime.now(UTC).isoformat().replace('+00:00', '') + "Z",
                            "message": "AT+TEST=1",
                            "dir": "tx",
                            "component": "TestDevice",
                            "channel": "COM1"
                        },
                        {
                            "timestamp": datetime.now(UTC).isoformat().replace('+00:00', '') + "Z",
                            "message": "OK",
                            "dir": "rx",
                            "component": "TestDevice",
                            "channel": "COM1"
                        }
                    ]
                })

                # Send test_case_finished
                await ws.send_json({
                    "type": "test_case_finished",
                    "run_id": run_id,
                    "test_case_id": test_case_id,
                    "status": "passed"
                })

                # Wait a bit for server to process (reduced from 0.5s)
                await asyncio.sleep(0.2)

        # Step 2: Open the test case log page in browser
        url = f"http://127.0.0.1:{port}/testRun/{run_id}/log/{test_case_id}.html"
        await page.goto(url, wait_until="domcontentloaded", timeout=5000)

        # Step 3: Verify UI displays direction badges
        # Wait for the log table to load
        await page.wait_for_selector("#msg_table tbody tr", timeout=5000)

        # Check for TX badge (Host ――► DUT)
        tx_badge = page.locator("text=Host ――► DUT")
        await expect(tx_badge).to_be_visible()

        # Check for RX badge (Host ◄―― DUT)
        rx_badge = page.locator("text=Host ◄―― DUT")
        await expect(rx_badge).to_be_visible()

        # Verify the messages are displayed
        await expect(page.locator("text=AT+TEST=1")).to_be_visible()
        await expect(page.locator("text=OK")).to_be_visible()

        # Verify component/channel badges are displayed (scope to table; use .first to avoid strict-mode ambiguity)
        await expect(page.locator("#msg_table").get_by_text("TestDevice").first).to_be_visible()
        await expect(page.locator("#msg_table").get_by_text("COM1").first).to_be_visible()

        # Verify spacing between badge and message (should have a space)
        # Check that the HTML contains the space between badge and message
        table_html = await page.locator("#msg_table").inner_html()
        # The HTML should contain the badge followed by a space and the message
        assert "Host ――► DUT" in table_html
        assert "Host ◄―― DUT" in table_html

    @pytest.mark.asyncio
    @pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="Playwright not installed")
    async def test_exception_message_displays_in_ui(self, server_process, browser_page):
        """Test that exception messages display correctly in the UI."""
        port, server_proc = server_process
        page = browser_page

        run_id = f"e2e-exception-{int(time.time())}"
        test_case_id = "E2ETest.ExceptionDisplay"

        # Step 1: Send WebSocket messages with exception
        async with ClientSession() as session:
            async with session.ws_connect(f"ws://127.0.0.1:{port}/ws/nunit") as ws:
                # Send run_started (server generates run_id)
                await ws.send_json({
                    "type": "run_started",
                    "user_metadata": {},
                    "retention_days": 1,
                    "local_run": False
                })

                # Wait for server response with run_id
                response = await ws.receive_json()
                if response.get("type") == "run_started_response":
                    run_id = response.get("run_id")
                else:
                    pytest.fail(f"Unexpected response: {response}")

                # Send test_case_started
                await ws.send_json({
                    "type": "test_case_started",
                    "run_id": run_id,
                    "test_case_id": test_case_id
                })

                # Send exception message
                await ws.send_json({
                    "type": "exception",
                    "run_id": run_id,
                    "test_case_id": test_case_id,
                    "timestamp": datetime.now(UTC).isoformat().replace('+00:00', '') + "Z",
                    "message": "Test assertion failed",
                    "exception_type": "NUnit.Framework.AssertionException",
                    "stack_trace": [
                        "at E2ETest.ExceptionDisplay() in ExampleTests.cs:line 42",
                        "at NUnit.Framework.Internal.Commands.TestMethodCommand.Execute(TestExecutionContext context)"
                    ],
                    "is_error": False
                })

                # Send test_case_finished
                await ws.send_json({
                    "type": "test_case_finished",
                    "run_id": run_id,
                    "test_case_id": test_case_id,
                    "status": "failed"
                })

                await asyncio.sleep(0.5)

        # Step 2: Open the test case log page
        url = f"http://127.0.0.1:{port}/testRun/{run_id}/log/{test_case_id}.html"
        await page.goto(url, wait_until="domcontentloaded", timeout=10000)

        # Step 3: Verify exception is displayed
        # Wait for stack trace section (note: ID is stackTraceList, not stack_trace_list)
        await page.wait_for_selector("#stackTraceList", timeout=5000)

        # Check that exception message is visible (scope to stack trace panel to avoid strict-mode ambiguity)
        await expect(page.locator("#stackTraceList").get_by_text("Test assertion failed")).to_be_visible()

        # Check that exception type is visible (scope to stack trace panel)
        await expect(page.locator("#stackTraceList").get_by_text("NUnit.Framework.AssertionException")).to_be_visible()

        # Check that stack trace lines are visible
        await expect(page.locator("#stackTraceList").get_by_text("at E2ETest.ExceptionDisplay")).to_be_visible()

    @pytest.mark.asyncio
    @pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="Playwright not installed")
    async def test_exception_is_error_flag_displays_correctly(self, server_process, browser_page):
        """Test that is_error flag distinguishes Error from Failure in UI."""
        port, server_proc = server_process
        page = browser_page

        run_id = f"e2e-is-error-{int(time.time())}"
        test_case_id = "E2ETest.IsErrorFlag"

        # Step 1: Send exception with is_error=true
        async with ClientSession() as session:
            async with session.ws_connect(f"ws://127.0.0.1:{port}/ws/nunit") as ws:
                # Send run_started (server generates run_id)
                await ws.send_json({
                    "type": "run_started",
                    "user_metadata": {},
                    "retention_days": 1,
                    "local_run": False
                })

                # Wait for server response with run_id
                response = await ws.receive_json()
                if response.get("type") == "run_started_response":
                    run_id = response.get("run_id")
                else:
                    pytest.fail(f"Unexpected response: {response}")

                await ws.send_json({
                    "type": "test_case_started",
                    "run_id": run_id,
                    "test_case_id": test_case_id
                })

                # Send exception with is_error=true (runtime error)
                await ws.send_json({
                    "type": "exception",
                    "run_id": run_id,
                    "test_case_id": test_case_id,
                    "timestamp": datetime.now(UTC).isoformat().replace('+00:00', '') + "Z",
                    "message": "Unexpected runtime error",
                    "exception_type": "System.Exception",
                    "stack_trace": ["at E2ETest.IsErrorFlag() in ExampleTests.cs:line 42"],
                    "is_error": True
                })

                await ws.send_json({
                    "type": "test_case_finished",
                    "run_id": run_id,
                    "test_case_id": test_case_id,
                    "status": "failed"
                })

                await asyncio.sleep(0.5)

        # Step 2: Open the page
        url = f"http://127.0.0.1:{port}/testRun/{run_id}/log/{test_case_id}.html"
        await page.goto(url, wait_until="domcontentloaded", timeout=10000)

        # Step 3: Verify that is_error=true shows exception type (not "Failure")
        await page.wait_for_selector("#stackTraceList", timeout=5000)

        # The exception card title should show the exception_type when is_error=true
        # Since exception_type is "System.Exception", it should show that (not "Failure")
        # Scope to stack trace panel to avoid matching the log table too.
        error_title = page.locator("#stackTraceList").locator(".stack-trace-title", has_text="System.Exception")
        await expect(error_title).to_be_visible()

        # Should NOT show "Failure" title (since is_error=true)
        failure_title = page.locator("#stackTraceList").locator(".stack-trace-title", has_text="Failure")
        await expect(failure_title).not_to_be_visible()

        # Verify the exception message is visible
        await expect(page.locator("#stackTraceList").get_by_text("Unexpected runtime error")).to_be_visible()
