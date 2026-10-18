import errno
import socket
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from cairn.cli import _server_socket, parser
from cairn.server import create_app
from cairn.storage import Repository


def test_default_port_collision_warns_and_reserves_fallback(capsys):
    assert parser().parse_args(["server"]).port is None
    with closing(socket.socket()) as occupied:
        try:
            occupied.bind(("127.0.0.1", 8765))
            occupied.listen()
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE:
                raise
        with closing(_server_socket("127.0.0.1", None)) as listener:
            assert listener.getsockname()[1] != 8765
            assert "Warning: port 8765 is occupied" in capsys.readouterr().err
            with pytest.raises(OSError):
                _server_socket("127.0.0.1", listener.getsockname()[1])


def test_explicit_busy_port_fails():
    with closing(_server_socket("127.0.0.1", 0)) as listener:
        with pytest.raises(OSError) as error:
            _server_socket("127.0.0.1", listener.getsockname()[1])
        assert error.value.errno == errno.EADDRINUSE


def test_login_link_survives_restart_and_rejects_changed_token(tmp_path):
    with closing(Repository(tmp_path)) as repo:
        for _ in range(2):
            with TestClient(create_app(repo, token="persistent-token", ui=True)) as client:
                for _ in range(2):
                    assert (
                        client.post(
                            "/ui-api/login", json={"link_token": "persistent-token"}
                        ).status_code
                        == 200
                    )
                    assert client.post("/ui-api/logout").status_code == 200
        with TestClient(create_app(repo, token="replacement-token", ui=True)) as client:
            assert (
                client.post("/ui-api/login", json={"link_token": "persistent-token"}).status_code
                == 401
            )


def test_server_ctrl_c_runs_cleanup_without_traceback(tmp_path):
    import os
    import signal
    import subprocess
    import sys

    with closing(socket.socket()) as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "cairn",
            "--repo",
            str(tmp_path / "repo"),
            "server",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--no-auth",
            "--no-ui",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        import time

        import httpx

        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail(process.communicate()[0])
            try:
                if httpx.get(f"http://127.0.0.1:{port}/v1/info", timeout=0.2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.05)
        else:
            pytest.fail("Server did not start")
        os.kill(process.pid, signal.SIGINT)
        output = process.communicate(timeout=10)[0]
        assert process.returncode == 0, output
        assert "Application shutdown complete" in output
        assert "Traceback" not in output and "KeyboardInterrupt" not in output
        with closing(socket.socket()) as available:
            available.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            available.bind(("127.0.0.1", port))
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
