import asyncio
import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import msgpack
import pytest

from testrift_server import config, database
from testrift_server.database import TestResultsDatabase
from testrift_server.protocol import F_ERROR, F_RUN_ID, F_TARGET_SETUP_STATE, F_TARGET_URL
from testrift_server.run_ingestion import ingest_run_context
from testrift_server.websocket import WebSocketServer


@pytest.fixture
def test_database():
    temporary_directory = tempfile.mkdtemp()
    database = TestResultsDatabase(str(Path(temporary_directory) / "test.db"))
    try:
        yield database
    finally:
        shutil.rmtree(temporary_directory)


@pytest.mark.asyncio
async def test_direct_and_prepared_runs_persist_the_same_context(test_database):
    context = {
        "target_key": "nora-b26x",
        "purpose": "nightly",
        "sources": {
            "firmware": {"branch": "main", "revision": "firmware-sha"},
            "test-system": {"branch": "development", "revision": "test-system-sha"},
        },
    }

    prepared = await ingest_run_context(
        test_database,
        run_id="prepared-run",
        context=context,
        status="preparing",
        retention_days=7,
        local_run=False,
        user_metadata={},
        run_name="Nightly",
    )
    direct = await ingest_run_context(
        test_database,
        run_id="direct-run",
        context=context,
        status="running",
        retention_days=7,
        local_run=False,
        user_metadata={},
        run_name="Nightly 1",
    )

    assert prepared.target_setup_state == "needs_setup"
    assert prepared.target_key == direct.target_key
    assert prepared.sources == direct.sources
    assert prepared.target_url == "/targets/nora-b26x"
    assert prepared.collection_urls == []

    async with test_database.get_connection() as connection:
        target_count = await (
            await connection.execute("SELECT COUNT(*) FROM targets WHERE key = ?", ("nora-b26x",))
        ).fetchone()
        source_rows = await (
            await connection.execute(
                "SELECT run_id, source_role, branch, revision FROM run_sources ORDER BY run_id, source_role"
            )
        ).fetchall()

    assert target_count == (1,)
    assert source_rows == [
        ("direct-run", "firmware", "main", "firmware-sha"),
        ("direct-run", "test-system", "development", "test-system-sha"),
        ("prepared-run", "firmware", "main", "firmware-sha"),
        ("prepared-run", "test-system", "development", "test-system-sha"),
    ]


@pytest.mark.asyncio
async def test_conflicting_prepared_activation_is_rejected_without_consuming_run(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "runs")
    database.initialize_database(tmp_path)
    server = WebSocketServer()
    context = {
        "target_key": "nora-b26x",
        "purpose": "nightly",
        "sources": {"firmware": {"branch": "main", "revision": "firmware-sha"}},
    }

    prepare_socket = AsyncMock()
    await server._handle_run_prepare(prepare_socket, context)
    prepare_response = msgpack.unpackb(prepare_socket.send_bytes.await_args.args[0], raw=False)
    prepared_run_id = prepare_response[F_RUN_ID]
    assert prepare_response[F_TARGET_URL] == "/targets/nora-b26x"
    assert prepare_response[F_TARGET_SETUP_STATE] == "needs_setup"

    activation_socket = AsyncMock()
    await server._handle_run_started(
        activation_socket,
        {**context, "run_id": prepared_run_id, "target_key": "other-target"},
        {},
    )
    activation_response = msgpack.unpackb(activation_socket.send_bytes.await_args.args[0], raw=False)
    assert "conflicts" in activation_response[F_ERROR]
    assert prepared_run_id in server.prepared_runs
