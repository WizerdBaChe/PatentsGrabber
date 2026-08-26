"""Local SQLite store — the personal patent library, not a cache.

BR-5 (docs/01-concept-note.md): everything looked up is kept. The distinction
matters for schema, not just wording — a cache may drop rows on a whim and needs
no history, whereas this table is the seed of the later "memory" / landscape
extension and therefore keeps the query that led here and when it happened.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    number        TEXT PRIMARY KEY,
    title         TEXT,
    source        TEXT NOT NULL,
    fetched_at    TEXT NOT NULL,
    payload       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lookups (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    query         TEXT NOT NULL,
    number        TEXT,
    ok            INTEGER NOT NULL,
    detail        TEXT,
    at            TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_lookups_at ON lookups(at DESC);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def get(self, number: str) -> dict | None:
        row = self.conn.execute(
            "SELECT payload, fetched_at FROM documents WHERE number = ?", (number,)
        ).fetchone()
        if not row:
            return None
        payload = json.loads(row["payload"])
        payload["_from_store"] = True
        payload["_fetched_at"] = row["fetched_at"]
        return payload

    def put(self, number: str, title: str | None, source: str, payload: dict) -> None:
        self.conn.execute(
            "INSERT INTO documents (number, title, source, fetched_at, payload) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(number) DO UPDATE SET "
            "  title=excluded.title, source=excluded.source, "
            "  fetched_at=excluded.fetched_at, payload=excluded.payload",
            (number, title, source, _now(), json.dumps(payload, ensure_ascii=False)),
        )
        self.conn.commit()

    def patch(self, number: str, updates: dict) -> bool:
        """Merge extra keys into a stored card WITHOUT restamping fetched_at.

        Enrichment arriving later (EPO drawings, family) is not a re-fetch of the
        document; moving the timestamp would make the library lie about when the
        document itself was read.
        """
        row = self.conn.execute(
            "SELECT payload FROM documents WHERE number = ?", (number,)
        ).fetchone()
        if not row:
            return False
        payload = json.loads(row["payload"])
        payload.update(updates)
        self.conn.execute(
            "UPDATE documents SET payload = ? WHERE number = ?",
            (json.dumps(payload, ensure_ascii=False), number),
        )
        self.conn.commit()
        return True

    def log_lookup(self, query: str, number: str | None, ok: bool, detail: str = "") -> None:
        self.conn.execute(
            "INSERT INTO lookups (query, number, ok, detail, at) VALUES (?, ?, ?, ?, ?)",
            (query, number, 1 if ok else 0, detail, _now()),
        )
        self.conn.commit()

    def recent(self, limit: int = 30) -> list[dict]:
        rows = self.conn.execute(
            "SELECT d.number, d.title, d.fetched_at "
            "FROM documents d ORDER BY d.fetched_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"]
