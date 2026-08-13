"""TLS certificate generation, ingest HTTPS gate, and server-info fields."""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from starlette.testclient import TestClient

from testrift_server import auth, config, database
from testrift_server.tls_certs import (
    current_material,
    fingerprint_der,
    format_fingerprint,
    ingest_base_url,
    parse_ingest_port,
    parse_tls_config,
    reset_material_for_tests,
    setup_tls,
)
from testrift_server.tr_server import create_app
from testrift_server.websocket import WebSocketServer


def _tls_config(**overrides):
    cfg = {
        "ui": "off",
        "ingest": "off",
        "cert_file": "",
        "key_file": "",
    }
    cfg.update(overrides)
    return cfg


@pytest.fixture
def tls_data_dir():
    temp_dir = tempfile.mkdtemp()
    reset_material_for_tests()
    yield Path(temp_dir)
    reset_material_for_tests()
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_parse_tls_rejects_unknown_mode():
    with pytest.raises(ValueError, match="tls.ingest"):
        parse_tls_config({"ingest": "maybe"})


def test_parse_tls_rejects_mixed_auto_and_files():
    with pytest.raises(ValueError, match="cannot mix"):
        parse_tls_config({"ingest": "auto", "ui": "files", "cert_file": "a", "key_file": "b"})


def test_parse_tls_files_requires_paths():
    with pytest.raises(ValueError, match="cert_file"):
        parse_tls_config({"ingest": "files"})


def test_ingest_port_defaults_when_ui_is_http():
    tls = _tls_config(ingest="auto")
    assert parse_ingest_port({}, tls, 8080) == 8443


def test_ingest_port_must_differ_from_ui_when_ui_is_http():
    tls = _tls_config(ingest="auto")
    with pytest.raises(ValueError, match="differ"):
        parse_ingest_port({"ingest_port": 8080}, tls, 8080)


def test_ingest_port_unused_when_tls_off():
    assert parse_ingest_port({"ingest_port": 8443}, _tls_config(), 8080) is None


def test_ingest_port_shared_when_ui_also_tls():
    tls = _tls_config(ingest="auto", ui="auto")
    assert parse_ingest_port({}, tls, 8080) is None


def test_auto_certs_generate_reuse_and_fingerprint(tls_data_dir, caplog):
    cfg = {
        "data_dir": tls_data_dir,
        "tls": _tls_config(ingest="auto"),
        "port": 8080,
    }
    caplog.set_level(logging.INFO)
    first = setup_tls(cfg)
    assert first is not None
    assert first.ca_cert_path.is_file()
    assert first.chain_path.is_file()
    assert "TLS CA fingerprint (SHA-256):" in caplog.text
    assert first.ca_fingerprint in caplog.text
    assert first.ca_fingerprint.count(":") == 31

    ca_pem = first.ca_cert_path.read_bytes()
    server_cert = x509.load_pem_x509_certificate(first.cert_path.read_bytes())
    sans = server_cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    dns = set(sans.get_values_for_type(x509.DNSName))
    ips = {str(name) for name in sans.get_values_for_type(x509.IPAddress)}
    assert "localhost" in dns
    assert "127.0.0.1" in ips
    assert "::1" in ips

    second = setup_tls(cfg)
    assert second.ca_fingerprint == first.ca_fingerprint
    assert second.ca_cert_path.read_bytes() == ca_pem
    der = x509.load_pem_x509_certificate(ca_pem).public_bytes(serialization.Encoding.DER)
    assert fingerprint_der(der) == first.ca_fingerprint
    assert format_fingerprint(first.ca_fingerprint.replace(":", "")) == first.ca_fingerprint


def test_ingest_base_url_uses_ingest_port():
    cfg = {
        "port": 8080,
        "ingest_port": 8443,
        "tls": _tls_config(ingest="auto"),
    }
    assert ingest_base_url(cfg, "127.0.0.1:8080") == "https://127.0.0.1:8443"
    assert ingest_base_url(cfg, "localhost:8080") == "https://localhost:8443"
    assert ingest_base_url(cfg, "[::1]:8080") == "https://[::1]:8443"


