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
