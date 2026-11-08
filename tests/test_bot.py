import json
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import storage
from conftest import BOT_MAIN_PATH, FakeCallback, FakeMessage, all_reminders, make_user


def utc_now_minute():
    return datetime.now(timezone.utc).replace(second=0, microsecond=0)


async def start_flow(state, main_module, **data):
    await state.update_data(**data)
    await state.set_state(main_module.ReminderFlow.waiting_for_time)


# ---------- 1. Weekday order ----------

async def test_list_shows_weekdays_in_week_order(send, fsm):
    uid = await make_user(1)
    await storage.create_reminder(
        uid, datetime.now(timezone.utc) + timedelta(days=1), "спорт",
        repeat_type="weekly", repeat_config=json.dumps({"weekdays": ["sun", "mon", "fri"]}),
    )
    msg = FakeMessage(1, text="📋 Мои напоминания")
    await send(msg, fsm(1))
    assert any("Каждую неделю: Пн, Пт, Вс" in t for t in msg.texts()), msg.texts()


async def test_weekly_confirmation_lists_days_in_week_order(send, fsm, main_module):
    await make_user(1)
    state = fsm(1)
    await start_flow(state, main_module, text="спорт", repeat_type="weekly",
                     selected_weekdays={"sun", "mon", "fri"})
    msg = FakeMessage(1, text="10:00")
    await send(msg, state)
    assert any("Каждую неделю: Пн, Пт, Вс" in t for t in msg.texts()), msg.texts()


# ---------- 2. HTML in reminder text ----------

async def test_list_escapes_html_in_reminder_text(send, fsm):
    uid = await make_user(1)
    await storage.create_reminder(uid, datetime.now(timezone.utc) + timedelta(days=1),
                                  "<b>молоко</b> & хлеб")
    msg = FakeMessage(1, text="📋 Мои напоминания")
    await send(msg, fsm(1))
    assert any("&lt;b&gt;молоко&lt;/b&gt; &amp; хлеб" in t for t in msg.texts()), msg.texts()


async def test_all_three_list_entry_points_render_the_same(send, press, fsm, main_module):
    uid = await make_user(1)
    await storage.create_reminder(
        uid, datetime.now(timezone.utc) + timedelta(days=1), "спорт",
        repeat_type="weekly", repeat_config=json.dumps({"weekdays": ["tue"]}),
    )
    await storage.create_reminder(uid, datetime.now(timezone.utc) + timedelta(days=2), "врач")

    by_button = FakeMessage(1, text="📋 Мои напоминания")
    await send(by_button, fsm(1))

    by_command = FakeMessage(1, text="/list")
    await main_module.cmd_list(by_command)

    cb_msg = FakeMessage(1)
    cb = FakeCallback("my_reminders", cb_msg)
    await press(cb, fsm(1))

    assert len(by_button.sent) == 1  # весь список — одно сообщение
    assert by_button.sent == by_command.sent == cb_msg.sent
    assert cb.answers  # callback spinner is stopped


# ---------- 3. Hourly start time ----------

async def test_hourly_starts_at_selected_time_today(send, fsm, main_module):
    await make_user(1)
    state = fsm(1)
    start = utc_now_minute() + timedelta(hours=2, minutes=7)
    await start_flow(state, main_module, text="вода", repeat_type="hourly")
    await send(FakeMessage(1, text=f"{start:%H:%M}"), state)

    rem = (await all_reminders())[-1]
    assert rem["repeat_type"] == "hourly"
    assert datetime.fromisoformat(rem["run_at"]) == start.replace(tzinfo=None)


async def test_hourly_with_passed_time_continues_rhythm(send, fsm, main_module):
    # Отсчёт от выбранного времени: если оно уже прошло, берём следующий шаг ритма
    await make_user(1)
    state = fsm(1)
    passed = utc_now_minute() - timedelta(minutes=30)
    await start_flow(state, main_module, text="вода", repeat_type="hourly")
    await send(FakeMessage(1, text=f"{passed:%H:%M}"), state)

    rem = (await all_reminders())[-1]
    expected = datetime.combine(datetime.now(timezone.utc).date(), passed.time())
    while expected <= datetime.now(timezone.utc).replace(tzinfo=None):
        expected += timedelta(hours=1)
    assert datetime.fromisoformat(rem["run_at"]) == expected


# ---------- 4. Stale buttons (no active dialog) ----------

async def test_stale_time_button_does_not_crash_or_create(press, fsm):
    await make_user(1)
    state = fsm(1)
    cb = FakeCallback("time_10:00", FakeMessage(1))
    await press(cb, state)
    assert cb.answers
    assert await all_reminders() == []


