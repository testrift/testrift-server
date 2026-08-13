"""TLS certificate generation, fingerprinting, and runtime material."""

from __future__ import annotations

import ipaddress
import logging
import os
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

logger = logging.getLogger(__name__)

TLS_DIR_NAME = "tls"
CA_CERT_NAME = "ca.crt"
CA_KEY_NAME = "ca.key"
SERVER_CERT_NAME = "server.crt"
SERVER_KEY_NAME = "server.key"
SERVER_CHAIN_NAME = "server-chain.crt"
CERT_DAYS = 825

_material: Optional["TlsMaterial"] = None


class TlsMaterial:
    def __init__(
        self,
        *,
        ca_cert_path: Optional[Path],
        cert_path: Path,
        key_path: Path,
        chain_path: Path,
        ca_fingerprint: str,
    ):
        self.ca_cert_path = ca_cert_path
        self.cert_path = cert_path
        self.key_path = key_path
        self.chain_path = chain_path
        self.ca_fingerprint = ca_fingerprint


def default_tls_config() -> dict:
    return {
        "ui": "off",
        "ingest": "off",
        "cert_file": "",
        "key_file": "",
    }


def parse_tls_config(tls) -> dict:
    """Validate and normalize the top-level tls section."""
    if tls is None:
        tls = {}
    if not isinstance(tls, dict):
        raise ValueError("tls must be a mapping")
    defaults = default_tls_config()

    def _mode(key: str) -> str:
        value = str(tls.get(key, defaults[key]) or "off").strip().lower()
        if value not in ("off", "auto", "files"):
            raise ValueError(f"tls.{key} must be off, auto, or files")
        return value

    ui = _mode("ui")
    ingest = _mode("ingest")
    enabled = {ui, ingest} - {"off"}
    if "auto" in enabled and "files" in enabled:
        raise ValueError("tls.ui and tls.ingest cannot mix auto and files")
    cert_file = str(tls.get("cert_file") or "").strip()
    key_file = str(tls.get("key_file") or "").strip()
    if "files" in enabled:
        if not cert_file or not key_file:
            raise ValueError("tls.cert_file and tls.key_file are required when tls.ui or tls.ingest is files")
    return {
        "ui": ui,
        "ingest": ingest,
        "cert_file": cert_file,
        "key_file": key_file,
    }


def parse_ingest_port(server_config: dict, tls: dict, ui_port: int) -> Optional[int]:
    """Return the ingest bind port, or None when ingest uses the UI listener."""
    ingest_tls = tls.get("ingest") != "off"
    ui_tls = tls.get("ui") != "off"
    raw = server_config.get("ingest_port")
    if raw is None:
        if ingest_tls and not ui_tls:
            return 8443
        return None
    if not isinstance(raw, int) or not (1 <= raw <= 65535):
        raise ValueError(f"server.ingest_port must be a port number, got: {raw}")
    if ingest_tls and not ui_tls and raw == ui_port:
        raise ValueError("server.ingest_port must differ from server.port when the UI is HTTP")
    if not ingest_tls:
        return None
    if raw == ui_port:
        return None
    return raw


def fingerprint_der(der: bytes) -> str:
    digest = hashes.Hash(hashes.SHA256())
    digest.update(der)
    return format_fingerprint(digest.finalize().hex())


def format_fingerprint(hex_digest: str) -> str:
    compact = normalize_fingerprint(hex_digest)
    return ":".join(compact[i:i + 2] for i in range(0, len(compact), 2)).upper()


def normalize_fingerprint(value: str) -> str:
    text = (value or "").strip().lower()
    if text.startswith("sha256:"):
        text = text[7:]
    text = text.replace(":", "").replace(" ", "")
    return text


def fingerprints_equal(left: str, right: str) -> bool:
    a = normalize_fingerprint(left)
    b = normalize_fingerprint(right)
    if not a or not b or len(a) != 64 or len(b) != 64:
        return False
    return a == b


def fingerprint_cert_pem(pem: bytes) -> str:
    cert = x509.load_pem_x509_certificate(pem)
    return fingerprint_der(cert.public_bytes(serialization.Encoding.DER))


def current_material() -> Optional[TlsMaterial]:
    return _material


def ingest_tls_enabled(config: Optional[dict] = None) -> bool:
    from . import config as config_mod
    cfg = config if config is not None else config_mod.CONFIG
    return (cfg.get("tls") or {}).get("ingest", "off") != "off"


def ui_tls_enabled(config: Optional[dict] = None) -> bool:
    from . import config as config_mod
    cfg = config if config is not None else config_mod.CONFIG
    return (cfg.get("tls") or {}).get("ui", "off") != "off"


def setup_tls(config: dict) -> Optional[TlsMaterial]:
    """Load or generate certificates. Returns material when any listener uses TLS."""
    global _material
    tls = config.get("tls") or default_tls_config()
    if tls.get("ui") == "off" and tls.get("ingest") == "off":
        _material = None
        return None
    if tls.get("ui") == "files" or tls.get("ingest") == "files":
        material = _material_from_files(tls)
    else:
        material = _material_from_auto(Path(config["data_dir"]))
    _material = material
    logger.info("TLS CA fingerprint (SHA-256): %s", material.ca_fingerprint)
    if material.ca_cert_path:
        logger.info("TLS CA certificate: %s", material.ca_cert_path)
    return material


