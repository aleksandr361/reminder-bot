import asyncio
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramForbiddenError, TelegramNetworkError
from aiogram.methods import SendMessage

import scheduler
import storage
from conftest import FakeBot, all_reminders, make_user

UTC = timezone.utc
MSK = ZoneInfo("Europe/Moscow")
NY = ZoneInfo("America/New_York")


def weekly(*days):
    return json.dumps({"weekdays": list(days)})


# ---------- compute_next_run ----------

def test_once_has_no_next_run():
    run_at = datetime(2026, 11, 9, 9, tzinfo=UTC)
    assert scheduler.compute_next_run("once", run_at, "UTC", None, run_at) is None


def test_hourly_next_run_is_one_hour_later():
    run_at = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)
    now = run_at + timedelta(seconds=5)
    assert scheduler.compute_next_run("hourly", run_at, "UTC", None, now) == run_at + timedelta(hours=1)


def test_hourly_skips_hours_missed_while_bot_was_off():
    run_at = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)
    now = datetime(2026, 11, 9, 13, 30, tzinfo=UTC)
    assert scheduler.compute_next_run("hourly", run_at, "UTC", None, now) == datetime(2026, 11, 9, 14, 0, tzinfo=UTC)


def test_daily_keeps_local_time_across_dst_change():
    # 1 ноября 2026 Нью-Йорк переходит на зимнее время: 09:00 должно остаться 09:00
    run_at = datetime(2026, 10, 31, 9, 0, tzinfo=NY).astimezone(UTC)
    now = run_at + timedelta(minutes=1)
    nxt = scheduler.compute_next_run("daily", run_at, "America/New_York", None, now)
    assert nxt.astimezone(NY) == datetime(2026, 11, 1, 9, 0, tzinfo=NY)
    assert nxt.tzinfo is not None and nxt.utcoffset() == timedelta(0)


def test_daily_skips_days_missed_while_bot_was_off():
    run_at = datetime(2026, 11, 1, 9, 0, tzinfo=MSK).astimezone(UTC)
    now = datetime(2026, 11, 4, 12, 0, tzinfo=MSK).astimezone(UTC)
    nxt = scheduler.compute_next_run("daily", run_at, "Europe/Moscow", None, now)
    assert nxt.astimezone(MSK) == datetime(2026, 11, 5, 9, 0, tzinfo=MSK)


def test_weekly_goes_to_next_selected_day():
    monday = datetime(2026, 11, 9, 9, 0, tzinfo=MSK).astimezone(UTC)
    nxt = scheduler.compute_next_run("weekly", monday, "Europe/Moscow", weekly("mon", "thu"),
                                     monday + timedelta(seconds=1))
    assert nxt.astimezone(MSK) == datetime(2026, 11, 12, 9, 0, tzinfo=MSK)

    nxt2 = scheduler.compute_next_run("weekly", nxt, "Europe/Moscow", weekly("mon", "thu"),
                                      nxt + timedelta(seconds=1))
    assert nxt2.astimezone(MSK) == datetime(2026, 11, 16, 9, 0, tzinfo=MSK)


def test_weekly_single_day_repeats_in_seven_days():
    monday = datetime(2026, 11, 9, 9, 0, tzinfo=MSK).astimezone(UTC)
    nxt = scheduler.compute_next_run("weekly", monday, "Europe/Moscow", weekly("mon"),
                                     monday + timedelta(seconds=1))
    assert nxt.astimezone(MSK) == datetime(2026, 11, 16, 9, 0, tzinfo=MSK)


def test_weekly_with_broken_config_repeats_same_weekday():
    monday = datetime(2026, 11, 9, 9, 0, tzinfo=MSK).astimezone(UTC)
    nxt = scheduler.compute_next_run("weekly", monday, "Europe/Moscow", "не json",
                                     monday + timedelta(seconds=1))
    assert nxt.astimezone(MSK) == datetime(2026, 11, 16, 9, 0, tzinfo=MSK)


# ---------- check_due_reminders ----------

async def test_due_once_reminder_is_sent_and_marked_sent():
    uid = await make_user(77)
    rid = await storage.create_reminder(uid, datetime.now(UTC) - timedelta(seconds=1), "купить хлеб")
    bot = FakeBot()

    await scheduler.check_due_reminders(bot)

    assert len(bot.sent) == 1
    chat_id, text, kwargs = bot.sent[0]
    assert chat_id == 77
    assert "купить хлеб" in text
    datas = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert f"done_{rid}" in datas and f"snooze_{rid}_10" in datas
    assert (await storage.get_reminder_by_id(rid))["status"] == "sent"


async def test_text_is_sent_as_plain_text():
    uid = await make_user(77)
    await storage.create_reminder(uid, datetime.now(UTC) - timedelta(seconds=1), "<b>не жирный</b>")
    bot = FakeBot()
    await scheduler.check_due_reminders(bot)
    _, text, kwargs = bot.sent[0]
    assert "<b>не жирный</b>" in text
    assert kwargs.get("parse_mode") is None


