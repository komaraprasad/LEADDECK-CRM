import os
import uuid
import pytest
from app.db import Database

# Opt-in Postgres run: set LEADDECK_TEST_PG_URL to a throwaway Postgres (e.g. a Neon test database).
# Every in-memory SQLite database a test creates is then made on Postgres instead, each in its own
# fresh schema, dropped at the end of the test. Without the variable the suite stays offline on SQLite.
PG_URL = os.environ.get("LEADDECK_TEST_PG_URL", "").strip()


def _pg_url_with_schema(schema: str) -> str:
    url = PG_URL.replace("-pooler.", ".")  # startup options need a direct (non-pooled) connection
    return url + ("&" if "?" in url else "?") + f"options=-csearch_path%3D{schema}"


@pytest.fixture(autouse=True)
def _postgres_mode(monkeypatch):
    if not PG_URL:
        yield
        return
    import psycopg
    made = []
    real_init = Database.__init__

    def init(self, url):
        if url in ("sqlite:///:memory:", ":memory:", "sqlite:///"):
            schema = "t_" + uuid.uuid4().hex[:12]
            with psycopg.connect(PG_URL, autocommit=True) as c:
                c.execute(f"CREATE SCHEMA {schema}")
            made.append(schema)
            url = _pg_url_with_schema(schema)
        real_init(self, url)

    monkeypatch.setattr(Database, "__init__", init)
    yield
    if made:
        with psycopg.connect(PG_URL, autocommit=True) as c:
            for s in made:
                c.execute(f"DROP SCHEMA IF EXISTS {s} CASCADE")


@pytest.fixture
def db(_postgres_mode):
    d = Database("sqlite:///:memory:")
    d.init_schema()
    return d


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Acceptance criterion: the suite runs with no network. Any real socket connection fails the test.
    In the opt-in Postgres run only the database host may be reached."""
    import socket
    real_connect = socket.socket.connect
    allowed_port = 5432 if PG_URL else None

    def guarded(self, address, *a, **k):
        if allowed_port and isinstance(address, tuple) and len(address) >= 2 and address[1] == allowed_port:
            return real_connect(self, address, *a, **k)
        raise RuntimeError("network access attempted in a test")
    monkeypatch.setattr(socket.socket, "connect", guarded)