@pytest.mark.parametrize("data", ["repeat_daily", "date_1_2026_12_20", "time_custom"])
async def test_stale_flow_buttons_do_not_advance_dialog(press, fsm, data):
    await make_user(1)
    state = fsm(1)
    cb = FakeCallback(data, FakeMessage(1))
    await press(cb, state)
    assert await state.get_state() is None
    assert cb.answers


async def test_stale_weekday_button_does_not_edit_keyboard(press, fsm):
    await make_user(1)
    cb_msg = FakeMessage(1)
    cb = FakeCallback("weekday_mon", cb_msg)
    await press(cb, fsm(1))
    assert not [s for s in cb_msg.sent if s[0] == "edit_reply_markup"]
    assert cb.answers


# ---------- 5. Ownership of reminder buttons ----------

@pytest.mark.parametrize("template", [
    "cancel_reminder_{id}", "cancel_series_{id}", "cancel_{id}", "done_{id}", "snooze_{id}_10",
])
async def test_cannot_touch_someone_elses_reminder(press, fsm, template):
    owner = await make_user(1)
    await make_user(2)
    rid = await storage.create_reminder(owner, datetime.now(timezone.utc) + timedelta(days=1), "секрет")

    stranger_msg = FakeMessage(2)
    cb = FakeCallback(template.format(id=rid), stranger_msg)
    await press(cb, fsm(2))

    rems = await all_reminders()
    assert len(rems) == 1, "no extra (snoozed) reminder must be created"
    assert rems[0]["status"] == "pending"
    assert not [s for s in stranger_msg.sent if s[0] == "edit_text"]
    assert cb.answers and cb.answers[-1][1] is True  # shown as alert


@pytest.mark.parametrize("template,status,count", [
    ("cancel_reminder_{id}", "canceled", 1),
    ("cancel_series_{id}", "canceled", 1),
    ("cancel_{id}", "canceled", 1),
    ("done_{id}", "sent", 1),
    ("snooze_{id}_10", "pending", 2),
])
async def test_owner_can_use_reminder_buttons(press, fsm, template, status, count):
    owner = await make_user(1)
    rid = await storage.create_reminder(owner, datetime.now(timezone.utc) + timedelta(days=1), "моё")
    cb = FakeCallback(template.format(id=rid), FakeMessage(1))
    await press(cb, fsm(1))

    rems = await all_reminders()
    assert len(rems) == count
    assert rems[0]["status"] == status
    if count == 2:
        assert rems[1]["user_id"] == owner and rems[1]["text"] == "моё"


@pytest.mark.parametrize("data", ["cancel_reminder_abc", "cancel_series_x", "done_x", "snooze_x_10", "cancel_x"])
async def test_malformed_reminder_buttons_do_not_crash(press, fsm, data):
    await make_user(1)
    cb = FakeCallback(data, FakeMessage(1))
    await press(cb, fsm(1))
    assert cb.answers


# ---------- 6. Non-text messages in text steps ----------

async def test_sticker_during_timezone_input_does_not_crash(send, fsm, main_module):
    state = fsm(1)
    await state.set_state(main_module.ReminderFlow.waiting_for_tz_input)
    msg = FakeMessage(1, text=None)
    await send(msg, state)
    assert msg.texts()
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_tz_input.state


async def test_sticker_during_time_input_does_not_crash(send, fsm, main_module):
    await make_user(1)
    state = fsm(1)
    await state.update_data(text="x", repeat_type="daily")
    await state.set_state(main_module.ReminderFlow.waiting_for_time)
    msg = FakeMessage(1, text=None)
    await send(msg, state)
    assert msg.texts()
    assert await all_reminders() == []


async def test_sticker_as_reminder_text_is_rejected(send, fsm, main_module):
    await make_user(1)
    state = fsm(1)
    await state.set_state(main_module.ReminderFlow.waiting_for_text)
    msg = FakeMessage(1, text=None)
    await send(msg, state)
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_text.state
    assert "text" not in await state.get_data()
    assert msg.texts()


# ---------- 7. Bot token ----------

def test_no_hardcoded_bot_token_in_source():
    with open(BOT_MAIN_PATH, encoding="utf-8") as f:
        src = f.read()
    assert not re.search(r"\d{8,10}:[A-Za-z0-9_-]{30,}", src)


FAKE_TOKEN = "123456789:" + "A" * 35


@pytest.fixture
def fake_polling(main_module, monkeypatch):
    polled = []

    async def _poll(*bots, **kw):
        polled.append(bots)

    monkeypatch.setattr(main_module.dp, "start_polling", _poll)
    return polled


@pytest.fixture
def env_file(main_module, monkeypatch, tmp_path):
    """Файл .env во временной папке (по умолчанию не существует)."""
    path = tmp_path / ".env"
    monkeypatch.setattr(main_module, "ENV_FILE", path)
    return path