async def test_future_and_canceled_reminders_are_not_sent():
    uid = await make_user(77)
    await storage.create_reminder(uid, datetime.now(UTC) + timedelta(minutes=5), "рано")
    canceled = await storage.create_reminder(uid, datetime.now(UTC) - timedelta(minutes=5), "отменено")
    await storage.update_reminder_status(canceled, "canceled")
    bot = FakeBot()
    await scheduler.check_due_reminders(bot)
    assert bot.sent == []


async def test_daily_reminder_is_sent_and_moved_to_next_day():
    uid = await make_user(77, tz="Europe/Moscow")
    run_at = (datetime.now(UTC) - timedelta(seconds=1)).replace(microsecond=0)
    rid = await storage.create_reminder(uid, run_at, "зарядка", repeat_type="daily")
    bot = FakeBot()

    await scheduler.check_due_reminders(bot)

    assert len(bot.sent) == 1
    reminder = await storage.get_reminder_by_id(rid)
    assert reminder["status"] == "pending"
    assert datetime.fromisoformat(reminder["run_at"]).replace(tzinfo=UTC) == run_at + timedelta(days=1)


async def test_reminder_is_sent_only_once_per_occurrence():
    uid = await make_user(77)
    await storage.create_reminder(uid, datetime.now(UTC) - timedelta(seconds=1), "раз", repeat_type="hourly")
    bot = FakeBot()
    await scheduler.check_due_reminders(bot)
    await scheduler.check_due_reminders(bot)
    assert len(bot.sent) == 1


async def test_blocked_bot_marks_reminder_failed():
    uid = await make_user(77)
    rid = await storage.create_reminder(uid, datetime.now(UTC) - timedelta(seconds=1), "x", repeat_type="daily")
    error = TelegramForbiddenError(method=SendMessage(chat_id=77, text="x"),
                                   message="Forbidden: bot was blocked by the user")
    await scheduler.check_due_reminders(FakeBot(error=error))
    assert (await storage.get_reminder_by_id(rid))["status"] == "failed"


async def test_network_error_keeps_reminder_for_retry():
    uid = await make_user(77)
    run_at = datetime.now(UTC) - timedelta(seconds=1)
    rid = await storage.create_reminder(uid, run_at, "x")
    error = TelegramNetworkError(method=SendMessage(chat_id=77, text="x"), message="timeout")
    await scheduler.check_due_reminders(FakeBot(error=error))

    reminder = await storage.get_reminder_by_id(rid)
    assert reminder["status"] == "pending"

    bot = FakeBot()
    await scheduler.check_due_reminders(bot)
    assert len(bot.sent) == 1


async def test_one_failing_reminder_does_not_block_others():
    uid = await make_user(77)
    await storage.create_reminder(uid, datetime.now(UTC) - timedelta(seconds=2), "первое")
    await storage.create_reminder(uid, datetime.now(UTC) - timedelta(seconds=1), "второе")

    class FlakyBot(FakeBot):
        async def send_message(self, chat_id, text, **kwargs):
            if "первое" in text:
                raise RuntimeError("boom")
            await super().send_message(chat_id, text, **kwargs)

    bot = FlakyBot()
    await scheduler.check_due_reminders(bot)
    assert [t for _, t, _ in bot.sent if "второе" in t]


async def test_very_long_text_is_trimmed_to_telegram_limit():
    uid = await make_user(77)
    await storage.create_reminder(uid, datetime.now(UTC) - timedelta(seconds=1), "я" * 4096)
    bot = FakeBot()
    await scheduler.check_due_reminders(bot)
    assert len(bot.sent[0][1]) <= 4096


# ---------- create_scheduler ----------

async def test_scheduler_runs_in_background_and_stops():
    uid = await make_user(77)
    await storage.create_reminder(uid, datetime.now(UTC) - timedelta(seconds=1), "фон")
    bot = FakeBot()

    sched = scheduler.create_scheduler(bot, interval=0.01)
    assert sched.running is False
    sched.start()
    assert sched.running is True
    for _ in range(200):
        if (await all_reminders())[0]["status"] == "sent":
            break
        await asyncio.sleep(0.01)
    sched.shutdown()
    await asyncio.sleep(0)

    assert sched.running is False
    assert len(bot.sent) == 1
    assert (await all_reminders())[0]["status"] == "sent"


async def test_scheduler_survives_errors_in_a_tick(monkeypatch):
    calls = []

    async def flaky(bot):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("db is locked")

    monkeypatch.setattr(scheduler, "check_due_reminders", flaky)
    sched = scheduler.create_scheduler(FakeBot(), interval=0.01)
    sched.start()
    for _ in range(100):
        if len(calls) >= 2:
            break
        await asyncio.sleep(0.01)
    sched.shutdown()
    assert len(calls) >= 2


# ---------- Каждые N часов / дней, каждый месяц ----------

def every(n):
    return json.dumps({"every": n})


def monthly(day):
    return json.dumps({"day": day})


