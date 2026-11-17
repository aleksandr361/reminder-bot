"""Хранение пользователей и напоминаний в SQLite (файл reminders.db рядом с ботом).

Время напоминаний хранится в UTC строкой вида "2026-12-01T09:00:00".
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

DB_PATH = str(Path(__file__).with_name("reminders.db"))

_RUN_AT_FORMAT = "%Y-%m-%dT%H:%M:%S"
# Пользователь заблокировал бота — его напоминания ждут в этом статусе, пока он не вернётся (/start)
PAUSED = "paused_blocked"


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
                is_blocked INTEGER NOT NULL DEFAULT 0,
                blocked_at TEXT,
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
                is_forward    INTEGER NOT NULL DEFAULT 0,
                created_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
            );

            CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders (status, run_at);
            CREATE INDEX IF NOT EXISTS idx_reminders_user ON reminders (user_id, status);
            """
        )
        # База от прошлых версий бота: добавляем недостающие колонки
        await _add_missing_columns(db, "reminders", {
            "content_type": "TEXT NOT NULL DEFAULT 'text'",
            "file_id": "TEXT",
            "is_forward": "INTEGER NOT NULL DEFAULT 0",
        })
        await _add_missing_columns(db, "users", {
            "is_blocked": "INTEGER NOT NULL DEFAULT 0",
            "blocked_at": "TEXT",
        })
        await db.commit()


async def _add_missing_columns(db, table: str, columns: dict[str, str]):
    async with db.execute(f"PRAGMA table_info({table})") as cur:
        existing = {row[1] for row in await cur.fetchall()}
    for name, definition in columns.items():
        if name not in existing:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


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


async def is_user_blocked(chat_id: int) -> bool:
    async with _connect() as db:
        async with db.execute("SELECT is_blocked FROM users WHERE chat_id = ?", (chat_id,)) as cur:
            row = await cur.fetchone()
    return bool(row and row[0])


async def set_user_blocked(chat_id: int, blocked: bool):
    async with _connect() as db:
        await db.execute(
            "UPDATE users SET is_blocked = ?, "
            "blocked_at = CASE WHEN ? THEN strftime('%Y-%m-%dT%H:%M:%S', 'now') ELSE NULL END "
            "WHERE chat_id = ?",
            (int(blocked), int(blocked), chat_id),
        )
        await db.commit()


async def block_user_and_pause(user_id: int) -> int:
    """Пользователь заблокировал бота: отметить это и приостановить его активные напоминания.
    Возвращает, сколько напоминаний приостановлено."""
    async with _connect() as db:
        await db.execute(
            "UPDATE users SET is_blocked = 1, blocked_at = strftime('%Y-%m-%dT%H:%M:%S', 'now') WHERE id = ?",
            (user_id,),
        )
        cur = await db.execute(
            "UPDATE reminders SET status = ? WHERE user_id = ? AND status = 'pending'", (PAUSED, user_id)
        )
        await db.commit()
        return cur.rowcount


# ---------- Напоминания ----------

async def create_reminder(user_id: int, run_at: datetime, text: str,
                          repeat_type: str = "once", repeat_config: str | None = None,
                          content_type: str = "text", file_id: str | None = None,
                          is_forward: bool = False) -> int:
    """text — текст напоминания или подпись к вложению (content_type: photo, voice, document…).
    is_forward — напоминание сделано из пересланного сообщения."""
    async with _connect() as db:
        cur = await db.execute(
            "INSERT INTO reminders (user_id, run_at, text, repeat_type, repeat_config, content_type, file_id, "
            "is_forward) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, _to_db_time(run_at), text or "", repeat_type, repeat_config, content_type, file_id,
             int(bool(is_forward))),
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


async def get_paused_reminders(chat_id: int) -> list[dict]:
    """Напоминания, приостановленные, пока пользователь блокировал бота."""
    return await _fetch_all(
        "SELECT r.* FROM reminders r JOIN users u ON u.id = r.user_id "
        "WHERE u.chat_id = ? AND r.status = ? ORDER BY r.run_at, r.id",
        (chat_id, PAUSED),
    )


async def count_paused_reminders(chat_id: int) -> int:
    return len(await get_paused_reminders(chat_id))


async def resume_reminder(reminder_id: int, run_at: datetime):
    """Вернуть приостановленное напоминание в работу с новым временем."""
    async with _connect() as db:
        await db.execute(
            "UPDATE reminders SET status = 'pending', run_at = ? WHERE id = ?", (_to_db_time(run_at), reminder_id)
        )
        await db.commit()


async def delete_paused_reminders(chat_id: int) -> int:
    async with _connect() as db:
        cur = await db.execute(
            "DELETE FROM reminders WHERE status = ? AND user_id IN (SELECT id FROM users WHERE chat_id = ?)",
            (PAUSED, chat_id),
        )
        await db.commit()
        return cur.rowcount


async def get_admin_stats() -> dict:
    """Для /admin_stats: сколько пользователей и напоминаний (всех, за всё время)."""
    async with _connect() as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cur:
            users_total = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM reminders") as cur:
            reminders_total = (await cur.fetchone())[0]
    return {
        "users_total": users_total,
        "reminders_total": reminders_total,
        "avg_reminders_per_user": reminders_total / users_total if users_total else 0.0,
    }


async def get_due_reminders(now: datetime, limit: int = 100) -> list[dict]:
    """Напоминания, время которых наступило, вместе с chat_id и часовым поясом владельца."""
    return await _fetch_all(
        "SELECT r.*, u.chat_id, u.timezone FROM reminders r JOIN users u ON u.id = r.user_id "
        "WHERE r.status = 'pending' AND r.run_at <= ? "
        "ORDER BY r.run_at, r.id LIMIT ?",
        (_to_db_time(now), limit),
    )
