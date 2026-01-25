"""
SQLite database module for test results storage and analysis.
Provides functionality to store and query test runs, test cases, and user metadata.
"""

import sqlite3
import json
import asyncio
from datetime import datetime, timedelta, UTC
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from contextlib import asynccontextmanager
import aiosqlite


@dataclass
class TestRunData:  # pytest: disable=collection
    __test__ = False  # Tell pytest to ignore this class
    """Represents a test run in the database."""
    run_id: str
    status: str
    start_time: str
    end_time: Optional[str]
    retention_days: Optional[int]
    local_run: bool
    dut: str = "TestDevice-001"
    run_name: Optional[str] = None
    group_name: Optional[str] = None
    group_hash: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class TestCaseData:  # pytest: disable=collection
    __test__ = False  # Tell pytest to ignore this class
    """Represents a test case in the database."""
    id: int
    run_id: str
    tc_full_name: str
    tc_id: Optional[str]
    status: str
    start_time: str
    end_time: Optional[str]
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class UserMetadata:
    """Represents user metadata for a test run."""
    id: int
    run_id: str
    key: str
    value: str
    url: Optional[str] = None
    created_at: Optional[str] = None


class TestResultsDatabase:
    """SQLite database for test results storage and analysis."""

    def __init__(self, db_path: str = "test_results.db"):
        self.db_path = db_path
        self._initialized = False

    async def initialize(self):
        """Initialize the database with required tables."""
        if self._initialized:
            return

        async with aiosqlite.connect(self.db_path) as db:
            # Enable foreign key constraints
            await db.execute("PRAGMA foreign_keys = ON")

            # Create test_runs table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS test_runs (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT,
                    retention_days INTEGER,
                    local_run BOOLEAN NOT NULL DEFAULT 0,
                    dut TEXT NOT NULL DEFAULT 'TestDevice-001',
                    run_name TEXT,
                    group_name TEXT,
                    group_hash TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Ensure new columns exist for legacy databases
            cursor = await db.execute("PRAGMA table_info(test_runs)")
            columns = await cursor.fetchall()
            column_names = {col[1] for col in columns}
            if "run_name" not in column_names:
                await db.execute("ALTER TABLE test_runs ADD COLUMN run_name TEXT")
            if "group_name" not in column_names:
                await db.execute("ALTER TABLE test_runs ADD COLUMN group_name TEXT")
            if "group_hash" not in column_names:
                await db.execute("ALTER TABLE test_runs ADD COLUMN group_hash TEXT")

            # Create test_cases table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS test_cases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    tc_full_name TEXT NOT NULL,
                    tc_id TEXT,
                    status TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (run_id) REFERENCES test_runs (run_id) ON DELETE CASCADE,
                    UNIQUE (run_id, tc_full_name)
                )
            """)

            # Create user_metadata table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS user_metadata (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    url TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (run_id) REFERENCES test_runs (run_id) ON DELETE CASCADE,
                    UNIQUE (run_id, key)
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS group_metadata (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    url TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (run_id) REFERENCES test_runs (run_id) ON DELETE CASCADE,
                    UNIQUE (run_id, key)
                )
            """)

            # Create indexes for better query performance
            await db.execute("CREATE INDEX IF NOT EXISTS idx_test_runs_status ON test_runs (status)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_test_runs_start_time ON test_runs (start_time)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_test_runs_group_hash ON test_runs (group_hash)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_test_cases_run_id ON test_cases (run_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_test_cases_status ON test_cases (status)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_user_metadata_run_id ON user_metadata (run_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_user_metadata_key ON user_metadata (key)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_group_metadata_run_id ON group_metadata (run_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_group_metadata_key ON group_metadata (key)")

            await db.commit()

        self._initialized = True

    @asynccontextmanager
    async def get_connection(self):
        """Get a database connection with proper initialization."""
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            yield db

    async def insert_test_run(
        self,
        test_run: TestRunData,
        user_metadata: Dict[str, Any] = None,
        group_metadata: Dict[str, Any] = None
    ) -> bool:
        """Insert a new test run into the database."""
        async with self.get_connection() as db:
            try:
                # Insert test run
                await db.execute("""
                    INSERT OR REPLACE INTO test_runs
                    (run_id, status, start_time, end_time, retention_days, local_run, dut, run_name, group_name, group_hash, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    test_run.run_id,
                    test_run.status,
                    test_run.start_time,
                    test_run.end_time,
                    test_run.retention_days,
                    test_run.local_run,
                    test_run.dut,
                    test_run.run_name,
                    test_run.group_name,
                    test_run.group_hash,
                    datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"
                ))

                # Insert user metadata if provided
                if user_metadata:
                    for key, meta_value in user_metadata.items():
                        value = meta_value.get("value", "") if isinstance(meta_value, dict) else str(meta_value)
                        url = meta_value.get("url") if isinstance(meta_value, dict) else None

                        await db.execute("""
                            INSERT OR REPLACE INTO user_metadata (run_id, key, value, url)
                            VALUES (?, ?, ?, ?)
                        """, (test_run.run_id, key, value, url))

                # Insert group metadata if provided
                if group_metadata:
                    for key, meta_value in group_metadata.items():
                        value = meta_value.get("value", "") if isinstance(meta_value, dict) else str(meta_value)
                        url = meta_value.get("url") if isinstance(meta_value, dict) else None

                        await db.execute("""
                            INSERT OR REPLACE INTO group_metadata (run_id, key, value, url)
                            VALUES (?, ?, ?, ?)
                        """, (test_run.run_id, key, value, url))

                await db.commit()
                return True
            except Exception as e:
                print(f"Error inserting test run: {e}")
                await db.rollback()
                return False

    async def update_test_run(self, run_id: str, **updates) -> bool:
        """Update an existing test run."""
        async with self.get_connection() as db:
            try:
                # Build dynamic update query
                set_clauses = []
                values = []

                for key, value in updates.items():
                    if key in ['status', 'end_time']:
                        set_clauses.append(f"{key} = ?")
                        values.append(value)

                if set_clauses:
                    set_clauses.append("updated_at = ?")
                    values.append(datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z")
                    values.append(run_id)

                    await db.execute(f"""
                        UPDATE test_runs
                        SET {', '.join(set_clauses)}
                        WHERE run_id = ?
                    """, values)

                    await db.commit()
                    return True
                return False
            except Exception as e:
                print(f"Error updating test run: {e}")
                await db.rollback()
                return False

    async def insert_test_case(self, test_case: TestCaseData) -> bool:
        """Insert a new test case into the database."""
        async with self.get_connection() as db:
            try:
                await db.execute("""
                    INSERT OR REPLACE INTO test_cases
                    (run_id, tc_full_name, tc_id, status, start_time, end_time, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    test_case.run_id,
                    test_case.tc_full_name,
                    test_case.tc_id,
                    test_case.status,
                    test_case.start_time,
                    test_case.end_time,
                    datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"
                ))

                await db.commit()
                return True
            except Exception as e:
                print(f"Error inserting test case: {e}")
                await db.rollback()
                return False

    async def get_test_runs(
        self,
        limit: int = 100,
        offset: int = 0,
        status_filter: Optional[str] = None,
        metadata_filters: Optional[Dict[str, str]] = None,
        group_hash: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get test runs with optional filtering."""
        async with self.get_connection() as db:
            # Build query with joins for metadata filtering
            # Note: user_metadata JOIN removed to prevent duplicate rows inflating counts
            # Metadata filtering is handled via EXISTS subqueries in WHERE clause
            query = """
                SELECT tr.*,
                       COUNT(tc.id) as test_case_count,
                       SUM(CASE WHEN tc.status = 'passed' THEN 1 ELSE 0 END) as passed_count,
                       SUM(CASE WHEN tc.status = 'failed' THEN 1 ELSE 0 END) as failed_count,
                       SUM(CASE WHEN tc.status = 'skipped' THEN 1 ELSE 0 END) as skipped_count,
                       SUM(CASE WHEN tc.status = 'aborted' THEN 1 ELSE 0 END) as aborted_count,
                       SUM(CASE WHEN tc.status = 'error' THEN 1 ELSE 0 END) as error_count
                FROM test_runs tr
                LEFT JOIN test_cases tc ON tr.run_id = tc.run_id
            """

            conditions = []
            params = []

            if status_filter:
                conditions.append("tr.status = ?")
                params.append(status_filter)

            if metadata_filters:
                for key, value in metadata_filters.items():
                    conditions.append("EXISTS (SELECT 1 FROM user_metadata um2 WHERE um2.run_id = tr.run_id AND um2.key = ? AND um2.value = ?)")
                    params.extend([key, value])

            if group_hash:
                conditions.append("tr.group_hash = ?")
                params.append(group_hash)

            if conditions:
                query += " WHERE " + " AND ".join(conditions)

            query += " GROUP BY tr.run_id ORDER BY tr.start_time DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()

            # Convert to list of dictionaries
            columns = [desc[0] for desc in cursor.description]
            return [dict(zip(columns, row)) for row in rows]

    async def get_run_names_starting_with(self, base_name: str, group_hash: str = None) -> List[str]:
        """Get all run_names that start with a given base name, optionally filtered by group."""
        async with self.get_connection() as db:
            if group_hash:
                cursor = await db.execute("""
                    SELECT run_name FROM test_runs
                    WHERE (run_name = ? OR run_name LIKE ?) AND group_hash = ?
                    ORDER BY run_name
                """, (base_name, f"{base_name} %", group_hash))
            else:
                cursor = await db.execute("""
                    SELECT run_name FROM test_runs
                    WHERE (run_name = ? OR run_name LIKE ?) AND group_hash IS NULL
                    ORDER BY run_name
                """, (base_name, f"{base_name} %"))

            rows = await cursor.fetchall()
            return [row[0] for row in rows if row[0]]

    async def get_test_run_by_id(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Get a single test run by ID."""
        async with self.get_connection() as db:
            query = """
                SELECT tr.*,
                       COUNT(tc.id) as test_case_count,
                       SUM(CASE WHEN tc.status = 'passed' THEN 1 ELSE 0 END) as passed_count,
                       SUM(CASE WHEN tc.status = 'failed' THEN 1 ELSE 0 END) as failed_count,
                       SUM(CASE WHEN tc.status = 'skipped' THEN 1 ELSE 0 END) as skipped_count,
                       SUM(CASE WHEN tc.status = 'aborted' THEN 1 ELSE 0 END) as aborted_count,
                       SUM(CASE WHEN tc.status = 'error' THEN 1 ELSE 0 END) as error_count
                FROM test_runs tr
                LEFT JOIN test_cases tc ON tr.run_id = tc.run_id
                WHERE tr.run_id = ?
                GROUP BY tr.run_id
            """

            cursor = await db.execute(query, (run_id,))
            row = await cursor.fetchone()

            if row:
                columns = [desc[0] for desc in cursor.description]
                return dict(zip(columns, row))
            return None

    async def get_test_cases_for_run(self, run_id: str) -> List[Dict[str, Any]]:
        """Get all test cases for a specific run."""
        async with self.get_connection() as db:
            cursor = await db.execute("""
                SELECT * FROM test_cases
                WHERE run_id = ?
                ORDER BY start_time
            """, (run_id,))

            rows = await cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
            return [dict(zip(columns, row)) for row in rows]

    async def get_test_results_for_runs(self, run_ids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
        """Get all test results for multiple runs efficiently."""
        if not run_ids:
            return {}

        placeholders = ','.join('?' * len(run_ids))
        async with self.get_connection() as db:
            cursor = await db.execute(f"""
                SELECT * FROM test_cases
                WHERE run_id IN ({placeholders})
                ORDER BY run_id, start_time
            """, run_ids)

            rows = await cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]

            # Group results by run_id
            results = {}
            for row in rows:
                row_dict = dict(zip(columns, row))
                run_id = row_dict['run_id']
                if run_id not in results:
                    results[run_id] = []
                results[run_id].append(row_dict)

            return results

    async def get_user_metadata_for_run(self, run_id: str) -> Dict[str, Any]:
        """Get user metadata for a specific run."""
        async with self.get_connection() as db:
            cursor = await db.execute("""
                SELECT key, value, url FROM user_metadata
                WHERE run_id = ?
            """, (run_id,))

            rows = await cursor.fetchall()
            metadata = {}
            for key, value, url in rows:
                metadata[key] = {"value": value, "url": url}
            return metadata

    async def get_group_metadata_for_run(self, run_id: str) -> Dict[str, Any]:
        """Get group metadata for a specific run."""
        async with self.get_connection() as db:
            cursor = await db.execute("""
                SELECT key, value, url FROM group_metadata
                WHERE run_id = ?
            """, (run_id,))

            rows = await cursor.fetchall()
            metadata = {}
            for key, value, url in rows:
                metadata[key] = {"value": value, "url": url}
            return metadata

    async def get_test_results_over_time(
        self,
        days_back: int = 30,
        metadata_filters: Optional[Dict[str, str]] = None,
        group_hash: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get test results aggregated over time for trending analysis."""
        async with self.get_connection() as db:
            # Calculate date threshold
            cutoff_date = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days_back)
            cutoff_str = cutoff_date.isoformat() + "Z"

            query = """
                SELECT
                    SUBSTR(tr.start_time, 1, 10) as date,
                    COUNT(DISTINCT tr.run_id) as total_runs,
                    SUM(CASE WHEN tc.status = 'passed' THEN 1 ELSE 0 END) as passed_tests,
                    SUM(CASE WHEN tc.status = 'failed' THEN 1 ELSE 0 END) as failed_tests,
                    SUM(CASE WHEN tc.status = 'skipped' THEN 1 ELSE 0 END) as skipped_tests,
                    SUM(CASE WHEN tc.status = 'aborted' THEN 1 ELSE 0 END) as aborted_tests,
                    SUM(CASE WHEN tc.status = 'error' THEN 1 ELSE 0 END) as error_tests,
                    SUM(CASE WHEN tc.status IN ('passed', 'failed', 'skipped', 'aborted', 'error') THEN 1 ELSE 0 END) as total_tests
                FROM test_runs tr
                LEFT JOIN test_cases tc ON tr.run_id = tc.run_id
                LEFT JOIN user_metadata um ON tr.run_id = um.run_id
            """

            conditions = ["tr.start_time >= ?"]
            params = [cutoff_str]

            if metadata_filters:
                for key, value in metadata_filters.items():
                    conditions.append("EXISTS (SELECT 1 FROM user_metadata um2 WHERE um2.run_id = tr.run_id AND um2.key = ? AND um2.value = ?)")
                    params.extend([key, value])

            if group_hash:
                conditions.append("tr.group_hash = ?")
                params.append(group_hash)

            query += " WHERE " + " AND ".join(conditions)
            query += " GROUP BY DATE(tr.start_time) ORDER BY date DESC"

            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()

            columns = [desc[0] for desc in cursor.description]
            return [dict(zip(columns, row)) for row in rows]

    async def get_test_runs_over_time(
        self,
        days_back: int = 30,
        metadata_filters: Optional[Dict[str, str]] = None,
        group_hash: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get individual test runs over time for trending analysis."""
        async with self.get_connection() as db:
            # Calculate date threshold
            cutoff_date = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days_back)
            cutoff_str = cutoff_date.isoformat() + "Z"

            query = """
                SELECT
                    tr.run_id,
                    tr.run_name,
                    tr.start_time,
                    tr.end_time,
                    tr.status,
                    COUNT(tc.id) as total_tests,
                    SUM(CASE WHEN tc.status = 'passed' THEN 1 ELSE 0 END) as passed_tests,
                    SUM(CASE WHEN tc.status = 'failed' THEN 1 ELSE 0 END) as failed_tests,
                    SUM(CASE WHEN tc.status = 'skipped' THEN 1 ELSE 0 END) as skipped_tests,
                    SUM(CASE WHEN tc.status = 'aborted' THEN 1 ELSE 0 END) as aborted_tests,
                    SUM(CASE WHEN tc.status = 'error' THEN 1 ELSE 0 END) as error_tests
                FROM test_runs tr
                LEFT JOIN test_cases tc ON tr.run_id = tc.run_id
            """

            conditions = ["tr.start_time >= ?", "tr.status = 'finished'"]
            params = [cutoff_str]

            if metadata_filters:
                for key, value in metadata_filters.items():
                    conditions.append("EXISTS (SELECT 1 FROM user_metadata um WHERE um.run_id = tr.run_id AND um.key = ? AND um.value = ?)")
                    params.extend([key, value])

            if group_hash:
                conditions.append("tr.group_hash = ?")
                params.append(group_hash)

            query += " WHERE " + " AND ".join(conditions)
            query += " GROUP BY tr.run_id, tr.start_time, tr.end_time, tr.status ORDER BY tr.start_time ASC"

            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()

            columns = [desc[0] for desc in cursor.description]
            return [dict(zip(columns, row)) for row in rows]

    async def get_test_case_history(
        self,
        tc_full_name: str,
        limit: int = 50,
        metadata_filters: Optional[Dict[str, str]] = None,
        group_hash: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get execution history for a specific test case."""
        async with self.get_connection() as db:
            query = """
                SELECT tc.id, tc.run_id, tc.tc_full_name, tc.tc_id, tc.status, tc.start_time, tc.end_time,
                       tr.start_time as run_start_time, tr.status as run_status, tr.run_name
                FROM test_cases tc
                JOIN test_runs tr ON tc.run_id = tr.run_id
            """

            conditions = ["tc.tc_full_name = ?"]
            params = [tc_full_name]

            if metadata_filters:
                for key, value in metadata_filters.items():
                    conditions.append("EXISTS (SELECT 1 FROM user_metadata um2 WHERE um2.run_id = tr.run_id AND um2.key = ? AND um2.value = ?)")
                    params.extend([key, value])

            if group_hash:
                conditions.append("tr.group_hash = ?")
                params.append(group_hash)

            query += " WHERE " + " AND ".join(conditions)
            query += " ORDER BY tc.start_time DESC LIMIT ?"
            params.append(limit)

            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()

            columns = [desc[0] for desc in cursor.description]
            return [dict(zip(columns, row)) for row in rows]

    async def get_unique_metadata_values(self, key: str) -> List[str]:
        """Get unique values for a specific metadata key."""
        async with self.get_connection() as db:
            cursor = await db.execute("""
                SELECT DISTINCT value FROM user_metadata
                WHERE key = ? AND value IS NOT NULL AND value != ''
                ORDER BY value
            """, (key,))

            rows = await cursor.fetchall()
            return [row[0] for row in rows]

    async def get_all_metadata_keys(self) -> List[str]:
        """Get all unique metadata keys."""
        async with self.get_connection() as db:
            cursor = await db.execute("""
                SELECT DISTINCT key FROM user_metadata
                WHERE key IS NOT NULL AND key != ''
                ORDER BY key
            """)

            rows = await cursor.fetchall()
            return [row[0] for row in rows]

    async def get_failed_test_cases(
        self,
        days_back: int = 30,
        limit: int = 100,
        group_hash: Optional[str] = None,
        metadata_filters: Optional[Dict[str, str]] = None
    ) -> List[Dict[str, Any]]:
        """Get failed test cases within a time range for failure analysis."""
        async with self.get_connection() as db:
            query = """
                SELECT tc.run_id, tc.tc_full_name, tc.tc_id, tc.status, tc.start_time, tc.end_time,
                       tr.start_time as run_start_time, tr.group_hash, tr.group_name
                FROM test_cases tc
                JOIN test_runs tr ON tc.run_id = tr.run_id
            """

            conditions = [
                "tc.status = 'failed'",
                "tr.start_time >= datetime('now', ?)"
            ]
            params = [f"-{days_back} days"]

            if group_hash:
                conditions.append("tr.group_hash = ?")
                params.append(group_hash)

            if metadata_filters:
                for key, value in metadata_filters.items():
                    conditions.append("""
                        EXISTS (SELECT 1 FROM user_metadata um
                                WHERE um.run_id = tr.run_id AND um.key = ? AND um.value = ?)
                    """)
                    params.extend([key, value])

            query += " WHERE " + " AND ".join(conditions)
            query += " ORDER BY tc.start_time DESC LIMIT ?"
            params.append(limit)

            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()

            columns = [desc[0] for desc in cursor.description]
            return [dict(zip(columns, row)) for row in rows]

    async def get_failure_counts_by_test_case(
        self,
        days_back: int = 30,
        top_n: int = 20,
        group_hash: Optional[str] = None,
        metadata_filters: Optional[Dict[str, str]] = None
    ) -> List[Dict[str, Any]]:
        """Get top N test cases by failure count, including run_id of last failure."""
        async with self.get_connection() as db:
            # Build conditions for the base query
            base_conditions = [
                "tc.status = 'failed'",
                "tr.start_time >= datetime('now', ?)"
            ]
            params = [f"-{days_back} days"]

            if group_hash:
                base_conditions.append("tr.group_hash = ?")
                params.append(group_hash)

            if metadata_filters:
                for key, value in metadata_filters.items():
                    base_conditions.append("""
                        EXISTS (SELECT 1 FROM user_metadata um
                                WHERE um.run_id = tr.run_id AND um.key = ? AND um.value = ?)
                    """)
                    params.extend([key, value])

            where_clause = " AND ".join(base_conditions)

            # Use a subquery to get the run_id and tc_id of the last failure for each test case
            query = f"""
                WITH failure_stats AS (
                    SELECT tc.tc_full_name,
                           COUNT(*) as failure_count,
                           MAX(tc.start_time) as last_failure
                    FROM test_cases tc
                    JOIN test_runs tr ON tc.run_id = tr.run_id
                    WHERE {where_clause}
                    GROUP BY tc.tc_full_name
                    ORDER BY failure_count DESC
                    LIMIT ?
                ),
                last_failures AS (
                    SELECT tc.tc_full_name, tc.run_id as last_failure_run_id, tc.tc_id as last_failure_tc_id, tc.start_time,
                           ROW_NUMBER() OVER (PARTITION BY tc.tc_full_name ORDER BY tc.start_time DESC) as rn
                    FROM test_cases tc
                    JOIN test_runs tr ON tc.run_id = tr.run_id
                    WHERE tc.status = 'failed' AND {where_clause}
                )
                SELECT fs.tc_full_name, fs.failure_count, fs.last_failure, lf.last_failure_run_id, lf.last_failure_tc_id
                FROM failure_stats fs
                LEFT JOIN last_failures lf ON fs.tc_full_name = lf.tc_full_name AND lf.rn = 1
                ORDER BY fs.failure_count DESC
            """

            # Add top_n param and duplicate the other params for the second subquery
            params.append(top_n)
            params.extend(params[:-1])  # Duplicate params for the last_failures subquery (excluding top_n)

            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()

            columns = [desc[0] for desc in cursor.description]
            return [dict(zip(columns, row)) for row in rows]

    async def get_test_case_classification_data(
        self,
        tc_full_name: str,
        group_hash: Optional[str] = None,
        limit: int = 10,
        current_run_id: Optional[str] = None,
        current_run_start_time: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get test case history data needed for classification.

        Returns the last N results for a test case within the same group,
        ordered from most recent to oldest.
        Excludes the current run and any runs executed later than the current run.
        """
        async with self.get_connection() as db:
            query = """
                SELECT tc.status, tc.tc_id, tr.start_time as run_start_time, tr.run_id, tr.run_name
                FROM test_cases tc
                JOIN test_runs tr ON tc.run_id = tr.run_id
                WHERE tc.tc_full_name = ?
            """
            params = [tc_full_name]

            if group_hash:
                query += " AND tr.group_hash = ?"
                params.append(group_hash)

            # Exclude current run
            if current_run_id:
                query += " AND tr.run_id != ?"
                params.append(current_run_id)

            # Exclude runs executed later than current run (based on start_time)
            if current_run_start_time:
                query += " AND tr.start_time <= ?"
                params.append(current_run_start_time)

            query += " ORDER BY tr.start_time DESC LIMIT ?"
            params.append(limit)

            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
            return [dict(zip(columns, row)) for row in rows]

    async def get_test_run_history_in_group(
        self,
        group_hash: str,
        limit: int = 10,
        exclude_run_id: Optional[str] = None,
        current_run_start_time: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get recent test runs within a group for hover history.

        Returns summary info for last N runs in the group.
        """
        async with self.get_connection() as db:
            query = """
                SELECT tr.run_id, tr.run_name, tr.status, tr.start_time, tr.end_time,
                       COUNT(tc.id) as test_case_count,
                       SUM(CASE WHEN tc.status = 'passed' THEN 1 ELSE 0 END) as passed_count,
                       SUM(CASE WHEN tc.status = 'failed' THEN 1 ELSE 0 END) as failed_count,
                       SUM(CASE WHEN tc.status = 'skipped' THEN 1 ELSE 0 END) as skipped_count,
                       SUM(CASE WHEN tc.status = 'error' THEN 1 ELSE 0 END) as error_count
                FROM test_runs tr
                LEFT JOIN test_cases tc ON tr.run_id = tc.run_id
                WHERE tr.group_hash = ?
            """
            params = [group_hash]

            if exclude_run_id:
                query += " AND tr.run_id != ?"
                params.append(exclude_run_id)

            if current_run_start_time:
                query += " AND tr.start_time < ?"
                params.append(current_run_start_time)

            query += " GROUP BY tr.run_id ORDER BY tr.start_time DESC LIMIT ?"
            params.append(limit)

            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
            return [dict(zip(columns, row)) for row in rows]

    async def get_previous_run_test_cases(
        self,
        group_hash: str,
        current_run_id: str
    ) -> List[str]:
        """Get test case IDs from the previous run in the same group.

        Used to determine if a test case is new (not in previous run).
        """
        async with self.get_connection() as db:
            # First get the current run's start time
            cursor = await db.execute(
                "SELECT start_time FROM test_runs WHERE run_id = ?",
                (current_run_id,)
            )
            row = await cursor.fetchone()
            if not row:
                return []
            current_start_time = row[0]

            # Find the most recent run before the current one in the same group
            cursor = await db.execute("""
                SELECT run_id FROM test_runs
                WHERE group_hash = ? AND start_time < ?
                ORDER BY start_time DESC LIMIT 1
            """, (group_hash, current_start_time))

            row = await cursor.fetchone()
            if not row:
                return []

            previous_run_id = row[0]

            # Get test case IDs from the previous run
            cursor = await db.execute(
                "SELECT tc_full_name FROM test_cases WHERE run_id = ?",
                (previous_run_id,)
            )
            rows = await cursor.fetchall()
            return [r[0] for r in rows]

    async def get_classifications_for_run(
        self,
        run_id: str,
        group_hash: Optional[str] = None
    ) -> Dict[str, Dict[str, Any]]:
        """Get classification data for all test cases in a run.

        Returns a dict mapping tc_full_name to classification info:
        - classification: 'flaky', 'fixed', 'regression', or None
        - is_new: True if TC wasn't in previous run
        - history: list of last 10 statuses (for hover tooltip)
        """
        async with self.get_connection() as db:
            # Get current run's start time
            cursor = await db.execute(
                "SELECT start_time FROM test_runs WHERE run_id = ?",
                (run_id,)
            )
            run_row = await cursor.fetchone()
            current_run_start_time = run_row[0] if run_row else None

            # Get all test cases in the run
            cursor = await db.execute(
                "SELECT tc_full_name, status FROM test_cases WHERE run_id = ?",
                (run_id,)
            )
            test_cases = await cursor.fetchall()

            if not test_cases:
                return {}

            # Get previous run's test cases if we have a group
            previous_tc_ids = set()
            if group_hash:
                previous_tc_ids = set(await self.get_previous_run_test_cases(group_hash, run_id))

            result = {}
            for tc_id, current_status in test_cases:
                # Get history for this TC - only previous runs (excludes current and future runs)
                history = await self.get_test_case_classification_data(
                    tc_id,
                    group_hash,
                    limit=10,
                    current_run_id=run_id,
                    current_run_start_time=current_run_start_time
                )

                # Calculate classification based on previous runs only
                classification = self._calculate_classification(current_status, history)

                # Determine if TC is new (wasn't in previous run)
                # Returns True if:
                # - We have a group_hash (so we can compare runs)
                # - There were test cases in the previous run
                # - This TC was not in the previous run
                is_new = bool(
                    group_hash
                    and len(previous_tc_ids) > 0
                    and tc_id not in previous_tc_ids
                )

                result[tc_id] = {
                    'classification': classification,
                    'is_new': is_new,
                    'history': [
                        {
                            'status': h['status'],
                            'run_id': h['run_id'],
                            'run_name': h.get('run_name'),
                            'run_start_time': h.get('run_start_time')
                        }
                        for h in history
                    ]
                }

            return result

    def _calculate_classification(
        self,
        current_status: str,
        history: List[Dict[str, Any]]
    ) -> Optional[str]:
        """Calculate classification based on current status and history.

        - flaky: More than 4 transitions in last 10 results
        - fixed: Last 5 results were fail/error, new one is pass
        - regression: Last 5 results were pass, new one is fail/error
        """
        if not history:
            return None

        current_is_pass = current_status.lower() == 'passed'
        current_is_fail = current_status.lower() in ('failed', 'error')

        # Filter out skipped for classification purposes
        relevant_history = [
            h for h in history
            if h['status'].lower() not in ('skipped', 'running', 'aborted')
        ]

        if not relevant_history:
            return None

        # Check for flaky (count transitions in history + current)
        statuses = [current_status.lower()] + [h['status'].lower() for h in relevant_history]
        # Filter to just pass/fail for transition counting
        statuses = [
            'pass' if s == 'passed' else 'fail'
            for s in statuses
            if s in ('passed', 'failed', 'error')
        ]

        if len(statuses) >= 2:
            transitions = sum(
                1 for i in range(len(statuses) - 1)
                if statuses[i] != statuses[i + 1]
            )
            if transitions > 4:
                return 'flaky'

        # Check for fixed (last 5 were fail, now pass)
        if current_is_pass and len(relevant_history) >= 5:
            last_5 = [h['status'].lower() for h in relevant_history[:5]]
            if all(s in ('failed', 'error') for s in last_5):
                return 'fixed'

        # Check for regression (last 5 were pass, now fail)
        if current_is_fail and len(relevant_history) >= 5:
            last_5 = [h['status'].lower() for h in relevant_history[:5]]
            if all(s == 'passed' for s in last_5):
                return 'regression'

        return None


# Global database instance - will be initialized with config path
db = None

def initialize_database(data_dir: str = "data"):
    """Initialize the global database instance with the configured data directory."""
    global db
    # Ensure the data directory exists
    data_path = Path(data_dir)
    data_path.mkdir(parents=True, exist_ok=True)

    db_path = data_path / "test_results.db"
    db = TestResultsDatabase(str(db_path))
    return db

# Convenience functions for integration with existing code
async def log_test_run_started(
    run_id: str,
    retention_days: Optional[int],
    local_run: bool,
    user_metadata: Dict[str, Any] = None,
    dut: str = "TestDevice-001",
    run_name: Optional[str] = None,
    group_name: Optional[str] = None,
    group_hash: Optional[str] = None,
    group_metadata: Dict[str, Any] = None
):
    """Log a test run start to the database."""
    test_run = TestRunData(
        run_id=run_id,
        status="running",
        start_time=datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
        end_time=None,
        retention_days=retention_days,
        local_run=local_run,
        dut=dut,
        run_name=run_name,
        group_name=group_name,
        group_hash=group_hash
    )
    return await db.insert_test_run(test_run, user_metadata, group_metadata)


async def log_test_run_finished(run_id: str, status: str):
    """Log a test run completion to the database."""
    return await db.update_test_run(run_id, status=status, end_time=datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z")


async def log_test_case_started(run_id: str, tc_full_name: str, tc_id: str, start_time: str = None):
    """Log a test case start to the database."""
    if start_time is None:
        start_time = datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"

    test_case = TestCaseData(
        id=0,  # Will be auto-generated
        run_id=run_id,
        tc_full_name=tc_full_name,
        tc_id=tc_id,
        status="running",
        start_time=start_time,
        end_time=None
    )
    return await db.insert_test_case(test_case)


async def log_test_case_finished(run_id: str, tc_full_name: str, status: str):
    """Log a test case completion to the database."""
    async with db.get_connection() as connection:
        await connection.execute("""
            UPDATE test_cases
            SET status = ?, end_time = ?, updated_at = ?
            WHERE run_id = ? AND tc_full_name = ?
        """, (
            status,
            datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
            datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
            run_id,
            tc_full_name
        ))
        await connection.commit()
        return True