def _chmod_private(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _material_from_files(tls: dict) -> TlsMaterial:
    cert_path = Path(tls["cert_file"]).expanduser().resolve()
    key_path = Path(tls["key_file"]).expanduser().resolve()
    if not cert_path.is_file():
        raise ValueError(f"tls.cert_file not found: {cert_path}")
    if not key_path.is_file():
        raise ValueError(f"tls.key_file not found: {key_path}")
    pem = cert_path.read_bytes()
    fingerprint = _fingerprint_from_pem_bundle(pem)
    return TlsMaterial(
        ca_cert_path=None,
        cert_path=cert_path,
        key_path=key_path,
        chain_path=cert_path,
        ca_fingerprint=fingerprint,
    )


def _fingerprint_from_pem_bundle(pem: bytes) -> str:
    certs = []
    rest = pem
    begin = b"-----BEGIN CERTIFICATE-----"
    while True:
        start = rest.find(begin)
        if start < 0:
            break
        end = rest.find(b"-----END CERTIFICATE-----", start)
        if end < 0:
            break
        block = rest[start:end + len(b"-----END CERTIFICATE-----")]
        certs.append(x509.load_pem_x509_certificate(block))
        rest = rest[end + 1:]
    if not certs:
        raise ValueError("tls.cert_file does not contain a PEM certificate")
    for cert in reversed(certs):
        try:
            constraints = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
            if constraints.ca:
                return fingerprint_der(cert.public_bytes(serialization.Encoding.DER))
        except x509.ExtensionNotFound:
            continue
    return fingerprint_der(certs[-1].public_bytes(serialization.Encoding.DER))


def _material_from_auto(data_dir: Path) -> TlsMaterial:
    tls_dir = data_dir / TLS_DIR_NAME
    tls_dir.mkdir(parents=True, exist_ok=True)
    ca_cert_path = tls_dir / CA_CERT_NAME
    ca_key_path = tls_dir / CA_KEY_NAME
    server_cert_path = tls_dir / SERVER_CERT_NAME
    server_key_path = tls_dir / SERVER_KEY_NAME
    chain_path = tls_dir / SERVER_CHAIN_NAME

    if not (ca_cert_path.is_file() and ca_key_path.is_file()):
        _write_ca(ca_cert_path, ca_key_path)
        logger.info("Generated TLS CA in %s", tls_dir)
    ca_key = serialization.load_pem_private_key(ca_key_path.read_bytes(), password=None)
    ca_cert = x509.load_pem_x509_certificate(ca_cert_path.read_bytes())

    if not (server_cert_path.is_file() and server_key_path.is_file()) or _server_cert_expired(server_cert_path):
        _write_server_cert(server_cert_path, server_key_path, ca_cert, ca_key)
        logger.info("Issued TLS server certificate in %s", tls_dir)

    server_pem = server_cert_path.read_bytes()
    ca_pem = ca_cert_path.read_bytes()
    chain_path.write_bytes(server_pem.rstrip() + b"\n" + ca_pem)
    fingerprint = fingerprint_der(ca_cert.public_bytes(serialization.Encoding.DER))
    return TlsMaterial(
        ca_cert_path=ca_cert_path,
        cert_path=server_cert_path,
        key_path=server_key_path,
        chain_path=chain_path,
        ca_fingerprint=fingerprint,
    )


def _server_cert_expired(path: Path) -> bool:
    try:
        cert = x509.load_pem_x509_certificate(path.read_bytes())
    except Exception:
        return True
    now = datetime.now(timezone.utc)
    return cert.not_valid_after_utc <= now + timedelta(days=14)


def _write_ca(cert_path: Path, key_path: Path) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "TestRift local CA"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "TestRift"),
    ])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=CERT_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_cert_sign=True,
                crl_sign=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    _write_key(key_path, key)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def _write_server_cert(cert_path: Path, key_path: Path, ca_cert: x509.Certificate, ca_key) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    hostname = socket.gethostname()
    names = [
        x509.DNSName("localhost"),
        x509.DNSName(hostname),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
        x509.IPAddress(ipaddress.ip_address("::1")),
    ]
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "TestRift"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "TestRift"),
        ]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=CERT_DAYS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName(names), critical=False)
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=True,
                key_cert_sign=False,
                crl_sign=False,
                content_commitment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    _write_key(key_path, key)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def _write_key(path: Path, key) -> None:
    path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    _chmod_private(path)


def ingest_base_url(config: dict, host_header: str = "127.0.0.1") -> str:
    """Public ingest origin advertised to test clients."""
    tls = config.get("tls") or {}
    ingest_port = config.get("ingest_port")
    port = ingest_port or config["port"]
    if ingest_port:
        scheme = "https"
    elif tls.get("ingest", "off") != "off" or tls.get("ui", "off") != "off":
        scheme = "https"
    else:
        scheme = "http"
    host = _host_without_port(host_header)
    default_port = 443 if scheme == "https" else 80
    if port == default_port:
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def _host_without_port(host_header: str) -> str:
    host = (host_header or "").split(",")[0].strip() or "127.0.0.1"
    if host.startswith("["):
        end = host.find("]")
        if end > 0:
            return host[: end + 1]
    if host.count(":") == 1:
        return host.split(":")[0]
    return host


def reset_material_for_tests() -> None:
    global _material
    _material = None
