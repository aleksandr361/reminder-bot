"""Диалог создания напоминания:
тип повторения → текст или вложение → дата / дни недели / число N → время → «✅ Напоминание установлено».
"""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup

import storage
from conftest import FakeCallback, FakeMessage, all_reminders, make_user

UTC = timezone.utc


def inline_datas(markup):
    assert isinstance(markup, InlineKeyboardMarkup)
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def reply_texts(markup):
    assert isinstance(markup, ReplyKeyboardMarkup)
    return [b.text for row in markup.keyboard for b in row]


async def open_menu(send, state):
    """Нажать «➕ Новое напоминание». Возвращает сообщение с меню типов."""
    start = FakeMessage(1, text="➕ Новое напоминание")
    await send(start, state)
    return start.answered[-1]


async def choose_type(press, state, menu, repeat):
    """Выбрать тип в меню. Возвращает подсказку «Отправьте текст или вложение»."""
    await press(FakeCallback(f"repeat_{repeat}", menu), state)
    return menu.answered[-1]


async def send_content(send, state, **kwargs):
    """Отправить текст/вложение. Возвращает следующую подсказку бота."""
    msg = FakeMessage(1, **{k: v for k, v in kwargs.items() if k == "text"})
    for key, value in kwargs.items():
        setattr(msg, key, value)
    await send(msg, state)
    return msg.answered[-1]


async def send_text(send, state, text):
    msg = FakeMessage(1, text=text)
    await send(msg, state)
    return msg.answered[-1]


def tomorrow_utc():
    return (datetime.now(UTC) + timedelta(days=1)).date()


async def pick_date(press, state, calendar_msg, day):
    await press(FakeCallback(f"date_1_{day.year}_{day.month}_{day.day}", calendar_msg), state)
    return calendar_msg.answered[-1]


# ---------- Меню ----------

async def test_menu_has_five_types_in_one_column(send, fsm):
    await make_user(1, tz="UTC")
    menu = await open_menu(send, fsm(1))

    assert "Выберите тип повторения" in menu.text
    markup = menu.kwargs["reply_markup"]
    assert all(len(row) == 1 for row in markup.inline_keyboard)
    assert inline_datas(markup) == [
        "repeat_once", "repeat_hourly", "repeat_daily", "repeat_weekly", "repeat_monthly"
    ]
    texts = " | ".join(b.text for row in markup.inline_keyboard for b in row)
    for label in ["Однократно", "Каждые N часов", "Каждые N дней", "Каждую неделю", "Каждый месяц"]:
        assert label in texts


