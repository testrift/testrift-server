"""
TestRift Server - Main entry point.

A real-time test logging system for NUnit tests.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, WebSocket

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
from .tls_certs import (
    current_material,
    ingest_tls_enabled,
    setup_tls,
    ui_tls_enabled,
)
from .handlers import get_routes as get_handler_routes, log_event
from .api_handlers import get_routes as get_api_routes
from .http_compat import convert_aiohttp_path, wrap_handler
from .websocket import WebSocketServer
from .cleanup import (
    cleanup_runs_sweep,
    cleanup_abandoned_running_runs,
    cleanup_old_runs,
)
from . import database
from .log_buffer import log_buffer


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
handler.setLevel(logging.INFO)
root_logger.addHandler(handler)
root_logger.setLevel(logging.DEBUG)

# Keep library chatter out of the /logs ring buffer (capacity is finite).
logging.getLogger("aiosqlite").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)

# Add in-memory ring buffer handler for the /logs page
buf_formatter = logging.Formatter('%(message)s')
log_buffer.setFormatter(buf_formatter)
log_buffer.setLevel(logging.DEBUG)
root_logger.addHandler(log_buffer)

logger = logging.getLogger(__name__)


def _register_http_routes(app: FastAPI) -> None:
    """Register HTTP routes from handler modules."""
    for methods, path, handler in list(get_handler_routes()) + list(get_api_routes()):
        app.add_api_route(
            convert_aiohttp_path(path),
            wrap_handler(handler),
            methods=list(methods),
            name=getattr(handler, "__name__", None),
        )


def _register_websocket_routes(app: FastAPI, ws_server: WebSocketServer) -> None:
    """Register MessagePack WebSocket endpoints."""

    @app.websocket("/ws/nunit")
    async def ws_nunit(websocket: WebSocket):
        await ws_server.accept_and_route(websocket, "/ws/nunit")

    @app.websocket("/ws/ui")
    async def ws_ui(websocket: WebSocket):
        await ws_server.accept_and_route(websocket, "/ws/ui")

    @app.websocket("/ws/logs/{run_id}/{test_case_id}")
    async def ws_logs(websocket: WebSocket, run_id: str, test_case_id: str):
        await ws_server.accept_and_route(websocket, f"/ws/logs/{run_id}/{test_case_id}")


def create_app(ws_server: WebSocketServer | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    ws_server = ws_server or WebSocketServer()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            database.initialize_database(DATA_DIR)
            await database.db.initialize()
            from .auth import bootstrap_admin_if_needed
            await bootstrap_admin_if_needed()
            log_event("database_initialized")
        except Exception as e:
            log_event("database_init_error", level="error", error=str(e))

        try:
            await cleanup_runs_sweep()
            await cleanup_abandoned_running_runs()
        except Exception as e:
            log_event("startup_cleanup_error", level="error", error=str(e))

        cleanup_task = asyncio.create_task(cleanup_old_runs())
        app.state.cleanup_task = cleanup_task
        await ws_server.start_prepared_runs_cleanup()

        try:
            yield
        finally:
            await ws_server.close_all_connections()
            cleanup_task.cancel()
            try:
                await cleanup_task
            except asyncio.CancelledError:
                pass
            await ws_server.stop_prepared_runs_cleanup()

    app = FastAPI(title="TestRift Server", lifespan=lifespan)
    app.state.ws_server = ws_server
    # Dict-like access for helpers that still use app["ws_server"] in tests via CompatRequest
    _register_http_routes(app)
    _register_websocket_routes(app, ws_server)
    return app


# Module-level app for `uvicorn testrift_server.tr_server:app`
ws_server = WebSocketServer()
app = create_app(ws_server=ws_server)


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
    handler.setLevel(logging.INFO)
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.DEBUG)

    # Keep library chatter out of the /logs ring buffer (capacity is finite).
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    # Re-add ring buffer handler (removed above with the old handlers)
    log_buffer.clear()
    buf_formatter = logging.Formatter('%(message)s')
    log_buffer.setFormatter(buf_formatter)
    log_buffer.setLevel(logging.DEBUG)
    root_logger.addHandler(log_buffer)

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

    try:
        setup_tls(CONFIG)
    except Exception as e:
        logger.error(f"TLS setup failed: {e}")
        return 2

    ingest_port = CONFIG.get("ingest_port")
    ui_tls = ui_tls_enabled(CONFIG)
    ingest_tls = ingest_tls_enabled(CONFIG)
    if ingest_tls and ingest_port:
        logger.info(f"Ingest HTTPS listener on {host}:{ingest_port}")
    elif ingest_tls:
        logger.info("Ingest HTTPS shares the UI listener")

    _run_listeners(host, ui_tls=ui_tls, ingest_tls=ingest_tls, ingest_port=ingest_port)
    return 0


def _ssl_kwargs():
    material = current_material()
    if material is None:
        raise RuntimeError("TLS is enabled but no certificate material is loaded")
    return {
        "ssl_certfile": str(material.chain_path),
        "ssl_keyfile": str(material.key_path),
    }


def _uvicorn_config(host: str, port: int, *, ssl: bool, lifespan: str = "auto") -> uvicorn.Config:
    kwargs = {
        "app": app,
        "host": host,
        "port": port,
        "log_level": "info",
        "access_log": False,
        "timeout_graceful_shutdown": 5,
        "lifespan": lifespan,
    }
    if ssl:
        kwargs.update(_ssl_kwargs())
    return uvicorn.Config(**kwargs)


def _run_listeners(host: str, *, ui_tls: bool, ingest_tls: bool, ingest_port: int | None) -> None:
    configs = [_uvicorn_config(host, PORT, ssl=ui_tls, lifespan="auto")]
    if ingest_tls and ingest_port:
        configs.append(_uvicorn_config(host, ingest_port, ssl=True, lifespan="off"))
    if len(configs) == 1:
        uvicorn.Server(configs[0]).run()
        return

    async def _serve_all():
        servers = []
        for index, cfg in enumerate(configs):
            server = uvicorn.Server(cfg)
            if index > 0:
                server.install_signal_handlers = False
            servers.append(server)
        tasks = [asyncio.create_task(server.serve()) for server in servers]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for server in servers:
            server.should_exit = True
        await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(_serve_all())


if __name__ == "__main__":
    raise SystemExit(main())
