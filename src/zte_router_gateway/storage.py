"""SQLite 存储：网速采样、设备快照、限速计划。

只用一个连接，所有操作串行化后丢到线程里执行（sqlite3 本身很快，
这里不需要额外依赖）。WAL 模式保证后台采样写入不会阻塞 API 查询。
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any, Sequence

from .router.models import Device
from .util import now_ms

_SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    mac          TEXT PRIMARY KEY,
    name         TEXT NOT NULL DEFAULT '',
    ip           TEXT,
    online       INTEGER NOT NULL DEFAULT 0,
    last_seen    INTEGER NOT NULL,
    last_down_bps INTEGER NOT NULL DEFAULT 0,
    last_up_bps   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS speed_samples (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    mac      TEXT NOT NULL,
    ts       INTEGER NOT NULL,
    down_bps INTEGER NOT NULL,
    up_bps   INTEGER NOT NULL,
    online   INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_samples_mac_ts ON speed_samples(mac, ts);
CREATE INDEX IF NOT EXISTS idx_samples_ts ON speed_samples(ts);

CREATE TABLE IF NOT EXISTS qos_schedules (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    mac            TEXT NOT NULL,
    down_kbps      INTEGER NOT NULL,
    up_kbps        INTEGER NOT NULL,
    starts_at      INTEGER NOT NULL,
    ends_at        INTEGER NOT NULL,
    state          TEXT NOT NULL,
    had_limit      INTEGER NOT NULL DEFAULT 0,
    prev_down_kbps INTEGER,
    prev_up_kbps   INTEGER,
    prev_inst_id   TEXT,
    attempts       INTEGER NOT NULL DEFAULT 0,
    next_attempt_at INTEGER,
    last_error     TEXT,
    created_at     INTEGER NOT NULL,
    updated_at     INTEGER NOT NULL,
    applied_at     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_sched_state
    ON qos_schedules(state, starts_at, ends_at);
"""

_SCHEDULE_FIELDS = {
    "state",
    "had_limit",
    "prev_down_kbps",
    "prev_up_kbps",
    "prev_inst_id",
    "attempts",
    "next_attempt_at",
    "last_error",
    "applied_at",
    "updated_at",
}


def _row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _fetchall(conn: sqlite3.Connection, sql: str, params: Sequence[Any] = ()):
    return conn.execute(sql, params).fetchall()


def _commit(conn: sqlite3.Connection) -> None:
    conn.commit()