def test_every_3_hours():
    run_at = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)
    assert scheduler.compute_next_run("hourly", run_at, "UTC", every(3), run_at + timedelta(seconds=5)) \
        == datetime(2026, 11, 9, 13, 0, tzinfo=UTC)


def test_every_3_hours_skips_missed():
    run_at = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)
    now = datetime(2026, 11, 9, 13, 30, tzinfo=UTC)
    assert scheduler.compute_next_run("hourly", run_at, "UTC", every(3), now) == datetime(2026, 11, 9, 16, 0, tzinfo=UTC)


def test_every_2_days():
    run_at = datetime(2026, 11, 1, 9, 0, tzinfo=MSK).astimezone(UTC)
    nxt = scheduler.compute_next_run("daily", run_at, "Europe/Moscow", every(2), run_at + timedelta(seconds=1))
    assert nxt.astimezone(MSK) == datetime(2026, 11, 3, 9, 0, tzinfo=MSK)


def test_every_2_days_skips_missed_keeping_rhythm():
    run_at = datetime(2026, 11, 1, 9, 0, tzinfo=MSK).astimezone(UTC)
    now = datetime(2026, 11, 6, 12, 0, tzinfo=MSK).astimezone(UTC)
    nxt = scheduler.compute_next_run("daily", run_at, "Europe/Moscow", every(2), now)
    assert nxt.astimezone(MSK) == datetime(2026, 11, 7, 9, 0, tzinfo=MSK)  # 1, 3, 5, 7


def test_monthly_next_month_same_day():
    run_at = datetime(2026, 11, 15, 9, 0, tzinfo=MSK).astimezone(UTC)
    nxt = scheduler.compute_next_run("monthly", run_at, "Europe/Moscow", monthly(15), run_at + timedelta(seconds=1))
    assert nxt.astimezone(MSK) == datetime(2026, 12, 15, 9, 0, tzinfo=MSK)
    nxt2 = scheduler.compute_next_run("monthly", nxt, "Europe/Moscow", monthly(15), nxt + timedelta(seconds=1))
    assert nxt2.astimezone(MSK) == datetime(2027, 1, 15, 9, 0, tzinfo=MSK)


def test_monthly_31st_uses_last_day_of_short_month_and_returns_to_31():
    jan31 = datetime(2027, 1, 31, 9, 0, tzinfo=MSK).astimezone(UTC)
    feb = scheduler.compute_next_run("monthly", jan31, "Europe/Moscow", monthly(31), jan31 + timedelta(seconds=1))
    assert feb.astimezone(MSK) == datetime(2027, 2, 28, 9, 0, tzinfo=MSK)
    mar = scheduler.compute_next_run("monthly", feb, "Europe/Moscow", monthly(31), feb + timedelta(seconds=1))
    assert mar.astimezone(MSK) == datetime(2027, 3, 31, 9, 0, tzinfo=MSK)


def test_monthly_skips_missed_months():
    run_at = datetime(2026, 1, 15, 9, 0, tzinfo=MSK).astimezone(UTC)
    now = datetime(2026, 3, 20, 9, 0, tzinfo=MSK).astimezone(UTC)
    nxt = scheduler.compute_next_run("monthly", run_at, "Europe/Moscow", monthly(15), now)
    assert nxt.astimezone(MSK) == datetime(2026, 4, 15, 9, 0, tzinfo=MSK)


# ---------- Вложения ----------

async def test_photo_reminder_is_sent_as_photo_with_caption_and_buttons():
    uid = await make_user(77)
    rid = await storage.create_reminder(uid, datetime.now(UTC) - timedelta(seconds=1), "рецепт",
                                        content_type="photo", file_id="photo-id")
    bot = FakeBot()
    await scheduler.check_due_reminders(bot)

    assert bot.sent == []
    method, chat_id, file_id, kwargs = bot.media[0]
    assert (method, chat_id, file_id) == ("send_photo", 77, "photo-id")
    assert "Напоминание" in kwargs["caption"] and "рецепт" in kwargs["caption"]
    datas = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert f"done_{rid}" in datas
    assert (await storage.get_reminder_by_id(rid))["status"] == "sent"


async def test_long_caption_is_trimmed_to_telegram_limit():
    uid = await make_user(77)
    await storage.create_reminder(uid, datetime.now(UTC) - timedelta(seconds=1), "я" * 2000,
                                  content_type="document", file_id="doc")
    bot = FakeBot()
    await scheduler.check_due_reminders(bot)
    assert len(bot.media[0][3]["caption"]) <= 1024


async def test_sticker_is_sent_with_separate_message_for_buttons():
    uid = await make_user(77)
    rid = await storage.create_reminder(uid, datetime.now(UTC) - timedelta(seconds=1), "",
                                        content_type="sticker", file_id="st")
    bot = FakeBot()
    await scheduler.check_due_reminders(bot)

    assert bot.media[0][:3] == ("send_sticker", 77, "st")
    assert "caption" not in bot.media[0][3]
    chat_id, text, kwargs = bot.sent[0]
    assert "Напоминание" in text
    datas = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert f"done_{rid}" in datas