async def test_after_type_bot_asks_for_text_or_attachment(send, press, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    menu = await open_menu(send, state)
    prompt = await choose_type(press, state, menu, "once")

    assert "Отправьте текст или вложение" in prompt.text
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_text.state


async def test_type_can_be_changed_while_asked_for_text(send, press, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    menu = await open_menu(send, state)
    await choose_type(press, state, menu, "once")
    await choose_type(press, state, menu, "weekly")  # нажали другой тип в том же меню

    assert (await state.get_data())["repeat_type"] == "weekly"
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_text.state


async def test_prompts_stay_in_chat(send, press, fsm, bot):
    await make_user(1, tz="UTC")
    state = fsm(1)
    menu = await open_menu(send, state)
    await choose_type(press, state, menu, "daily")
    await send_content(send, state, text="зарядка")
    await send_text(send, state, "1")
    await send_text(send, state, "0700")
    assert bot.deleted == []


# ---------- Однократно ----------

async def test_once_flow(send, press, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    menu = await open_menu(send, state)
    await choose_type(press, state, menu, "once")

    calendar = await send_content(send, state, text="к ортопеду с Андреем")
    assert "дату" in calendar.text
    assert any(d.startswith("date_") for d in inline_datas(calendar.kwargs["reply_markup"]))

    day = tomorrow_utc()
    time_prompt = await pick_date(press, state, calendar, day)
    assert f"Однократно, {main_module.format_date(day)}" in time_prompt.text
    assert "Во сколько напоминать" in time_prompt.text
    assert "ЧЧММ" in time_prompt.text
    assert isinstance(time_prompt.kwargs["reply_markup"], ReplyKeyboardMarkup)

    done = await send_text(send, state, "2000")
    assert "✅ Напоминание установлено" in done.text
    assert "📝 к ортопеду с Андреем" in done.text
    assert "📅 Однократно" in done.text
    assert f"⏰ {main_module.format_date(day)} в 20:00" in done.text
    assert "➕ Новое напоминание" in reply_texts(done.kwargs["reply_markup"])  # снова главное меню

    rem = (await all_reminders())[-1]
    assert rem["repeat_type"] == "once"
    assert rem["run_at"] == f"{day.isoformat()}T20:00:00"
    assert await state.get_state() is None


async def test_past_date_is_not_accepted(send, press, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    menu = await open_menu(send, state)
    await choose_type(press, state, menu, "once")
    calendar = await send_content(send, state, text="x")

    yesterday = (datetime.now(UTC) - timedelta(days=1)).date()
    cb = FakeCallback(f"date_1_{yesterday.year}_{yesterday.month}_{yesterday.day}", calendar)
    await press(cb, state)

    assert await state.get_state() == main_module.ReminderFlow.waiting_for_date.state
    assert cb.answers and cb.answers[-1][1] is True


async def test_past_time_today_asks_again(send, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await state.update_data(text="x", repeat_type="once", selected_date=datetime.now(UTC).date())
    await state.set_state(main_module.ReminderFlow.waiting_for_time)

    reply = await send_text(send, state, "0000")  # сегодня 00:00 по UTC всегда уже прошло
    assert "прошло" in reply.text
    assert await all_reminders() == []
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_time.state


# ---------- Каждые N часов / дней ----------

async def test_every_n_hours_flow(send, press, fsm):
    await make_user(1, tz="UTC")
    state = fsm(1)
    menu = await open_menu(send, state)
    await choose_type(press, state, menu, "hourly")

    ask_n = await send_content(send, state, text="пить воду")
    assert "Каждые сколько часов" in ask_n.text
    assert "3" in reply_texts(ask_n.kwargs["reply_markup"])

    time_prompt = await send_text(send, state, "3")
    assert "Каждые 3 часа" in time_prompt.text

    done = await send_text(send, state, "0900")
    assert "🔁 Каждые 3 часа" in done.text
    rem = (await all_reminders())[-1]
    assert rem["repeat_type"] == "hourly"
    assert json.loads(rem["repeat_config"]) == {"every": 3}


async def test_every_n_days_with_typed_number(send, press, fsm):
    await make_user(1, tz="UTC")
    state = fsm(1)
    menu = await open_menu(send, state)
    await choose_type(press, state, menu, "daily")
    ask_n = await send_content(send, state, text="полить цветы")
    assert "Каждые сколько дней" in ask_n.text

    await send_text(send, state, "10")  # числа 10 нет на кнопках — вводим сами
    done = await send_text(send, state, "1930")
    assert "🔁 Каждые 10 дней" in done.text
    assert json.loads((await all_reminders())[-1]["repeat_config"]) == {"every": 10}


@pytest.mark.parametrize("repeat,expected", [("hourly", "Каждый час"), ("daily", "Каждый день")])
async def test_interval_of_one_reads_naturally(send, press, fsm, repeat, expected):
    await make_user(1, tz="UTC")
    state = fsm(1)
    menu = await open_menu(send, state)
    await choose_type(press, state, menu, repeat)
    await send_content(send, state, text="x")
    await send_text(send, state, "1")
    done = await send_text(send, state, "0800")
    assert expected in done.text


@pytest.mark.parametrize("typed", ["0", "-2", "abc", "2.5", "1000", ""])
async def test_wrong_interval_is_rejected(send, press, fsm, main_module, typed):
    await make_user(1, tz="UTC")
    state = fsm(1)
    menu = await open_menu(send, state)
    await choose_type(press, state, menu, "hourly")
    await send_content(send, state, text="x")

    reply = await send_text(send, state, typed)
    assert "число" in reply.text.lower()
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_interval.state


# ---------- Каждую неделю / каждый месяц ----------

async def test_weekly_flow(send, press, fsm):
    await make_user(1, tz="UTC")
    state = fsm(1)
    menu = await open_menu(send, state)
    await choose_type(press, state, menu, "weekly")

    days_msg = await send_content(send, state, text="спортзал")
    assert "weekday_done" in inline_datas(days_msg.kwargs["reply_markup"])
    for data in ["weekday_wed", "weekday_mon", "weekday_done"]:
        await press(FakeCallback(data, days_msg), state)
    time_prompt = days_msg.answered[-1]
    assert "Каждую неделю: Пн, Ср" in time_prompt.text

    done = await send_text(send, state, "2000")
    assert "📆 Каждую неделю: Пн, Ср" in done.text
    rem = (await all_reminders())[-1]
    assert rem["repeat_type"] == "weekly"
    assert json.loads(rem["repeat_config"]) == {"weekdays": ["mon", "wed"]}


async def test_monthly_flow(send, press, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    menu = await open_menu(send, state)
    await choose_type(press, state, menu, "monthly")

    calendar = await send_content(send, state, text="оплатить интернет")
    day = tomorrow_utc()
    time_prompt = await pick_date(press, state, calendar, day)
    assert "Каждый месяц" in time_prompt.text

    done = await send_text(send, state, "1000")
    assert f"📆 Каждый месяц, {day.day} числа" in done.text
    rem = (await all_reminders())[-1]
    assert rem["repeat_type"] == "monthly"
    assert json.loads(rem["repeat_config"]) == {"day": day.day}
    assert rem["run_at"] == f"{day.isoformat()}T10:00:00"


# ---------- Вложения ----------

async def test_photo_with_caption_becomes_reminder(send, press, fsm):
    await make_user(1, tz="UTC")
    state = fsm(1)
    menu = await open_menu(send, state)
    await choose_type(press, state, menu, "once")
    photo = [SimpleNamespace(file_id="small"), SimpleNamespace(file_id="big")]
    calendar = await send_content(send, state, photo=photo, caption="рецепт")
    await pick_date(press, state, calendar, tomorrow_utc())
    done = await send_text(send, state, "1200")

    assert "🖼 Фото: рецепт" in done.text
    rem = (await all_reminders())[-1]
    assert (rem["content_type"], rem["file_id"], rem["text"]) == ("photo", "big", "рецепт")


@pytest.mark.parametrize("kind,label", [
    ("voice", "🎤 Голосовое"), ("document", "📎 Файл"), ("video", "🎬 Видео"),
    ("audio", "🎵 Аудио"), ("video_note", "📹 Кружок"), ("sticker", "🙂 Стикер"), ("animation", "🎞 GIF"),
])
async def test_other_attachments_become_reminders(send, press, fsm, kind, label):
    await make_user(1, tz="UTC")
    state = fsm(1)
    menu = await open_menu(send, state)
    await choose_type(press, state, menu, "daily")
    await send_content(send, state, **{kind: SimpleNamespace(file_id=f"id-{kind}")})
    await send_text(send, state, "1")
    done = await send_text(send, state, "0800")

    assert label in done.text
    rem = (await all_reminders())[-1]
    assert (rem["content_type"], rem["file_id"], rem["text"]) == (kind, f"id-{kind}", "")


async def test_gif_is_saved_as_animation_not_document(send, press, fsm):
    # У GIF в Telegram заполнены и animation, и document
    await make_user(1, tz="UTC")
    state = fsm(1)
    menu = await open_menu(send, state)
    await choose_type(press, state, menu, "daily")
    await send_content(send, state, animation=SimpleNamespace(file_id="gif"),
                       document=SimpleNamespace(file_id="gif-doc"))
    await send_text(send, state, "1")
    await send_text(send, state, "0800")
    assert (await all_reminders())[-1]["content_type"] == "animation"


async def test_unsupported_content_is_rejected(send, press, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    menu = await open_menu(send, state)
    await choose_type(press, state, menu, "once")
    reply = await send_content(send, state, poll=SimpleNamespace(question="?"))  # опрос не поддерживаем

    assert "текст" in reply.text.lower()
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_text.state


# ---------- Перезапуск диалога ----------

async def test_restarting_flow_resets_choices(send, press, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    menu = await open_menu(send, state)
    await choose_type(press, state, menu, "weekly")
    days_msg = await send_content(send, state, text="x")
    await press(FakeCallback("weekday_mon", days_msg), state)

    await open_menu(send, state)
    data = await state.get_data()
    assert not {"selected_weekdays", "text", "repeat_type"} & set(data)
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_repeat_type.state


# ---------- Ввод времени ----------

async def start_time_step(state, main_module, **data):
    await state.update_data(text="x", **data)
    await state.set_state(main_module.ReminderFlow.waiting_for_time)


@pytest.mark.parametrize("typed,expected", [
    ("1430", "14:30"), ("14:30", "14:30"), ("14.30", "14:30"), ("14 30", "14:30"),
    ("0930", "09:30"), ("930", "09:30"), ("9:05", "09:05"), ("00:00", "00:00"),
    ("2359", "23:59"), ("  1430 ", "14:30"), ("08:00", "08:00"),
])
async def test_time_can_be_typed(send, fsm, main_module, typed, expected):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await start_time_step(state, main_module, repeat_type="daily")
    await send(FakeMessage(1, text=typed), state)

    rems = await all_reminders()
    assert len(rems) == 1, typed
    assert datetime.fromisoformat(rems[-1]["run_at"]).strftime("%H:%M") == expected


@pytest.mark.parametrize("typed", ["2400", "1260", "14", "abc", "14:3", "143000", "25:00", "-1430"])
async def test_wrong_time_is_rejected(send, fsm, main_module, typed):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await start_time_step(state, main_module, repeat_type="daily")
    msg = FakeMessage(1, text=typed)
    await send(msg, state)

    assert await all_reminders() == []
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_time.state
    assert msg.answered and "ЧЧММ" in msg.answered[-1].text


# ---------- Часовой пояс ----------

async def test_manual_timezone_is_saved(send, press, fsm):
    await storage.get_or_create_user(1)
    state = fsm(1)
    await press(FakeCallback("tz_manual", FakeMessage(1, text="👋 Привет!")), state)
    await send(FakeMessage(1, text="Mars/Olympus"), state)
    assert await storage.get_user_timezone(1) is None
    await send(FakeMessage(1, text="Europe/Moscow"), state)
    assert await storage.get_user_timezone(1) == "Europe/Moscow"


async def test_geo_timezone_is_saved(send, press, fsm):
    await storage.get_or_create_user(1)
    state = fsm(1)
    choice = FakeMessage(1, text="👋 Привет!")
    await press(FakeCallback("tz_geo", choice), state)
    buttons = [b for row in choice.answered[-1].kwargs["reply_markup"].keyboard for b in row]
    assert any(b.request_location for b in buttons)

    await send(FakeMessage(1, location=SimpleNamespace(latitude=55.75, longitude=37.62)), state)
    assert await storage.get_user_timezone(1) == "Europe/Moscow"
