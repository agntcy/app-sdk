# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""``tls_insecure`` against a real, TLS-serving SLIM dataplane.

A throwaway dataplane container is started with a certificate generated for
this run (ECDSA P-256, SHA-256, valid for one day, ``localhost`` only, never
written to the repository).  The client trusts it through ``SSL_CERT_FILE``,
which is the only trust source ``tls_insecure=False`` has: it verifies against
the system CA pool.

Connection attempts that are *expected to fail verification* run in a
short-lived subprocess: slim-bindings keeps retrying such a connection in the
background and it cannot be cancelled, which would leak into later tests.

Requires Docker.  Skipped when Docker or the SLIM image is unavailable.
"""

from __future__ import annotations

import asyncio
import datetime
import ipaddress
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import cast
from pathlib import Path

import pytest
import slim_bindings
from a2a.client import Client, minimal_agent_card
from a2a.helpers import get_stream_response_text, new_text_message
from a2a.types import Role, SendMessageRequest

from agntcy_app_sdk.semantic.a2a import ClientConfig as A2AClientConfig
from agntcy_app_sdk.semantic.a2a import A2AClientFactory
from agntcy_app_sdk.semantic.a2a.client.config import SlimRpcConfig
from agntcy_app_sdk.semantic.a2a.server.srpc import A2ASRPCServerHandler
from agntcy_app_sdk.transport.slim.transport import SLIMTransport
from tests.server.a2a_slimrpc_server import _build_a2a_slimrpc_config

pytest_plugins = "pytest_asyncio"

_REPO = Path(__file__).resolve().parents[2]
_COMPOSE = _REPO / "services" / "docker" / "docker-compose.yaml"
_SERVER_PORT = 46357  # port the dataplane listens on inside the container

# Test-only placeholder, not a credential.
_TEST_SECRET = "x" * 32

# How long a connection attempt that is expected to fail is allowed to run.
_FAIL_WINDOW_S = 6


def _slim_image() -> str:
    """The SLIM image the rest of the e2e suite runs (override with SLIM_IMAGE)."""
    if override := os.environ.get("SLIM_IMAGE"):
        return override
    match = re.search(r"image:\s*(ghcr\.io/agntcy/slim:\S+)", _COMPOSE.read_text())
    if not match:
        pytest.skip("Cannot determine the SLIM image from docker-compose.yaml")
    return match.group(1)


# ---------------------------------------------------------------------------
# Ephemeral certificate + TLS dataplane
# ---------------------------------------------------------------------------


def _write_test_certificate(directory: Path) -> Path:
    """Write a one-day self-signed ``localhost`` certificate and its key."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path = directory / "cert.pem"
    key_path = directory / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    # The dataplane container runs as another user and must read both files.
    # The key is this run's throwaway key and is deleted with the directory.
    directory.chmod(0o755)
    cert_path.chmod(0o644)
    key_path.chmod(0o644)
    return cert_path


_DATAPLANE_CONFIG = f"""\
tracing:
  log_level: info
runtime:
  n_cores: 0
  thread_name: "slim-data-plane"
  drain_timeout: 10s
services:
  slim/0:
    dataplane:
      servers:
        - endpoint: "0.0.0.0:{_SERVER_PORT}"
          tls:
            insecure: false
            source:
              type: file
              cert: /certs/cert.pem
              key: /certs/key.pem
      clients: []
"""


@dataclass(frozen=True)
class TlsDataplane:
    port: int
    cert_path: Path

    @property
    def https(self) -> str:
        return f"https://localhost:{self.port}"

    @property
    def http(self) -> str:
        return f"http://localhost:{self.port}"

    @property
    def https_by_ip(self) -> str:
        """Same dataplane under another endpoint key (the cert covers the IP)."""
        return f"https://127.0.0.1:{self.port}"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