def test_ingest_base_url_http_when_tls_off():
    cfg = {"port": 8080, "ingest_port": None, "tls": _tls_config()}
    assert ingest_base_url(cfg, "127.0.0.1:8080") == "http://127.0.0.1:8080"


@pytest.fixture
def tls_ingest_app(monkeypatch, tls_data_dir):
    cfg = dict(config.CONFIG)
    cfg["data_dir"] = tls_data_dir
    cfg["tls"] = _tls_config(ingest="auto")
    cfg["ingest_port"] = 8443
    monkeypatch.setattr(config, "CONFIG", cfg)
    monkeypatch.setattr("testrift_server.tr_server.DATA_DIR", tls_data_dir)
    monkeypatch.setattr("testrift_server.config.DATA_DIR", tls_data_dir)
    database.initialize_database(tls_data_dir)
    setup_tls(cfg)
    app = create_app(ws_server=WebSocketServer())
    return app


def test_http_ingest_rejected_when_tls_on(tls_ingest_app):
    with TestClient(tls_ingest_app) as client:
        response = client.post("/api/runs/run1/commits", json={})
        assert response.status_code == 400
        assert response.json()["error"] == auth.INGEST_TLS_REQUIRED_MESSAGE
        with pytest.raises(Exception):
            with client.websocket_connect("/ws/nunit"):
                pass


def test_https_ingest_allowed_when_tls_on(tls_ingest_app):
    with TestClient(tls_ingest_app, base_url="https://testserver") as client:
        response = client.post("/api/runs/run1/commits", json={"diffs": []})
        assert response.json().get("error") != auth.INGEST_TLS_REQUIRED_MESSAGE


def test_nunit_websocket_requires_tls_scheme(monkeypatch):
    cfg = dict(config.CONFIG)
    cfg["tls"] = _tls_config(ingest="auto")
    monkeypatch.setattr(config, "CONFIG", cfg)
    monkeypatch.setattr(auth, "ingest_token_configured", lambda: "")

    from types import SimpleNamespace
    from testrift_server.tls_certs import ingest_tls_enabled
    assert ingest_tls_enabled() is True

    def _ws(scheme):
        return SimpleNamespace(url=SimpleNamespace(scheme=scheme), headers={}, cookies={})

    assert asyncio.run(auth.enforce_websocket(_ws("wss"), "/ws/nunit")) is True
    assert asyncio.run(auth.enforce_websocket(_ws("https"), "/ws/nunit")) is True
    assert asyncio.run(auth.enforce_websocket(_ws("ws"), "/ws/nunit")) is False
    assert asyncio.run(auth.enforce_websocket(_ws("http"), "/ws/nunit")) is False


def test_server_info_includes_ingest_tls_fields(tls_ingest_app):
    material = current_material()
    with TestClient(tls_ingest_app) as client:
        body = client.get("/api/server-info").json()
        assert body["ingest_url"] == "https://testserver:8443"
        assert body["tls_ca_fingerprint"] == material.ca_fingerprint
        ca = client.get("/ca.crt")
        assert ca.status_code == 200
        assert b"BEGIN CERTIFICATE" in ca.content


def test_ca_crt_missing_when_tls_off(monkeypatch, tls_data_dir):
    monkeypatch.setattr("testrift_server.config.DATA_DIR", tls_data_dir)
    monkeypatch.setattr("testrift_server.tr_server.DATA_DIR", tls_data_dir)
    database.initialize_database(tls_data_dir)
    app = create_app(ws_server=WebSocketServer())
    with TestClient(app) as client:
        assert client.get("/ca.crt").status_code == 404
        body = client.get("/api/server-info").json()
        assert body["ingest_url"].startswith("http://")
        assert body["tls_ca_fingerprint"] is None
