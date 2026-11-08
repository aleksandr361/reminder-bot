from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import storage
from conftest import make_user

UTC = timezone.utc


async def test_get_or_create_user_is_idempotent():
    first = await storage.get_or_create_user(42)
    second = await storage.get_or_create_user(42)
    other = await storage.get_or_create_user(43)
    assert first == second
    assert other != first


async def test_timezone_is_saved_per_chat():
    await storage.get_or_create_user(1)
    assert await storage.get_user_timezone(1) is None
    await storage.set_user_timezone(1, "Asia/Tokyo")
    assert await storage.get_user_timezone(1) == "Asia/Tokyo"
    assert await storage.get_user_timezone(999) is None


async def test_create_reminder_stores_run_at_as_naive_utc_iso():
    uid = await make_user(1)
    moscow_noon = datetime(2026, 12, 1, 12, 0, tzinfo=ZoneInfo("Europe/Moscow"))
    rid = await storage.create_reminder(uid, moscow_noon, "обед")

    reminder = await storage.get_reminder_by_id(rid)
    assert reminder["run_at"] == "2026-12-01T09:00:00"
    assert datetime.fromisoformat(reminder["run_at"]).replace(tzinfo=UTC) == moscow_noon
    assert reminder["text"] == "обед"
    assert reminder["status"] == "pending"
    assert reminder["repeat_type"] == "once"
    assert reminder["repeat_config"] is None
    assert reminder["user_id"] == uid
    assert reminder["id"] == rid


async def test_create_reminder_keeps_repeat_settings():
    uid = await make_user(1)
    rid = await storage.create_reminder(
        uid, datetime(2026, 12, 1, 9, tzinfo=UTC), "спорт",
        repeat_type="weekly", repeat_config='{"weekdays": ["mon"]}',
    )
    reminder = await storage.get_reminder_by_id(rid)
    assert reminder["repeat_type"] == "weekly"
    assert reminder["repeat_config"] == '{"weekdays": ["mon"]}'


async def test_get_reminder_by_id_unknown_returns_none():
    assert await storage.get_reminder_by_id(12345) is None


async def test_user_reminders_are_pending_only_own_and_sorted():
    me = await make_user(1)
    other = await make_user(2)
    later = await storage.create_reminder(me, datetime(2026, 12, 3, tzinfo=UTC), "позже")
    sooner = await storage.create_reminder(me, datetime(2026, 12, 2, tzinfo=UTC), "раньше")
    canceled = await storage.create_reminder(me, datetime(2026, 12, 1, tzinfo=UTC), "отменено")
    await storage.update_reminder_status(canceled, "canceled")
    await storage.create_reminder(other, datetime(2026, 12, 1, tzinfo=UTC), "чужое")

    reminders = await storage.get_user_reminders(1)
    assert [r["id"] for r in reminders] == [sooner, later]


async def test_update_reminder_status():
    uid = await make_user(1)
    rid = await storage.create_reminder(uid, datetime(2026, 12, 1, tzinfo=UTC), "x")
    await storage.update_reminder_status(rid, "sent")
    assert (await storage.get_reminder_by_id(rid))["status"] == "sent"


async def test_due_reminders_include_chat_and_timezone():
    uid = await make_user(77, tz="Europe/Moscow")
    now = datetime(2026, 12, 1, 12, 0, tzinfo=UTC)
    due = await storage.create_reminder(uid, now - timedelta(minutes=1), "пора")
    exactly_now = await storage.create_reminder(uid, now, "ровно сейчас")
    await storage.create_reminder(uid, now + timedelta(minutes=1), "рано")
    canceled = await storage.create_reminder(uid, now - timedelta(minutes=5), "отменено")
    await storage.update_reminder_status(canceled, "canceled")

    result = await storage.get_due_reminders(now)
    assert [r["id"] for r in result] == [due, exactly_now]
    assert result[0]["chat_id"] == 77
    assert result[0]["timezone"] == "Europe/Moscow"
    assert result[0]["text"] == "пора"


async def test_set_reminder_run_at_moves_reminder():
    uid = await make_user(1)
    rid = await storage.create_reminder(uid, datetime(2026, 12, 1, 9, tzinfo=UTC), "x")
    await storage.set_reminder_run_at(rid, datetime(2026, 12, 2, 12, 30, tzinfo=ZoneInfo("Europe/Moscow")))
    assert (await storage.get_reminder_by_id(rid))["run_at"] == "2026-12-02T09:30:00"


async def test_init_db_is_safe_to_call_twice():
    uid = await make_user(1)
    rid = await storage.create_reminder(uid, datetime(2026, 12, 1, tzinfo=UTC), "x")
    await storage.init_db()
    assert await storage.get_reminder_by_id(rid) is not None


# ---------- Вложения ----------

async def test_text_reminder_has_text_content_type_by_default():
    uid = await make_user(1)
    rid = await storage.create_reminder(uid, datetime(2026, 12, 1, tzinfo=UTC), "текст")
    reminder = await storage.get_reminder_by_id(rid)
    assert reminder["content_type"] == "text"
    assert reminder["file_id"] is None


async def test_attachment_reminder_is_stored():
    uid = await make_user(1)
    rid = await storage.create_reminder(uid, datetime(2026, 12, 1, tzinfo=UTC), "подпись",
                                        content_type="voice", file_id="voice-id")
    reminder = await storage.get_reminder_by_id(rid)
    assert (reminder["content_type"], reminder["file_id"], reminder["text"]) == ("voice", "voice-id", "подпись")
    due = await storage.get_due_reminders(datetime(2026, 12, 2, tzinfo=UTC))
    assert (due[0]["content_type"], due[0]["file_id"]) == ("voice", "voice-id")


async def test_init_db_upgrades_old_database(tmp_path, monkeypatch):
    """База от прошлой версии бота (как на сервере) получает новые колонки, данные сохраняются."""
    import aiosqlite

    old_db = tmp_path / "old.db"
    async with aiosqlite.connect(old_db) as db:
        await db.executescript(
            """
            CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL UNIQUE,
                                timezone TEXT, created_at TEXT);
            CREATE TABLE reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
                                    run_at TEXT NOT NULL, text TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                                    repeat_type TEXT NOT NULL DEFAULT 'once', repeat_config TEXT, created_at TEXT);
            INSERT INTO users (chat_id, timezone) VALUES (5, 'Europe/Moscow');
            INSERT INTO reminders (user_id, run_at, text, repeat_type) VALUES (1, '2026-12-01T09:00:00', 'старое', 'daily');
            """
        )
        await db.commit()

    monkeypatch.setattr(storage, "DB_PATH", str(old_db))
    await storage.init_db()
    await storage.init_db()  # повторный запуск тоже безопасен

    reminder = await storage.get_reminder_by_id(1)
    assert (reminder["text"], reminder["repeat_type"]) == ("старое", "daily")
    assert (reminder["content_type"], reminder["file_id"]) == ("text", None)
    assert await storage.get_user_timezone(5) == "Europe/Moscow"
