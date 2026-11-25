import pytest
from app.db import Database


@pytest.fixture
def db():
    d = Database("sqlite:///:memory:")
    d.init_schema()
    return d


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Acceptance criterion: the suite runs with no network. Any real socket connection fails the test."""
    import socket
    def blocked(self, *a, **k):
        raise RuntimeError("network access attempted in a test")
    monkeypatch.setattr(socket.socket, "connect", blocked)
