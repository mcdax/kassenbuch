import base64
import json
import socket

import pytest


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Tests must never talk to Lidl (or anything else)."""
    def blocked(*args, **kwargs):
        raise OSError("network access blocked in tests")
    monkeypatch.setattr(socket.socket, "connect", blocked)


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("KASSENBUCH_DATA", str(tmp_path / "data"))
    return tmp_path / "data"


def fake_jwt(sub: str) -> str:
    def encode(data):
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()
    return f"{encode({'alg': 'none'})}.{encode({'sub': sub})}.signature"