@pytest.fixture(scope="module")
def tls_dataplane() -> Iterator[TlsDataplane]:
    """A SLIM dataplane that serves TLS with a throwaway certificate."""
    if shutil.which("docker") is None:
        pytest.skip("docker is not available")
    pytest.importorskip("cryptography")

    workdir = Path(tempfile.mkdtemp(prefix="slim-tls-"))
    cert_path = _write_test_certificate(workdir)
    config_path = workdir / "config.yaml"
    config_path.write_text(_DATAPLANE_CONFIG)

    port = _free_port()
    container = f"slim-tls-{uuid.uuid4().hex[:8]}"
    started = subprocess.run(
        [
            "docker", "run", "-d", "--rm", "--name", container,
            "-p", f"127.0.0.1:{port}:{_SERVER_PORT}",
            "-v", f"{config_path}:/config.yaml:ro",
            "-v", f"{workdir}:/certs:ro",
            _slim_image(), "/slim", "--config", "/config.yaml",
        ],
        capture_output=True,
        text=True,
    )  # fmt: skip
    if started.returncode != 0:
        shutil.rmtree(workdir, ignore_errors=True)
        pytest.skip(f"cannot start the SLIM container: {started.stderr.strip()}")

    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                break
            except OSError:
                if time.monotonic() > deadline:
                    logs = subprocess.run(
                        ["docker", "logs", container], capture_output=True, text=True
                    )
                    pytest.fail(
                        f"TLS dataplane did not start:\n{logs.stdout}{logs.stderr}"
                    )
                time.sleep(0.3)
        yield TlsDataplane(port=port, cert_path=cert_path)
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)
        shutil.rmtree(workdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Connection matrix (one short-lived subprocess per attempt)
# ---------------------------------------------------------------------------

_CONNECT_SCRIPT = textwrap.dedent(
    """
    import asyncio, os, sys
    import slim_bindings
    from agntcy_app_sdk.transport.slim.common import (
        get_or_init_slim_service, new_client_config,
    )

    async def main(endpoint, insecure, window, raw):
        slim_bindings.uniffi_set_event_loop(asyncio.get_running_loop())
        service = get_or_init_slim_service()
        try:
            if raw:
                # Bypass the SDK: slim-bindings' own secure config, as is.
                config = slim_bindings.new_secure_client_config(endpoint)
            else:
                config = new_client_config(endpoint, insecure=insecure)
            await asyncio.wait_for(service.connect_async(config), window)
        except asyncio.TimeoutError:
            return "TIMEOUT"
        except Exception as exc:
            return f"ERROR {type(exc).__name__}: {exc}"
        return "CONNECTED"

    outcome = asyncio.run(
        main(sys.argv[1], sys.argv[2] == "1", float(sys.argv[3]), sys.argv[4] == "raw")
    )
    print("OUTCOME " + outcome, flush=True)
    os._exit(0)  # background retries must not keep the process alive
    """
)


def _attempt(
    endpoint: str, *, insecure: bool, trust: Path | None = None, raw: bool = False
) -> str:
    """Try one connection in a fresh process; return its outcome and logs.

    ``raw=True`` skips the SDK helper and uses slim-bindings' secure config
    directly (``insecure`` is then ignored).
    """
    env = {k: v for k, v in os.environ.items() if k != "SSL_CERT_FILE"}
    if trust is not None:
        env["SSL_CERT_FILE"] = str(trust)
    done = subprocess.run(
        [
            sys.executable, "-c", _CONNECT_SCRIPT,
            endpoint, "1" if insecure else "0", str(_FAIL_WINDOW_S),
            "raw" if raw else "sdk",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=_FAIL_WINDOW_S + 60,
    )  # fmt: skip
    return done.stdout + done.stderr


class TestTlsConnectionMatrix:
    def test_untrusted_certificate_is_rejected(
        self, tls_dataplane: TlsDataplane
    ) -> None:
        """``tls_insecure=False`` verifies: an unknown issuer never connects."""
        out = _attempt(tls_dataplane.https, insecure=False)

        assert "OUTCOME TIMEOUT" in out
        assert "UnknownIssuer" in out

    def test_trusted_certificate_connects(self, tls_dataplane: TlsDataplane) -> None:
        """Same client, same URL: only the trust differs, and it connects."""
        out = _attempt(
            tls_dataplane.https, insecure=False, trust=tls_dataplane.cert_path
        )

        assert "OUTCOME CONNECTED" in out

    def test_insecure_flag_is_plaintext_even_for_https_url(
        self, tls_dataplane: TlsDataplane
    ) -> None:
        """``tls_insecure=True`` wins over an ``https://`` URL, so it cannot
        talk to a TLS-only dataplane, even one whose certificate it trusts."""
        out = _attempt(
            tls_dataplane.https, insecure=True, trust=tls_dataplane.cert_path
        )

        assert "OUTCOME ERROR" in out
        assert "FRAME_SIZE_ERROR" in out  # plaintext HTTP/2 hitting a TLS port

    def test_tls_over_http_url_is_refused_before_connecting(
        self, tls_dataplane: TlsDataplane
    ) -> None:
        """``tls_insecure=False`` with ``http://`` is refused outright, so it
        can never end up as a silent plaintext connection."""
        out = _attempt(
            tls_dataplane.http, insecure=False, trust=tls_dataplane.cert_path
        )

        assert "OUTCOME ERROR ValueError" in out
        assert "https://" in out
        assert "FRAME_SIZE_ERROR" not in out  # it never dialed

    def test_canary_slim_bindings_still_goes_plaintext_over_http(
        self, tls_dataplane: TlsDataplane
    ) -> None:
        """Why the refusal above exists.  With slim-bindings' own secure
        config (no SDK guard), an ``http://`` URL on the same trusted
        dataplane fails with the plaintext error although ``https://`` works.
        If this starts connecting over TLS, the guard is no longer needed and
        this test says so."""
        out = _attempt(
            tls_dataplane.http,
            insecure=False,
            trust=tls_dataplane.cert_path,
            raw=True,
        )

        assert "OUTCOME ERROR" in out
        assert "FRAME_SIZE_ERROR" in out


# ---------------------------------------------------------------------------
# In-process: SLIMTransport and a full SlimRPC round trip over TLS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slim_transport_connects_over_tls_and_guards_the_shared_connection(
    tls_dataplane: TlsDataplane, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real ``SLIMTransport`` opens a verified TLS connection, and a second
    transport asking for the other setting is refused instead of silently
    reusing it."""
    monkeypatch.setenv("SSL_CERT_FILE", str(tls_dataplane.cert_path))
    slim_bindings.uniffi_set_event_loop(
        cast(asyncio.BaseEventLoop, asyncio.get_running_loop())
    )

    secure = SLIMTransport(
        routable_name="default/default/tls_secure",
        endpoint=tls_dataplane.https,
        shared_secret_identity=_TEST_SECRET,
        tls_insecure=False,
    )
    plaintext = SLIMTransport(
        routable_name="default/default/tls_plain",
        endpoint=tls_dataplane.https,
        shared_secret_identity=_TEST_SECRET,
        tls_insecure=True,
    )
    try:
        await secure.setup()
        assert secure._slim_connection_id is not None

        with pytest.raises(ValueError, match="tls_insecure"):
            await plaintext.setup()
    finally:
        await secure.close()


async def _collect_text(client: Client, request: SendMessageRequest) -> str:
    output = ""
    async for event in client.send_message(request):
        output += get_stream_response_text(event)
    return output


@pytest.mark.asyncio
async def test_slimrpc_round_trip_over_tls(
    tls_dataplane: TlsDataplane, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SlimRPC server and client both configured ``tls_insecure=False`` talk
    to each other through the TLS dataplane (each opens its own connection).

    Server and client share this process, and slim-bindings keys connections
    by endpoint string, so the client addresses the dataplane by IP."""
    monkeypatch.setenv("SSL_CERT_FILE", str(tls_dataplane.cert_path))
    agent = "default/default/Hello_World_Agent_1.0.0"

    config = _build_a2a_slimrpc_config(name=agent, endpoint=tls_dataplane.https)
    config.connection.tls_insecure = False
    handler = A2ASRPCServerHandler(config)

    client_config = A2AClientConfig(
        slimrpc_config=SlimRpcConfig(
            namespace="default",
            group="default",
            name="tls_client",
            slim_url=tls_dataplane.https_by_ip,
            tls_insecure=False,
        ),
    )
    request = SendMessageRequest(
        message=new_text_message("hello over TLS", role=Role.ROLE_USER)
    )

    await handler.setup()
    client: Client | None = None
    try:
        # One client only: every factory call opens a connection that is
        # never closed, and a second one for the same endpoint is refused.
        factory = A2AClientFactory(client_config)
        client = await factory.create(card=minimal_agent_card(agent, ["slimrpc"]))

        output = ""
        last_error: Exception | None = None
        for _ in range(8):  # the server needs a moment to become reachable
            try:
                output = await asyncio.wait_for(_collect_text(client, request), 10)
                if output:
                    break
            except Exception as exc:  # retry until reachable
                last_error = exc
            await asyncio.sleep(1)

        assert "Hello from" in output, f"no reply over TLS (last error: {last_error})"
    finally:
        if client is not None:
            await client.close()
        await handler.teardown()
