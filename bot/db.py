from __future__ import annotations

import csv
import io
from collections.abc import Iterable

import aiosqlite


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA journal_mode=WAL;")
        await self.conn.execute("PRAGMA foreign_keys=ON;")
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS voice_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                channel_name TEXT NOT NULL,
                join_ts INTEGER NOT NULL,
                leave_ts INTEGER NOT NULL,
                duration_seconds INTEGER NOT NULL CHECK(duration_seconds >= 0),
                counted_seconds INTEGER NOT NULL CHECK(counted_seconds >= 0),
                is_afk INTEGER NOT NULL DEFAULT 0,
                solo_seconds INTEGER NOT NULL DEFAULT 0,
                self_deaf_seconds INTEGER NOT NULL DEFAULT 0,
                muted_deafened_seconds INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        await self._ensure_column("counted_seconds", "INTEGER NOT NULL DEFAULT 0")
        await self._ensure_column("solo_seconds", "INTEGER NOT NULL DEFAULT 0")
        await self._ensure_column("self_deaf_seconds", "INTEGER NOT NULL DEFAULT 0")
        await self._ensure_column("muted_deafened_seconds", "INTEGER NOT NULL DEFAULT 0")
        await self.conn.execute(
            "UPDATE voice_sessions SET counted_seconds = duration_seconds WHERE counted_seconds = 0 AND duration_seconds > 0"
        )
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_user_period ON voice_sessions(guild_id, user_id, join_ts, leave_ts);"
        )
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_channel_period ON voice_sessions(guild_id, channel_id, join_ts, leave_ts);"
        )
        await self.conn.commit()

    async def _ensure_column(self, column_name: str, column_def: str) -> None:
        assert self.conn is not None
        rows = await self.conn.execute_fetchall("PRAGMA table_info(voice_sessions)")
        existing = {str(row["name"]) for row in rows}
        if column_name not in existing:
            await self.conn.execute(f"ALTER TABLE voice_sessions ADD COLUMN {column_name} {column_def}")

    async def close(self) -> None:
        if self.conn is not None:
            await self.conn.close()
            self.conn = None

    async def add_session(
        self,
        guild_id: int,
        user_id: int,
        channel_id: int,
        channel_name: str,
        join_ts: int,
        leave_ts: int,
        duration_seconds: int,
        counted_seconds: int,
        is_afk: bool,
        solo_seconds: int,
        self_deaf_seconds: int,
        muted_deafened_seconds: int,
    ) -> None:
        assert self.conn is not None
        await self.conn.execute(
            """
            INSERT INTO voice_sessions (
                guild_id, user_id, channel_id, channel_name,
                join_ts, leave_ts, duration_seconds, counted_seconds,
                is_afk, solo_seconds, self_deaf_seconds, muted_deafened_seconds
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                user_id,
                channel_id,
                channel_name,
                join_ts,
                leave_ts,
                duration_seconds,
                counted_seconds,
                int(is_afk),
                solo_seconds,
                self_deaf_seconds,
                muted_deafened_seconds,
            ),
        )
        await self.conn.commit()

    def _overlap_seconds_expr(self, weight_column: str) -> str:
        return f"""
            CASE
                WHEN leave_ts <= COALESCE(?, -1) THEN 0
                ELSE CAST(
                    MAX(
                        0,
                        MIN(leave_ts, COALESCE(?, leave_ts)) - MAX(join_ts, COALESCE(?, join_ts))
                    ) * 1.0 * {weight_column} / CASE WHEN duration_seconds > 0 THEN duration_seconds ELSE 1 END
                AS INTEGER)
            END
        """

    async def get_channel_totals(
        self,
        guild_id: int,
        user_id: int,
        after_ts: int | None,
        include_afk: bool,
    ) -> dict[int, int]:
        assert self.conn is not None
        params: list[object] = [after_ts, None, after_ts, guild_id, user_id]
        where = ["guild_id = ?", "user_id = ?"]
        if after_ts is not None:
            where.append("leave_ts > ?")
            params.append(after_ts)
        if not include_afk:
            where.append("is_afk = 0")
        query = f"""
            SELECT channel_id, SUM({self._overlap_seconds_expr('counted_seconds')}) AS total_seconds
            FROM voice_sessions
            WHERE {' AND '.join(where)}
            GROUP BY channel_id
        """
        rows = await self.conn.execute_fetchall(query, params)
        return {int(row["channel_id"]): int(row["total_seconds"] or 0) for row in rows}

    async def get_total_time(
        self,
        guild_id: int,
        user_id: int,
        after_ts: int | None,
        include_afk: bool,
    ) -> int:
        assert self.conn is not None
        params: list[object] = [after_ts, None, after_ts, guild_id, user_id]
        where = ["guild_id = ?", "user_id = ?"]
        if after_ts is not None:
            where.append("leave_ts > ?")
            params.append(after_ts)
        if not include_afk:
            where.append("is_afk = 0")
        query = f"SELECT COALESCE(SUM({self._overlap_seconds_expr('counted_seconds')}), 0) AS total_seconds FROM voice_sessions WHERE {' AND '.join(where)}"
        row = await self.conn.execute_fetchone(query, params)
        return int(row[0] or 0) if row else 0

    async def get_session_count(
        self,
        guild_id: int,
        user_id: int,
        after_ts: int | None,
        include_afk: bool,
    ) -> int:
        assert self.conn is not None
        params: list[object] = [guild_id, user_id]
        where = ["guild_id = ?", "user_id = ?"]
        if after_ts is not None:
            where.append("leave_ts > ?")
            params.append(after_ts)
        if not include_afk:
            where.append("is_afk = 0")
        query = f"SELECT COUNT(*) AS cnt FROM voice_sessions WHERE {' AND '.join(where)}"
        row = await self.conn.execute_fetchone(query, params)
        return int(row[0] or 0) if row else 0

    async def get_together_totals(
        self,
        guild_id: int,
        target_user_id: int,
        after_ts: int | None,
        include_afk: bool,
    ) -> list[tuple[int, int]]:
        assert self.conn is not None
        params: list[object] = [guild_id, target_user_id, guild_id]
        if after_ts is not None:
            params.append(after_ts)
            time_clause = " AND MIN(s1.leave_ts, s2.leave_ts) > ? "
        else:
            time_clause = ""
        afk_clause = "" if include_afk else " AND s1.is_afk = 0 AND s2.is_afk = 0 "
        overlap_start = "MAX(s1.join_ts, s2.join_ts"
        if after_ts is not None:
            overlap_start += ", ?"
            params.append(after_ts)
        overlap_start += ")"
        query = f"""
            SELECT
                s2.user_id AS other_user_id,
                SUM(
                    CASE
                        WHEN MIN(s1.leave_ts, s2.leave_ts) > {overlap_start}
                        THEN MIN(s1.leave_ts, s2.leave_ts) - {overlap_start}
                        ELSE 0
                    END
                ) AS together_seconds
            FROM voice_sessions s1
            JOIN voice_sessions s2
                ON s1.guild_id = s2.guild_id
                AND s1.channel_id = s2.channel_id
                AND s1.user_id != s2.user_id
                AND s1.join_ts < s2.leave_ts
                AND s2.join_ts < s1.leave_ts
            WHERE s1.guild_id = ?
                AND s1.user_id = ?
                AND s2.guild_id = ?
                {time_clause}
                {afk_clause}
            GROUP BY s2.user_id
            HAVING together_seconds > 0
            ORDER BY together_seconds DESC
        """
        rows = await self.conn.execute_fetchall(query, params)
        return [(int(row["other_user_id"]), int(row["together_seconds"] or 0)) for row in rows]

    async def get_leaderboard(
        self,
        guild_id: int,
        after_ts: int | None,
        include_afk: bool,
        limit: int = 10,
    ) -> list[tuple[int, int]]:
        assert self.conn is not None
        params: list[object] = [after_ts, None, after_ts, guild_id]
        where = ["guild_id = ?"]
        if after_ts is not None:
            where.append("leave_ts > ?")
            params.append(after_ts)
        if not include_afk:
            where.append("is_afk = 0")
        params.append(limit)
        query = f"""
            SELECT user_id, SUM({self._overlap_seconds_expr('counted_seconds')}) AS total_seconds
            FROM voice_sessions
            WHERE {' AND '.join(where)}
            GROUP BY user_id
            ORDER BY total_seconds DESC
            LIMIT ?
        """
        rows = await self.conn.execute_fetchall(query, params)
        return [(int(row["user_id"]), int(row["total_seconds"] or 0)) for row in rows]

    async def get_channel_leaderboard(
        self,
        guild_id: int,
        channel_id: int,
        after_ts: int | None,
        include_afk: bool,
        limit: int = 10,
    ) -> list[tuple[int, int]]:
        assert self.conn is not None
        params: list[object] = [after_ts, None, after_ts, guild_id, channel_id]
        where = ["guild_id = ?", "channel_id = ?"]
        if after_ts is not None:
            where.append("leave_ts > ?")
            params.append(after_ts)
        if not include_afk:
            where.append("is_afk = 0")
        params.append(limit)
        query = f"""
            SELECT user_id, SUM({self._overlap_seconds_expr('counted_seconds')}) AS total_seconds
            FROM voice_sessions
            WHERE {' AND '.join(where)}
            GROUP BY user_id
            ORDER BY total_seconds DESC
            LIMIT ?
        """
        rows = await self.conn.execute_fetchall(query, params)
        return [(int(row["user_id"]), int(row["total_seconds"] or 0)) for row in rows]

    async def get_recent_sessions(
        self,
        guild_id: int,
        user_id: int,
        limit: int = 10,
    ) -> list[aiosqlite.Row]:
        assert self.conn is not None
        rows = await self.conn.execute_fetchall(
            """
            SELECT channel_name, join_ts, leave_ts, duration_seconds, counted_seconds, is_afk,
                   solo_seconds, self_deaf_seconds, muted_deafened_seconds
            FROM voice_sessions
            WHERE guild_id = ? AND user_id = ?
            ORDER BY leave_ts DESC
            LIMIT ?
            """,
            (guild_id, user_id, limit),
        )
        return list(rows)

    async def export_user_sessions_csv(
        self,
        guild_id: int,
        user_id: int,
        after_ts: int | None,
        include_afk: bool,
    ) -> bytes:
        assert self.conn is not None
        params: list[object] = [guild_id, user_id]
        where = ["guild_id = ?", "user_id = ?"]
        if after_ts is not None:
            where.append("leave_ts > ?")
            params.append(after_ts)
        if not include_afk:
            where.append("is_afk = 0")
        rows = await self.conn.execute_fetchall(
            f"""
            SELECT channel_name, join_ts, leave_ts, duration_seconds, counted_seconds, is_afk,
                   solo_seconds, self_deaf_seconds, muted_deafened_seconds
            FROM voice_sessions
            WHERE {' AND '.join(where)}
            ORDER BY join_ts ASC
            """,
            params,
        )
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([
            "channel_name",
            "join_ts",
            "leave_ts",
            "duration_seconds",
            "counted_seconds",
            "is_afk",
            "solo_seconds",
            "self_deaf_seconds",
            "muted_deafened_seconds",
        ])
        for row in rows:
            writer.writerow([
                row["channel_name"],
                row["join_ts"],
                row["leave_ts"],
                row["duration_seconds"],
                row["counted_seconds"],
                row["is_afk"],
                row["solo_seconds"],
                row["self_deaf_seconds"],
                row["muted_deafened_seconds"],
            ])
        return buffer.getvalue().encode("utf-8-sig")
