import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio

from testrift_server import database
from testrift_server.database import TestResultsDatabase, TestRunData


@pytest_asyncio.fixture
async def initialized_database():
    temp_dir = tempfile.mkdtemp()
    database_path = Path(temp_dir) / "test.db"
    test_database = TestResultsDatabase(str(database_path))
    await test_database.initialize()
    try:
        yield test_database
    finally:
        shutil.rmtree(temp_dir)


@pytest.mark.asyncio
async def test_fresh_schema_has_target_collection_and_run_source_tables(initialized_database):
    async with initialized_database.get_connection() as connection:
        tables = await (
            await connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        ).fetchall()
        table_names = {row[0] for row in tables}
        assert {
            "targets",
            "collections",
            "collection_targets",
            "summary_profiles",
            "summary_profile_sources",
            "run_sources",
        }.issubset(table_names)

        columns = await (await connection.execute("PRAGMA table_info(test_runs)")).fetchall()
        column_names = {column[1] for column in columns}
        assert {"target_key", "purpose", "parent_run_id"}.issubset(column_names)
        assert not {"group_name", "group_hash"}.intersection(column_names)


@pytest.mark.asyncio
async def test_target_collection_profile_and_run_sources_are_constrained(initialized_database):
    first_target = await initialized_database.get_or_create_target("nora-b26x")
    second_target = await initialized_database.get_or_create_target("sara-b26x")
    collection_id = await initialized_database.create_collection("u-connectxpress", "u-connectXpress")

    await initialized_database.replace_collection_membership(
        collection_id,
        [first_target["id"], second_target["id"]],
    )
    profile_id = await initialized_database.create_summary_profile(
        collection_id,
        "nightly-main",
        "nightly",
        24,
        is_primary=True,
    )
    await initialized_database.replace_summary_profile_sources(
        profile_id,
        [("firmware", "main", None)],
    )

    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"
    run = TestRunData(
        run_id="run-1",
        status="finished",
        start_time=now,
        end_time=now,
        retention_days=7,
        local_run=False,
        target_key="nora-b26x",
        purpose="nightly",
    )
    assert await initialized_database.insert_test_run(
        run,
        sources={"firmware": {"branch": "main", "revision": "abc123"}},
    )

    async with initialized_database.get_connection() as connection:
        source = await (
            await connection.execute(
                "SELECT source_role, branch, revision FROM run_sources WHERE run_id = ?",
                ("run-1",),
            )
        ).fetchone()
        assert source == ("firmware", "main", "abc123")

    with pytest.raises(sqlite3.IntegrityError):
        await initialized_database.create_summary_profile(
            collection_id,
            "another-primary",
            "nightly",
            24,
            is_primary=True,
        )

    async with initialized_database.get_connection() as connection:
        await connection.execute("DELETE FROM collections WHERE id = ?", (collection_id,))
        await connection.commit()
        assert await (
            await connection.execute("SELECT COUNT(*) FROM collection_targets")
        ).fetchone() == (0,)
        assert await (
            await connection.execute("SELECT COUNT(*) FROM summary_profiles")
        ).fetchone() == (0,)