class Storage:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------- lifecycle

    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(_SCHEMA)
        # v2：采样值从「固件 DownloadSpeed（单位不可靠）」改为
        # BytesReceived/BytesSend 差分得到的 B/s，旧样本直接清空
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 2:
            conn.execute("DELETE FROM speed_samples")
            conn.execute("UPDATE devices SET last_down_bps = 0, last_up_bps = 0")
            conn.execute("PRAGMA user_version = 2")
        conn.commit()
        self._conn = conn

    async def close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            await asyncio.to_thread(conn.close)

    async def _run(self, func, *args):
        conn = self._conn
        if conn is None:
            raise RuntimeError("storage 尚未打开")
        async with self._lock:
            return await asyncio.to_thread(func, conn, *args)

    # ------------------------------------------------------------- snapshots

    async def record_snapshot(
        self,
        devices: Sequence[Device],
        samples: Sequence[tuple[str, int, int, int]],
        ts: int,
    ) -> None:
        """samples: (mac, ts, down_bps, up_bps)，单位 B/s。"""
        sample_by_mac = {mac: (down, up) for mac, _ts, down, up in samples}
        device_rows = []
        for device in devices:
            if not device.mac:
                continue
            key = device.mac.lower()
            down, up = sample_by_mac.get(key, (0, 0))
            if not device.online:
                down = up = 0
            device_rows.append(
                (key, device.name, device.ip, int(device.online), ts, down, up)
            )
        # 离线设备的速率没有意义，不写采样，曲线自然断开
        sample_rows = [(mac, ts, down, up, 1) for mac, _ts, down, up in samples]
        await self._run(_record_snapshot, device_rows, sample_rows)

    async def latest_rates(self) -> dict[str, tuple[int, int]]:
        rows = await self._run(
            _fetchall, "SELECT mac, last_down_bps, last_up_bps FROM devices"
        )
        return {
            row["mac"]: (int(row["last_down_bps"]), int(row["last_up_bps"]))
            for row in rows
        }

    async def prune_samples(self, before_ts: int) -> int:
        def _prune(conn: sqlite3.Connection, ts: int) -> int:
            cursor = conn.execute("DELETE FROM speed_samples WHERE ts < ?", (ts,))
            conn.commit()
            return cursor.rowcount

        return await self._run(_prune, before_ts)

    async def known_devices(self) -> list[dict[str, Any]]:
        rows = await self._run(
            _fetchall,
            "SELECT mac, name, ip, online, last_seen, last_down_bps, last_up_bps"
            " FROM devices ORDER BY online DESC, name COLLATE NOCASE, mac",
        )
        return [dict(row) for row in rows]

    async def device_names(self) -> dict[str, str]:
        rows = await self._run(_fetchall, "SELECT mac, name FROM devices")
        return {row["mac"]: row["name"] for row in rows}

    async def series(
        self,
        macs: Sequence[str],
        from_ts: int,
        to_ts: int,
        step_ms: int,
    ) -> dict[str, dict[str, list[list[int]]]]:
        if not macs:
            return {}

        placeholders = ",".join("?" for _ in macs)
        sql = (
            "SELECT mac, (ts / ?) * ? AS bucket,"
            " CAST(AVG(down_bps) AS INTEGER) AS down,"
            " CAST(AVG(up_bps) AS INTEGER) AS up"
            " FROM speed_samples"
            f" WHERE ts >= ? AND ts <= ? AND mac IN ({placeholders})"
            " GROUP BY mac, bucket ORDER BY bucket"
        )
        params: list[Any] = [step_ms, step_ms, from_ts, to_ts, *macs]
        rows = await self._run(_fetchall, sql, params)

        result: dict[str, dict[str, list[list[int]]]] = {
            mac: {"down": [], "up": []} for mac in macs
        }
        for row in rows:
            entry = result.setdefault(row["mac"], {"down": [], "up": []})
            bucket = int(row["bucket"])
            entry["down"].append([bucket, int(row["down"] or 0)])
            entry["up"].append([bucket, int(row["up"] or 0)])
        return result

    # ------------------------------------------------------------- schedules

    async def create_schedule(
        self,
        *,
        mac: str,
        down_kbps: int,
        up_kbps: int,
        starts_at: int,
        ends_at: int,
    ) -> dict[str, Any]:
        now = now_ms()

        def _insert(conn: sqlite3.Connection) -> int:
            cursor = conn.execute(
                "INSERT INTO qos_schedules"
                " (mac, down_kbps, up_kbps, starts_at, ends_at, state,"
                "  created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
                (mac, down_kbps, up_kbps, starts_at, ends_at, now, now),
            )
            conn.commit()
            return int(cursor.lastrowid)

        sid = await self._run(_insert)
        row = await self.get_schedule(sid)
        assert row is not None
        return row

    async def cancel_open_schedules(self, mac: str) -> int:
        """同一设备只保留一个有效限速：新建前把旧的 pending/active 标记为取消。"""

        def _cancel(conn: sqlite3.Connection) -> int:
            cursor = conn.execute(
                "UPDATE qos_schedules SET state = 'cancelled', updated_at = ?"
                " WHERE mac = ? AND state IN ('pending', 'active')",
                (now_ms(), mac),
            )
            conn.commit()
            return cursor.rowcount

        return await self._run(_cancel)

    async def get_schedule(self, sid: int) -> dict[str, Any] | None:
        row = await self._run(
            lambda conn: conn.execute(
                "SELECT * FROM qos_schedules WHERE id = ?", (sid,)
            ).fetchone()
        )
        return _row_dict(row)

    async def list_schedules(self, state: str | None = None) -> list[dict[str, Any]]:
        if state:
            rows = await self._run(
                _fetchall,
                "SELECT * FROM qos_schedules WHERE state = ? ORDER BY id DESC",
                (state,),
            )
        else:
            rows = await self._run(
                _fetchall, "SELECT * FROM qos_schedules ORDER BY id DESC"
            )
        return [dict(row) for row in rows]

    async def due_pending(self, now: int) -> list[dict[str, Any]]:
        rows = await self._run(
            _fetchall,
            "SELECT * FROM qos_schedules"
            " WHERE state = 'pending' AND starts_at <= ?"
            "   AND (next_attempt_at IS NULL OR next_attempt_at <= ?)"
            " ORDER BY starts_at",
            (now, now),
        )
        return [dict(row) for row in rows]

    async def due_active(self, now: int) -> list[dict[str, Any]]:
        rows = await self._run(
            _fetchall,
            "SELECT * FROM qos_schedules"
            " WHERE state = 'active' AND ends_at > 0 AND ends_at <= ?"
            "   AND (next_attempt_at IS NULL OR next_attempt_at <= ?)"
            " ORDER BY ends_at",
            (now, now),
        )
        return [dict(row) for row in rows]

    async def active_schedules(self) -> list[dict[str, Any]]:
        rows = await self._run(
            _fetchall,
            "SELECT * FROM qos_schedules WHERE state = 'active' ORDER BY id",
        )
        return [dict(row) for row in rows]

    async def update_schedule(self, sid: int, **fields: Any) -> None:
        updates = {
            key: (int(value) if isinstance(value, bool) else value)
            for key, value in fields.items()
            if key in _SCHEDULE_FIELDS
        }
        if "updated_at" not in updates:
            updates["updated_at"] = now_ms()
        assignments = ", ".join(f"{key} = ?" for key in updates)
        params: list[Any] = [*updates.values(), sid]

        def _update(conn: sqlite3.Connection, sql: str, values: Sequence[Any]) -> None:
            conn.execute(sql, values)
            conn.commit()

        await self._run(
            _update, f"UPDATE qos_schedules SET {assignments} WHERE id = ?", params
        )


def _record_snapshot(
    conn: sqlite3.Connection,
    device_rows: Sequence[tuple],
    sample_rows: Sequence[tuple],
) -> None:
    with conn:
        conn.executemany(
            "INSERT INTO devices"
            " (mac, name, ip, online, last_seen, last_down_bps, last_up_bps)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(mac) DO UPDATE SET"
            "  name = excluded.name,"
            "  ip = excluded.ip,"
            "  online = excluded.online,"
            "  last_seen = excluded.last_seen,"
            "  last_down_bps = excluded.last_down_bps,"
            "  last_up_bps = excluded.last_up_bps",
            device_rows,
        )
        conn.executemany(
            "INSERT INTO speed_samples (mac, ts, down_bps, up_bps, online)"
            " VALUES (?, ?, ?, ?, ?)",
            sample_rows,
        )