async def test_main_without_token_exits_without_polling(main_module, monkeypatch, fake_polling, env_file):
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        await main_module.main()
    assert fake_polling == []


async def test_main_uses_token_from_environment(main_module, monkeypatch, fake_polling, env_file):
    monkeypatch.setenv("BOT_TOKEN", FAKE_TOKEN)
    await main_module.main()
    assert fake_polling and fake_polling[0][0].token == FAKE_TOKEN


async def test_main_reads_token_from_env_file(main_module, monkeypatch, fake_polling, env_file):
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    # Блокнот Windows сохраняет UTF-8 с BOM — это тоже должно читаться
    env_file.write_text(f"# токен бота\nBOT_TOKEN = {FAKE_TOKEN}\n", encoding="utf-8-sig")
    await main_module.main()
    assert fake_polling and fake_polling[0][0].token == FAKE_TOKEN


async def test_main_stops_scheduler_when_polling_ends(main_module, monkeypatch, fake_polling, env_file):
    monkeypatch.setenv("BOT_TOKEN", FAKE_TOKEN)
    created = []
    real_create = main_module.create_scheduler

    def spy(bot, *args, **kwargs):
        scheduler = real_create(bot, *args, **kwargs)
        created.append(scheduler)
        return scheduler

    monkeypatch.setattr(main_module, "create_scheduler", spy)
    await main_module.main()
    assert created and created[0].running is False


