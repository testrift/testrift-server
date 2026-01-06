import asyncio
import os
from unittest.mock import MagicMock, patch

import pytest


def test_load_config_env_var_missing_file_exits():
    from testrift_server import tr_server

    with patch.dict(os.environ, {"TESTRIFT_SERVER_YAML": r"C:\definitely\does-not-exist.yaml"}):
        with pytest.raises(SystemExit) as e:
            tr_server.load_config(None)
        assert int(e.value.code) == 1


@pytest.mark.asyncio
async def test_admin_shutdown_forbidden_when_not_localhost():
    from testrift_server import tr_server

    request = MagicMock()
    request.remote = "10.0.0.1"
    resp = await tr_server.api_admin_shutdown_handler(request)
    assert resp.status == 403


@pytest.mark.asyncio
async def test_admin_shutdown_requires_matching_config_hash_header():
    from testrift_server import tr_server

    request = MagicMock()
    request.remote = "127.0.0.1"
    request.headers = {"X-TestRift-Config-Hash": "wrong"}
    resp = await tr_server.api_admin_shutdown_handler(request)
    assert resp.status == 403


@pytest.mark.asyncio
async def test_admin_shutdown_schedules_exit_when_hash_matches():
    from testrift_server import tr_server

    expected = tr_server._testrift_config_hash(tr_server.CONFIG)

    class FakeLoop:
        def __init__(self):
            self.calls = []

        def call_later(self, delay, cb):
            self.calls.append((delay, cb))

    fake_loop = FakeLoop()

    request = MagicMock()
    request.remote = "127.0.0.1"
    request.headers = {"X-TestRift-Config-Hash": expected}

    with patch.object(asyncio, "get_running_loop", return_value=fake_loop), patch.object(tr_server.os, "_exit") as p_exit:
        resp = await tr_server.api_admin_shutdown_handler(request)
        assert resp.status == 200
        assert len(fake_loop.calls) == 1
        delay, cb = fake_loop.calls[0]
        assert delay == pytest.approx(0.2)
        # Execute callback and verify it calls os._exit(0)
        cb()
        p_exit.assert_called_once_with(0)


def test_main_returns_2_on_mismatch_without_restart_flag(monkeypatch):
    from testrift_server import tr_server

    # Make it look like something is already running with a different config hash.
    monkeypatch.setattr(tr_server, "_get_running_server_info", lambda port: {"service": "testrift-server", "config_hash": "different"})
    monkeypatch.setattr(tr_server.web, "run_app", lambda *args, **kwargs: None)

    rc = tr_server.main(argv=[])
    assert rc == 2


def test_main_restart_on_config_triggers_shutdown_and_starts(monkeypatch):
    from testrift_server import tr_server

    # Force mismatch path
    monkeypatch.setattr(tr_server, "_testrift_config_hash", lambda cfg: "new")

    running_hash = "old"
    calls = {"shutdown": 0, "run_app": 0}

    def fake_running(port):
        # First call: server is running (mismatch)
        # Then, after shutdown: no server running
        if calls["shutdown"] == 0:
            return {"service": "testrift-server", "config_hash": running_hash, "config_path": "x"}
        return None

    monkeypatch.setattr(tr_server, "_get_running_server_info", fake_running)

    def fake_shutdown(port, rh):
        assert port == tr_server.PORT
        assert rh == running_hash
        calls["shutdown"] += 1
        return True

    monkeypatch.setattr(tr_server, "_request_running_server_shutdown", fake_shutdown)
    monkeypatch.setattr(tr_server.time, "sleep", lambda _: None)
    monkeypatch.setattr(tr_server.time, "time", lambda: 0.0)

    def fake_run_app(*args, **kwargs):
        calls["run_app"] += 1

    monkeypatch.setattr(tr_server.web, "run_app", fake_run_app)

    rc = tr_server.main(argv=["--restart-on-config"])
    assert rc == 0
    assert calls["shutdown"] == 1
    assert calls["run_app"] == 1


