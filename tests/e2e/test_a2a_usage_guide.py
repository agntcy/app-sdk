# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""
E2E tests for the A2A Usage Guide examples.

These tests validate that the code examples in docs/A2A_USAGE_GUIDE.md
actually work end-to-end by:
  1. Launching the server script as a subprocess
  2. Waiting for it to become ready
  3. Running the client script as a subprocess
  4. Asserting the client output contains expected text
  5. Tearing down the server

Requires Docker services running:
  docker-compose -f services/docker/docker-compose.yaml up
"""

import os
import signal
import subprocess
import time

import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SLIM_ENDPOINT = "http://localhost:46357"
NATS_ENDPOINT = "localhost:4222"

GUIDE_DIR = os.path.join(os.path.dirname(__file__), "..", "guide_examples")

SERVER_STARTUP_WAIT = 4  # seconds to let server start up
CLIENT_TIMEOUT = 30  # seconds before killing the client


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------


def _launch_server(cmd: list[str]) -> subprocess.Popen:
    """Launch a server subprocess with its own process group."""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        preexec_fn=os.setsid,
    )
    return proc


def _run_client(cmd: list[str], timeout: int = CLIENT_TIMEOUT) -> tuple[int, str]:
    """Run a client subprocess and capture its output."""
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        text=True,
    )
    return result.returncode, result.stdout


def _kill_server(proc: subprocess.Popen) -> None:
    """Terminate the server and its process group."""
    if proc.poll() is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)


def _server_output(proc: subprocess.Popen) -> str:
    """Read whatever the server has written to stdout so far (non-blocking)."""
    import select

    output = ""
    if proc.stdout and proc.stdout.fileno():
        while select.select([proc.stdout], [], [], 0)[0]:
            line = proc.stdout.readline()
            if not line:
                break
            output += line.decode("utf-8", errors="replace")
    return output


# ---------------------------------------------------------------------------
# Example 1 — SlimRPC
# ---------------------------------------------------------------------------


class TestExample1SlimRPC:
    """Test the SlimRPC example from A2A_USAGE_GUIDE.md."""

    def test_weather_agent_srpc(self):
        """Launch SlimRPC weather server, run client, verify response."""
        server_script = os.path.join(GUIDE_DIR, "weather_agent_srpc.py")
        client_script = os.path.join(GUIDE_DIR, "weather_client_srpc.py")

        agent_name = "default/default/weather-agent-srpc-test"

        # Launch server
        server_proc = _launch_server(
            [
                "uv", "run", "python", server_script,
                "--endpoint", SLIM_ENDPOINT,
                "--name", agent_name,
            ]
        )

        try:
            # Wait for server to start
            time.sleep(SERVER_STARTUP_WAIT)

            # Verify server is still running
            assert server_proc.poll() is None, (
                f"Server exited prematurely with code {server_proc.returncode}. "
                f"Output: {_server_output(server_proc)}"
            )

            # Run client
            returncode, output = _run_client(
                [
                    "uv", "run", "python", client_script,
                    "--endpoint", SLIM_ENDPOINT,
                    "--agent-name", agent_name,
                ]
            )

            print(f"Client exit code: {returncode}")
            print(f"Client output:\n{output}")

            # Assert success
            assert returncode == 0, (
                f"Client exited with code {returncode}. Output:\n{output}"
            )
            assert "SUCCESS" in output, (
                f"Expected 'SUCCESS' in client output. Got:\n{output}"
            )
            assert "sunny" in output.lower() or "75" in output, (
                f"Expected weather report in output. Got:\n{output}"
            )

        finally:
            _kill_server(server_proc)


# ---------------------------------------------------------------------------
# Example 2 — Experimental Patterns (SLIM)
# ---------------------------------------------------------------------------


class TestExample2ExperimentalPatternsSLIM:
    """Test the Experimental Patterns (SLIM) example from A2A_USAGE_GUIDE.md."""

    def test_weather_agent_slim_patterns(self):
        """Launch patterns weather server over SLIM, run client, verify response."""
        server_script = os.path.join(GUIDE_DIR, "weather_agent.py")
        client_script = os.path.join(GUIDE_DIR, "weather_client.py")

        # Launch server
        server_proc = _launch_server(
            [
                "uv", "run", "python", server_script,
                "--transport", "SLIM",
                "--endpoint", SLIM_ENDPOINT,
            ]
        )

        try:
            # Wait for server to start
            time.sleep(SERVER_STARTUP_WAIT)

            # Verify server is still running
            assert server_proc.poll() is None, (
                f"Server exited prematurely with code {server_proc.returncode}. "
                f"Output: {_server_output(server_proc)}"
            )

            # Run client
            returncode, output = _run_client(
                [
                    "uv", "run", "python", client_script,
                    "--transport", "SLIM",
                    "--endpoint", SLIM_ENDPOINT,
                ]
            )

            print(f"Client exit code: {returncode}")
            print(f"Client output:\n{output}")

            # Assert success
            assert returncode == 0, (
                f"Client exited with code {returncode}. Output:\n{output}"
            )
            assert "SUCCESS" in output, (
                f"Expected 'SUCCESS' in client output. Got:\n{output}"
            )
            assert "sunny" in output.lower() or "75" in output, (
                f"Expected weather report in output. Got:\n{output}"
            )

        finally:
            _kill_server(server_proc)


# ---------------------------------------------------------------------------
# Example 2 — Experimental Patterns (NATS)
# ---------------------------------------------------------------------------


class TestExample2ExperimentalPatternsNATS:
    """Test the Experimental Patterns (NATS) example from A2A_USAGE_GUIDE.md."""

    def test_weather_agent_nats_patterns(self):
        """Launch patterns weather server over NATS, run client, verify response."""
        server_script = os.path.join(GUIDE_DIR, "weather_agent.py")
        client_script = os.path.join(GUIDE_DIR, "weather_client.py")

        # Launch server
        server_proc = _launch_server(
            [
                "uv", "run", "python", server_script,
                "--transport", "NATS",
                "--endpoint", NATS_ENDPOINT,
            ]
        )

        try:
            # Wait for server to start
            time.sleep(SERVER_STARTUP_WAIT)

            # Verify server is still running
            assert server_proc.poll() is None, (
                f"Server exited prematurely with code {server_proc.returncode}. "
                f"Output: {_server_output(server_proc)}"
            )

            # Run client
            returncode, output = _run_client(
                [
                    "uv", "run", "python", client_script,
                    "--transport", "NATS",
                    "--endpoint", NATS_ENDPOINT,
                ]
            )

            print(f"Client exit code: {returncode}")
            print(f"Client output:\n{output}")

            # Assert success
            assert returncode == 0, (
                f"Client exited with code {returncode}. Output:\n{output}"
            )
            assert "SUCCESS" in output, (
                f"Expected 'SUCCESS' in client output. Got:\n{output}"
            )
            assert "sunny" in output.lower() or "75" in output, (
                f"Expected weather report in output. Got:\n{output}"
            )

        finally:
            _kill_server(server_proc)
