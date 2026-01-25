"""
TestRift Server - Main entry point.

A real-time test logging system for NUnit tests.
"""

import argparse
import asyncio
import logging
import sys
import time

from aiohttp import web

from .config import (
    CONFIG,
    CONFIG_PATH_USED,
    PORT,
    DATA_DIR,
    DEFAULT_RETENTION_DAYS,
    LOCALHOST_ONLY,
    ATTACHMENTS_ENABLED,
    ATTACHMENT_MAX_SIZE,
    get_config_hash,
    get_running_server_info,
    request_running_server_shutdown,
)
from .handlers import get_routes as get_handler_routes, log_event
from .api_handlers import get_routes as get_api_routes
from .websocket import WebSocketServer
from .cleanup import (
    cleanup_runs_sweep,
    cleanup_abandoned_running_runs,
    cleanup_old_runs,
)
from . import database


# Configure logging with timestamps
root_logger = logging.getLogger()
for handler in root_logger.handlers[:]:
    root_logger.removeHandler(handler)

formatter = logging.Formatter(
    '%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
handler = logging.StreamHandler()
handler.setFormatter(formatter)
root_logger.addHandler(handler)
root_logger.setLevel(logging.INFO)

logger = logging.getLogger(__name__)


# --- Main app setup ---

app = web.Application()
ws_server = WebSocketServer()

app["ws_server"] = ws_server

# Collect routes from all modules
routes = []
routes.extend(get_handler_routes())
routes.extend(get_api_routes())
routes.append(web.get("/ws/{tail:.*}", ws_server.handle_ws))

app.add_routes(routes)


async def on_startup(app):
    """Application startup handler."""
    # Initialize database with configured data directory
    try:
        database.initialize_database(DATA_DIR)
        await database.db.initialize()
        log_event("database_initialized")
    except Exception as e:
        log_event("database_init_error", error=str(e))

    # Run an immediate cleanup sweep at startup
    try:
        await cleanup_runs_sweep()
        await cleanup_abandoned_running_runs()
    except Exception as e:
        log_event("startup_cleanup_error", error=str(e))

    app["cleanup_task"] = asyncio.create_task(cleanup_old_runs())


async def on_cleanup(app):
    """Application cleanup handler."""
    app["cleanup_task"].cancel()
    try:
        await app["cleanup_task"]
    except asyncio.CancelledError:
        pass


app.on_startup.append(on_startup)
app.on_cleanup.append(on_cleanup)


def main(argv=None):
    """Main entry point for the server."""
    # Reconfigure logging at the start of main() to ensure our format is used
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    formatter = logging.Formatter(
        '%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)

    parser = argparse.ArgumentParser(prog="testrift-server")
    parser.add_argument(
        "--restart-on-config",
        action="store_true",
        help="If a server is already running on the configured port with a different config, "
             "ask it to shut down and then start with the new config.",
    )
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    # Determine host based on configuration
    host = "127.0.0.1" if LOCALHOST_ONLY else "0.0.0.0"

    # Detect already-running server on the configured port.
    new_hash = get_config_hash(CONFIG)
    try:
        running = get_running_server_info(PORT)
    except RuntimeError as e:
        logger.error(f" {e}")
        return 2

    if running is not None:
        running_hash = running.get("config_hash")
        if running_hash == new_hash:
            logger.info(f"TestRift server already running on 127.0.0.1:{PORT} with identical config. Exiting.")
            return 0

        logger.error(f" TestRift server already running on 127.0.0.1:{PORT} but config differs.")
        logger.info(f"  running config_path: {running.get('config_path')}")
        logger.info(f"  running config_hash: {running_hash}")
        logger.info(f"  new     config_path: {str(CONFIG_PATH_USED) if CONFIG_PATH_USED else None}")
        logger.info(f"  new     config_hash: {new_hash}")
        if args.restart_on_config and running_hash:
            logger.info("Attempting to restart running server with new config...")
            if not request_running_server_shutdown(PORT, running_hash):
                logger.info("ERROR: Failed to request shutdown of running server.")
                return 2

            # Wait for the running server to exit and release the port.
            deadline = time.time() + 5.0
            while time.time() < deadline:
                if get_running_server_info(PORT) is None:
                    break
                time.sleep(0.2)
            else:
                logger.info("ERROR: Timed out waiting for running server to shut down.")
                return 2

            logger.info("Old server stopped. Starting new server...")
        else:
            return 2

    logger.info(f"Starting server on {host}:{PORT}")
    logger.info(f"Default retention days: {DEFAULT_RETENTION_DAYS}")
    logger.info(f"Data directory: {DATA_DIR}")
    logger.info(f"Localhost only: {LOCALHOST_ONLY}")
    logger.info(f"Attachments enabled: {ATTACHMENTS_ENABLED}")
    if ATTACHMENTS_ENABLED:
        max_size_mb = ATTACHMENT_MAX_SIZE // (1024 * 1024)
        logger.info(f"Max attachment size: {max_size_mb}MB")

    def _runner_print(*args):
        """Route aiohttp runner banner through the logger for timestamps."""
        message = " ".join(str(arg) for arg in args)
        logger.info(message)

    web.run_app(app, host=host, port=PORT, print=_runner_print)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
