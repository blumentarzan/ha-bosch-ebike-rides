"""SQLite storage. Everything lives in /data, which Home Assistant backs up."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import zlib
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    color TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'mobile',
    client_id TEXT,
    access_token TEXT,
    refresh_token TEXT,
    expires_at REAL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'login_required',
    last_error TEXT,
    last_sync REAL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS pending_logins (
    state TEXT PRIMARY KEY,
    account_id INTEGER NOT NULL,
    verifier TEXT NOT NULL,
    client_id TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS bikes (
    account_id INTEGER NOT NULL,
    id TEXT NOT NULL,
    name TEXT,
    odometer_m REAL,
    PRIMARY KEY (account_id, id)
);
CREATE TABLE IF NOT EXISTS activities (
    account_id INTEGER NOT NULL,
    id TEXT NOT NULL,
    bike_id TEXT,
    start_ts INTEGER NOT NULL,
    end_ts INTEGER,
    tz TEXT,
    title TEXT,
    distance_m REAL,
    duration_s REAL,
    elevation_gain_m REAL,
    elevation_loss_m REAL,
    avg_speed_kmh REAL,
    max_speed_kmh REAL,
    avg_power_w REAL,
    avg_cadence REAL,
    calories REAL,
    start_odometer_m REAL,
    track_status TEXT NOT NULL DEFAULT 'pending',
    PRIMARY KEY (account_id, id)
);
CREATE INDEX IF NOT EXISTS ix_act_start ON activities(start_ts);
CREATE TABLE IF NOT EXISTS tracks (
    account_id INTEGER NOT NULL,
    activity_id TEXT NOT NULL,
    points BLOB,
    simple TEXT,
    cells TEXT,
    PRIMARY KEY (account_id, activity_id)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

ACTIVITY_FIELDS = (
    "bike_id", "start_ts", "end_ts", "tz", "title", "distance_m", "duration_s",
    "elevation_gain_m", "elevation_loss_m", "avg_speed_kmh", "max_speed_kmh",
    "avg_power_w", "avg_cadence", "calories", "start_odometer_m",
)

PALETTE = ["#2f7ed8", "#e8590c", "#2b9348", "#9c36b5", "#d6336c", "#0b7285", "#a07c00"]


class DB:
    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._lock = threading.RLock()

    # ---------------------------------------------------------------- generic
    def q(self, sql: str, args: tuple | list = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, args).fetchall()]

    def x(self, sql: str, args: tuple | list = ()) -> int:
        with self._lock:
            cur = self._conn.execute(sql, args)
            self._conn.commit()
            return cur.lastrowid or 0

    # ---------------------------------------------------------------- accounts
    def accounts(self) -> list[dict]:
        return self.q("SELECT * FROM accounts ORDER BY id")

    def account(self, account_id: int) -> dict | None:
        rows = self.q("SELECT * FROM accounts WHERE id=?", (account_id,))
        return rows[0] if rows else None

    def add_account(self, name: str, source: str, client_id: str | None, color: str | None) -> int:
        n = len(self.accounts())
        return self.x(
            "INSERT INTO accounts(name,color,source,client_id,created_at) VALUES(?,?,?,?,?)",
            (name, color or PALETTE[n % len(PALETTE)], source, client_id or None, time.time()),
        )

    def update_account(self, account_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ",".join(f"{k}=?" for k in fields)
        self.x(f"UPDATE accounts SET {cols} WHERE id=?", (*fields.values(), account_id))

    def delete_account(self, account_id: int) -> None:
        with self._lock:
            for table, col in (
                ("tracks", "account_id"), ("activities", "account_id"),
                ("bikes", "account_id"), ("pending_logins", "account_id"),
                ("accounts", "id"),
            ):
                self._conn.execute(f"DELETE FROM {table} WHERE {col}=?", (account_id,))
            self._conn.commit()

    # ---------------------------------------------------------------- logins
    def save_pending(self, state: str, account_id: int, verifier: str, client_id: str, redirect: str) -> None:
        self.x("DELETE FROM pending_logins WHERE created_at < ?", (time.time() - 3600,))
        self.x(
            "INSERT OR REPLACE INTO pending_logins VALUES(?,?,?,?,?,?)",
            (state, account_id, verifier, client_id, redirect, time.time()),
        )

    def latest_pending(self, account_id: int) -> dict | None:
        rows = self.q(
            "SELECT * FROM pending_logins WHERE account_id=? ORDER BY created_at DESC LIMIT 1",
            (account_id,),
        )
        return rows[0] if rows else None

    def clear_pending(self, account_id: int) -> None:
        self.x("DELETE FROM pending_logins WHERE account_id=?", (account_id,))

    # ---------------------------------------------------------------- bikes
    def upsert_bikes(self, account_id: int, bikes: list[dict]) -> None:
        with self._lock:
            for b in bikes:
                self._conn.execute(
                    "INSERT INTO bikes(account_id,id,name,odometer_m) VALUES(?,?,?,?) "
                    "ON CONFLICT(account_id,id) DO UPDATE SET name=excluded.name, "
                    "odometer_m=excluded.odometer_m",
                    (account_id, b["id"], b.get("name"), b.get("odometer_m")),
                )
            self._conn.commit()

    def bikes(self, account_id: int | None = None) -> list[dict]:
        if account_id is None:
            return self.q("SELECT * FROM bikes ORDER BY account_id, name")
        return self.q("SELECT * FROM bikes WHERE account_id=? ORDER BY name", (account_id,))

    # ---------------------------------------------------------------- activities
    def upsert_activities(self, account_id: int, acts: list[dict]) -> int:
        """Insert new rides, refresh summaries of known ones. Returns #new."""
        new = 0
        cols = ",".join(ACTIVITY_FIELDS)
        marks = ",".join("?" for _ in ACTIVITY_FIELDS)
        updates = ",".join(f"{c}=excluded.{c}" for c in ACTIVITY_FIELDS if c != "bike_id")
        with self._lock:
            known = {
                r[0] for r in self._conn.execute(
                    "SELECT id FROM activities WHERE account_id=?", (account_id,)
                )
            }
            for a in acts:
                if a["id"] not in known:
                    new += 1
                self._conn.execute(
                    f"INSERT INTO activities(account_id,id,{cols}) VALUES(?,?,{marks}) "
                    f"ON CONFLICT(account_id,id) DO UPDATE SET {updates}, "
                    "bike_id=COALESCE(activities.bike_id, excluded.bike_id)",
                    (account_id, a["id"], *(a.get(c) for c in ACTIVITY_FIELDS)),
                )
            self._conn.commit()
        return new

    def set_bike_ids(self, account_id: int, mapping: dict[str, str]) -> None:
        with self._lock:
            for act_id, bike_id in mapping.items():
                self._conn.execute(
                    "UPDATE activities SET bike_id=? WHERE account_id=? AND id=?",
                    (bike_id, account_id, act_id),
                )
            self._conn.commit()

    def pending_tracks(self, account_id: int, limit: int = 1000) -> list[str]:
        return [
            r["id"] for r in self.q(
                "SELECT id FROM activities WHERE account_id=? AND track_status IN ('pending','error') "
                "ORDER BY start_ts DESC LIMIT ?",
                (account_id, limit),
            )
        ]

    def save_track(self, account_id: int, activity_id: str, points: list, simple: list, cells: str) -> None:
        blob = zlib.compress(json.dumps(points, separators=(",", ":")).encode(), 6)
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO tracks VALUES(?,?,?,?,?)",
                (account_id, activity_id, blob,
                 json.dumps(simple, separators=(",", ":")), cells),
            )
            self._conn.execute(
                "UPDATE activities SET track_status=? WHERE account_id=? AND id=?",
                ("ok" if points else "empty", account_id, activity_id),
            )
            self._conn.commit()

    def mark_track(self, account_id: int, activity_id: str, status: str) -> None:
        self.x(
            "UPDATE activities SET track_status=? WHERE account_id=? AND id=?",
            (status, account_id, activity_id),
        )

    def track_points(self, account_id: int, activity_id: str) -> list:
        rows = self.q(
            "SELECT points FROM tracks WHERE account_id=? AND activity_id=?",
            (account_id, activity_id),
        )
        if not rows or not rows[0]["points"]:
            return []
        return json.loads(zlib.decompress(rows[0]["points"]))

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        rows = self.q("SELECT value FROM meta WHERE key=?", (key,))
        return rows[0]["value"] if rows else default

    def set_meta(self, key: str, value: str) -> None:
        self.x("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, value))

    # ---------------------------------------------------------------- queries
    def filtered(self, f: "Filter", with_geo: str | None = None) -> list[dict]:
        where, args = f.sql()
        geo = ""
        join = ""
        if with_geo:
            geo = f", t.{with_geo} AS geo"
            join = "LEFT JOIN tracks t ON t.account_id=a.account_id AND t.activity_id=a.id"
        return self.q(
            f"SELECT a.*{geo} FROM activities a {join} WHERE {where} ORDER BY a.start_ts",
            args,
        )


class Filter:
    def __init__(
        self,
        accounts: list[int] | None = None,
        bikes: list[str] | None = None,
        start: int | None = None,
        end: int | None = None,
    ) -> None:
        self.accounts, self.bikes, self.start, self.end = accounts, bikes, start, end

    def sql(self) -> tuple[str, list]:
        parts, args = ["1=1"], []
        if self.accounts:
            parts.append(f"a.account_id IN ({','.join('?' * len(self.accounts))})")
            args += self.accounts
        if self.bikes:
            parts.append(f"a.bike_id IN ({','.join('?' * len(self.bikes))})")
            args += self.bikes
        if self.start is not None:
            parts.append("a.start_ts >= ?")
            args.append(self.start)
        if self.end is not None:
            parts.append("a.start_ts < ?")
            args.append(self.end)
        return " AND ".join(parts), args
