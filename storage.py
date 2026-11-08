"""Хранение пользователей и напоминаний в SQLite (файл reminders.db рядом с ботом).

Время напоминаний хранится в UTC строкой вида "2026-12-01T09:00:00".
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

DB_PATH = str(Path(__file__).with_name("reminders.db"))

_RUN_AT_FORMAT = "%Y-%m-%dT%H:%M:%S"


def _to_db_time(dt: datetime) -> str:
    """Время → строка UTC без часового пояса. Время без пояса считается UTC."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime(_RUN_AT_FORMAT)


def _connect():
    return aiosqlite.connect(DB_PATH, timeout=30)


async def init_db():
    async with _connect() as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id    INTEGER NOT NULL UNIQUE,
                timezone   TEXT,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
            );

            CREATE TABLE IF NOT EXISTS reminders (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       INTEGER NOT NULL REFERENCES users(id),
                run_at        TEXT    NOT NULL,
                text          TEXT    NOT NULL,
                status        TEXT    NOT NULL DEFAULT 'pending',
                repeat_type   TEXT    NOT NULL DEFAULT 'once',
                repeat_config TEXT,
                content_type  TEXT    NOT NULL DEFAULT 'text',
                file_id       TEXT,
                created_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
            );

            CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders (status, run_at);
            CREATE INDEX IF NOT EXISTS idx_reminders_user ON reminders (user_id, status);
            """
        )
        # База от прошлой версии бота: добавляем колонки для вложений
        async with db.execute("PRAGMA table_info(reminders)") as cur:
            columns = {row[1] for row in await cur.fetchall()}
        if "content_type" not in columns:
            await db.execute("ALTER TABLE reminders ADD COLUMN content_type TEXT NOT NULL DEFAULT 'text'")
        if "file_id" not in columns:
            await db.execute("ALTER TABLE reminders ADD COLUMN file_id TEXT")
        await db.commit()


# ---------- Пользователи ----------

async def get_or_create_user(chat_id: int) -> int:
    """id пользователя в базе (создаёт запись при первом обращении)."""
    async with _connect() as db:
        await db.execute("INSERT OR IGNORE INTO users (chat_id) VALUES (?)", (chat_id,))
        await db.commit()
        async with db.execute("SELECT id FROM users WHERE chat_id = ?", (chat_id,)) as cur:
            row = await cur.fetchone()
    return row[0]


async def set_user_timezone(chat_id: int, tz_name: str):
    async with _connect() as db:
        await db.execute(
            "INSERT INTO users (chat_id, timezone) VALUES (?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET timezone = excluded.timezone",
            (chat_id, tz_name),
        )
        await db.commit()


async def get_user_timezone(chat_id: int) -> str | None:
    async with _connect() as db:
        async with db.execute("SELECT timezone FROM users WHERE chat_id = ?", (chat_id,)) as cur:
            row = await cur.fetchone()
    return row[0] if row else None


# ---------- Напоминания ----------

async def create_reminder(user_id: int, run_at: datetime, text: str,
                          repeat_type: str = "once", repeat_config: str | None = None,
                          content_type: str = "text", file_id: str | None = None) -> int:
    """text — текст напоминания или подпись к вложению (content_type: photo, voice, document…)."""
    async with _connect() as db:
        cur = await db.execute(
            "INSERT INTO reminders (user_id, run_at, text, repeat_type, repeat_config, content_type, file_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, _to_db_time(run_at), text or "", repeat_type, repeat_config, content_type, file_id),
        )
        await db.commit()
        return cur.lastrowid


async def _fetch_all(query: str, params: tuple) -> list[dict]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def get_reminder_by_id(reminder_id: int) -> dict | None:
    rows = await _fetch_all("SELECT * FROM reminders WHERE id = ?", (reminder_id,))
    return rows[0] if rows else None


async def get_user_reminders(chat_id: int) -> list[dict]:
    """Активные напоминания чата, ближайшие первыми."""
    return await _fetch_all(
        "SELECT r.* FROM reminders r JOIN users u ON u.id = r.user_id "
        "WHERE u.chat_id = ? AND r.status = 'pending' "
        "ORDER BY r.run_at, r.id",
        (chat_id,),
    )


async def update_reminder_status(reminder_id: int, status: str):
    async with _connect() as db:
        await db.execute("UPDATE reminders SET status = ? WHERE id = ?", (status, reminder_id))
        await db.commit()


async def set_reminder_run_at(reminder_id: int, run_at: datetime):
    async with _connect() as db:
        await db.execute("UPDATE reminders SET run_at = ? WHERE id = ?", (_to_db_time(run_at), reminder_id))
        await db.commit()


async def get_due_reminders(now: datetime, limit: int = 100) -> list[dict]:
    """Напоминания, время которых наступило, вместе с chat_id и часовым поясом владельца."""
    return await _fetch_all(
        "SELECT r.*, u.chat_id, u.timezone FROM reminders r JOIN users u ON u.id = r.user_id "
        "WHERE r.status = 'pending' AND r.run_at <= ? "
        "ORDER BY r.run_at, r.id LIMIT ?",
        (_to_db_time(now), limit),
    )
