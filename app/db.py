"""Small database layer: SQLite (tests/local) and Postgres (production) behind one interface.

SQL is written with `?` placeholders and portable syntax. Explicit BEGIN/COMMIT with SAVEPOINTs
lets callers do dry runs (roll back everything) and skip a single bad record.
NOTE: the Postgres branch (psycopg) could not be exercised in the build environment.
"""
from __future__ import annotations
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from app.schema import MIGRATIONS


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, url: str):
        self._lock = threading.RLock()
        url = (url or "").strip()  # a value pasted into a dashboard often carries a trailing newline
        self._url = url
        self._depth = 0
        if url.startswith(("postgres://", "postgresql://", "postgresql+psycopg://")):
            self.dialect = "postgres"
            self._url = url.replace("postgresql+psycopg://", "postgresql://")
            self._conn = self._connect_pg()
        else:
            self.dialect = "sqlite"
            path = url.split("sqlite:///", 1)[1] if url.startswith("sqlite:///") else url
            self._conn = sqlite3.connect(path or ":memory:", check_same_thread=False, isolation_level=None)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys=OFF")

    # -- connection handling -------------------------------------------------
    def _connect_pg(self):
        import psycopg  # imported lazily; not available in every environment
        from psycopg.rows import dict_row
        # prepare_threshold=None: no server-side prepared statements, which a connection pooler
        # (e.g. Neon's -pooler endpoint / PgBouncer) can lose between queries.
        return psycopg.connect(self._url, autocommit=True, row_factory=dict_row, prepare_threshold=None)

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.dialect == "postgres" else sql

    def _run(self, sql, params=()):
        sql = self._sql(sql)
        try:
            cur = self._conn.cursor()
            cur.execute(sql, tuple(params))
            return cur
        except Exception as e:
            # Neon computes sleep and drop idle connections: reconnect once, outside a transaction only.
            if self.dialect == "postgres" and self._depth == 0 and e.__class__.__name__ in ("OperationalError", "InterfaceError"):
                self._conn = self._connect_pg()
                cur = self._conn.cursor()
                cur.execute(sql, tuple(params))
                return cur
            raise

    # -- public API ----------------------------------------------------------
    def execute(self, sql, params=()) -> int:
        with self._lock:
            return self._run(sql, params).rowcount

    def query(self, sql, params=()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._run(sql, params).fetchall()]

    def one(self, sql, params=()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # tables keyed by something other than an `id` column: insert() returns None for them on both dialects
    _NO_ID_TABLES = frozenset({"campaign_leads", "erasures", "schema_version"})

    def insert(self, table: str, values: dict) -> int | None:
        cols = list(values)
        sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})"
        with self._lock:
            if table in self._NO_ID_TABLES:
                self._run(sql, list(values.values()))
                return None
            if self.dialect == "postgres":
                return self._run(sql + " RETURNING id", list(values.values())).fetchone()["id"]
            return self._run(sql, list(values.values())).lastrowid

    def update(self, table: str, row_id: int, values: dict) -> None:
        if values:
            sets = ", ".join(f"{c}=?" for c in values)
            self.execute(f"UPDATE {table} SET {sets} WHERE id=?", [*values.values(), row_id])

    @contextmanager
    def transaction(self, rollback: bool = False):
        """BEGIN ... COMMIT (or ROLLBACK if `rollback` or on error). Not re-entrant."""
        with self._lock:
            self._run("BEGIN")
            self._depth += 1
            try:
                yield self
            except BaseException:
                self._depth -= 1
                self._run("ROLLBACK")
                raise
            self._depth -= 1
            self._run("ROLLBACK" if rollback else "COMMIT")

    @contextmanager
    def savepoint(self, name="sp"):
        with self._lock:
            self._run(f"SAVEPOINT {name}")
            try:
                yield
            except BaseException:
                self._run(f"ROLLBACK TO SAVEPOINT {name}")
                self._run(f"RELEASE SAVEPOINT {name}")
                raise
            self._run(f"RELEASE SAVEPOINT {name}")

    def init_schema(self) -> None:
        pk = "id INTEGER PRIMARY KEY AUTOINCREMENT" if self.dialect == "sqlite" else "id BIGSERIAL PRIMARY KEY"
        self.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
        row = self.one("SELECT MAX(version) AS v FROM schema_version")
        current = (row or {}).get("v") or 0
        for i, stmts in enumerate(MIGRATIONS, start=1):
            if i <= current:
                continue
            with self.transaction():
                for s in stmts:
                    self._run(s.replace("{PK}", pk))
                self._run("INSERT INTO schema_version (version) VALUES (%d)" % i)

    def table_names(self) -> set[str]:
        if self.dialect == "sqlite":
            rows = self.query("SELECT name FROM sqlite_master WHERE type='table'")
        else:
            rows = self.query("SELECT table_name AS name FROM information_schema.tables WHERE table_schema = current_schema()")
        return {r["name"] for r in rows}

    def size_bytes(self) -> int | None:
        try:
            if self.dialect == "sqlite":
                r = self.one("SELECT (SELECT page_count FROM pragma_page_count) * (SELECT page_size FROM pragma_page_size) AS b")
            else:
                r = self.one("SELECT pg_database_size(current_database()) AS b")
            return int(r["b"]) if r else None
        except Exception:
            return None