async def test_main_with_placeholder_token_exits_with_hint(main_module, monkeypatch, fake_polling, env_file):
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    env_file.write_text("BOT_TOKEN=вставьте_сюда_токен\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        await main_module.main()
    assert "токен" in str(exc.value).lower()
    assert fake_polling == []


async def test_main_with_token_rejected_by_telegram_exits_with_hint(main_module, monkeypatch, env_file):
    from aiogram.exceptions import TelegramUnauthorizedError
    from aiogram.methods import GetMe

    async def unauthorized_polling(*bots, **kw):
        raise TelegramUnauthorizedError(method=GetMe(), message="Unauthorized")

    monkeypatch.setenv("BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setattr(main_module.dp, "start_polling", unauthorized_polling)
    with pytest.raises(SystemExit) as exc:
        await main_module.main()
    assert "botfather" in str(exc.value).lower()


# ---------- 8. Сегодняшнее число в календаре ----------

def marked_days(markup):
    """Номера дней, чей текст на кнопке отличается от простого числа."""
    days = []
    for row in markup.inline_keyboard:
        for b in row:
            if b.callback_data.startswith("date_"):
                day = b.callback_data.split("_")[4]
                if b.text != day:
                    days.append((int(b.callback_data.split("_")[2]), int(b.callback_data.split("_")[3]), int(day)))
    return days


async def test_calendar_marks_today_in_users_timezone(send, fsm, main_module):
    tz = "Pacific/Kiritimati"  # UTC+14: «сегодня» здесь часто уже не то, что у сервера
    await make_user(1, tz=tz)
    state = fsm(1)
    await state.update_data(repeat_type="once")
    await state.set_state(main_module.ReminderFlow.waiting_for_text)
    msg = FakeMessage(1, text="x")
    await send(msg, state)  # после текста бот присылает календарь

    today = datetime.now(ZoneInfo(tz)).date()
    markup = msg.answered[-1].kwargs["reply_markup"]
    assert marked_days(markup) == [(today.year, today.month, today.day)]


async def test_snooze_keeps_attachment(press, fsm):
    owner = await make_user(1)
    rid = await storage.create_reminder(owner, datetime.now(timezone.utc) + timedelta(days=1), "рецепт",
                                        content_type="photo", file_id="photo-id")
    await press(FakeCallback(f"snooze_{rid}_10", FakeMessage(1)), fsm(1))
    snoozed = (await all_reminders())[-1]
    assert snoozed["id"] != rid
    assert (snoozed["content_type"], snoozed["file_id"], snoozed["text"]) == ("photo", "photo-id", "рецепт")
    assert json.loads(snoozed["repeat_config"]) == {"snoozed": 10}


@pytest.mark.parametrize("data", ["done_{id}", "snooze_{id}_60", "cancel_series_{id}"])
async def test_buttons_under_photo_reminder_edit_caption(press, fsm, data):
    """У сообщения с фото нет текста — менять нужно подпись, а не текст."""
    owner = await make_user(1)
    rid = await storage.create_reminder(owner, datetime.now(timezone.utc) + timedelta(days=1), "рецепт",
                                        repeat_type="daily", content_type="photo", file_id="photo-id")
    photo_msg = FakeMessage(1)
    photo_msg.caption = "🔔 Напоминание\n\nрецепт"
    cb = FakeCallback(data.format(id=rid), photo_msg)
    await press(cb, fsm(1))

    kinds = [kind for kind, _, _ in photo_msg.sent]
    assert "edit_caption" in kinds and "edit_text" not in kinds
    assert cb.answers


async def test_snooze_message_tells_duration(press, fsm):
    owner = await make_user(1)
    rid = await storage.create_reminder(owner, datetime.now(timezone.utc) + timedelta(days=1), "x")
    msg = FakeMessage(1, text="🔔 Напоминание\n\n📝 x")
    await press(FakeCallback(f"snooze_{rid}_60", msg), fsm(1))
    assert any("отложено на 1 час" in (t or "") for _, t, _ in msg.sent)


async def test_calendar_navigation_keeps_today_mark(press, fsm, main_module):
    await make_user(1, tz="Europe/Moscow")
    state = fsm(1)
    await state.set_state(main_module.ReminderFlow.waiting_for_date)
    today = datetime.now(ZoneInfo("Europe/Moscow")).date()

    msg = FakeMessage(1)
    await press(FakeCallback(f"cal_1_{today.year}_{today.month}", msg), state)
    markup = next(kw["reply_markup"] for kind, _, kw in msg.sent if kind == "edit_reply_markup")
    assert marked_days(markup) == [(today.year, today.month, today.day)]



# ---------- Список напоминаний одним сообщением ----------

async def make_list(owner_chat=1, count=2):
    uid = await make_user(owner_chat)
    ids = []
    for i in range(count):
        ids.append(await storage.create_reminder(
            uid, datetime.now(timezone.utc) + timedelta(days=i + 1), f"дело {i + 1}"))
    return ids


def inline_of(kwargs):
    return [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]


async def test_list_is_one_message_with_cancel_buttons(send, fsm):
    ids = await make_list(count=3)
    msg = FakeMessage(1, text="📋 Мои напоминания")
    await send(msg, fsm(1))

    assert len(msg.sent) == 1
    _, text, kwargs = msg.sent[0]
    assert "У вас 3 активных напоминания" in text
    assert text.index("дело 1") < text.index("дело 2") < text.index("дело 3")
    assert inline_of(kwargs) == [f"lcancel_{i}" for i in ids]


async def test_list_cancel_updates_same_message(send, press, fsm):
    ids = await make_list(count=2)
    list_msg = FakeMessage(1, text="📋 У вас 2 активных напоминания")
    cb = FakeCallback(f"lcancel_{ids[0]}", list_msg)
    await press(cb, fsm(1))

    assert (await storage.get_reminder_by_id(ids[0]))["status"] == "canceled"
    kind, text, kwargs = list_msg.sent[-1]
    assert kind == "edit_text"
    assert "дело 1" not in text and "дело 2" in text
    assert inline_of(kwargs) == [f"lcancel_{ids[1]}"]
    assert list_msg.answered == []  # новых сообщений нет
    assert cb.answers


async def test_list_cancel_last_one_shows_empty(send, press, fsm):
    ids = await make_list(count=1)
    list_msg = FakeMessage(1, text="📋 …")
    await press(FakeCallback(f"lcancel_{ids[0]}", list_msg), fsm(1))
    kind, text, kwargs = list_msg.sent[-1]
    assert kind == "edit_text" and "Нет активных напоминаний" in text
    assert kwargs.get("reply_markup") is None


async def test_list_cancel_someone_elses_reminder_is_refused(press, fsm):
    ids = await make_list(owner_chat=1, count=1)
    await make_user(2)
    stranger_msg = FakeMessage(2, text="📋 …")
    cb = FakeCallback(f"lcancel_{ids[0]}", stranger_msg)
    await press(cb, fsm(2))
    assert (await storage.get_reminder_by_id(ids[0]))["status"] == "pending"
    assert cb.answers[-1][1] is True
    assert stranger_msg.sent == []


async def test_very_long_list_continues_numbering_on_next_message(send, fsm):
    uid = await make_user(1)
    ids = [await storage.create_reminder(uid, datetime.now(timezone.utc) + timedelta(days=i + 1), "я" * 1500)
           for i in range(3)]
    msg = FakeMessage(1, text="📋 Мои напоминания")
    await send(msg, fsm(1))

    assert len(msg.sent) == 2  # не влезло в лимит Telegram — второе сообщение
    assert all(len(text) <= 4096 for _, text, _ in msg.sent)
    buttons = [[(b.text, b.callback_data) for row in kw["reply_markup"].inline_keyboard for b in row]
               for _, _, kw in msg.sent]
    assert buttons == [[("❌ №1", f"lcancel_{ids[0]}"), ("❌ №2", f"lcancel_{ids[1]}")],
                       [("❌ №3", f"lcancel_{ids[2]}")]]
    assert "Напоминание #3" in msg.sent[1][1]
