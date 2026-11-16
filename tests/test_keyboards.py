"""Клавиатуры бота.

Шаги создания напоминания — на нижней клавиатуре (с «❌ Отмена» последней строкой).
Кнопки в сообщениях остались только у часового пояса, сработавших напоминаний и «Моих напоминаний» —
каждая такая кнопка должна попадать в свой обработчик main.py.
"""
from datetime import date, time

import pytest
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup

import keyboards as kb
from conftest import FakeCallback, FakeMessage, make_user

CANCEL = "❌ Отмена"


def rows(markup):
    assert isinstance(markup, ReplyKeyboardMarkup), markup
    return [[b.text for b in row] for row in markup.keyboard]


def inline_buttons(markup):
    assert isinstance(markup, InlineKeyboardMarkup)
    return [b for row in markup.inline_keyboard for b in row]


def callbacks(markup):
    return [b.callback_data for b in inline_buttons(markup)]


async def handler_for(main_module, data):
    """Имя обработчика main.py, который поймает нажатие (без вызова самого обработчика)."""
    cb = FakeCallback(data, FakeMessage(1))
    for handler in main_module.router.callback_query.handlers:
        ok, _ = await handler.check(cb, raw_state=None, state=None, bot=None)
        if ok:
            return handler.callback.__name__
    return None


INLINE_CASES = [
    (lambda: kb.get_timezone_geo_or_manual(), {"tz_geo_request", "tz_manual"}),
    (lambda: kb.get_reminder_actions_inline(5, "once"), {"cancel_single_reminder"}),
    (lambda: kb.get_reminder_actions_inline(5, "daily"), {"cancel_series_reminder"}),
    (lambda: kb.get_snooze_keyboard(5, "once"), {"snooze_reminder", "done_reminder"}),
    (lambda: kb.get_snooze_keyboard(5, "weekly"), {"snooze_reminder", "done_reminder"}),
    (lambda: kb.get_reminder_list_keyboard(), {"new_reminder_callback", "my_reminders_callback", "settings_callback"}),
]


@pytest.mark.parametrize("build,allowed", INLINE_CASES)
async def test_every_inline_button_reaches_its_handler(main_module, build, allowed):
    datas = callbacks(build())
    assert datas
    for data in datas:
        assert data and len(data.encode()) <= 64, data  # лимит Telegram на callback_data
        assert await handler_for(main_module, data) in allowed, data


# ---------- Главное меню ----------

async def test_main_menu_has_no_cancel_and_buttons_reach_handlers(main_module, send, fsm):
    await make_user(1)
    menu = rows(kb.get_main_menu_keyboard())
    assert menu == [["➕ Новое напоминание"], ["📋 Мои напоминания", "⚙️ Настройки"]]  # отмены изначально нет
    for text in [t for row in menu for t in row]:
        msg = FakeMessage(1, text=text)
        assert await send(msg, fsm(1)) is not UNHANDLED, text
        assert msg.sent, text


async def test_geo_button_requests_location():
    markup = kb.get_timezone_geo_button()
    assert isinstance(markup, ReplyKeyboardMarkup)
    assert any(b.request_location for row in markup.keyboard for b in row)


# ---------- Шаги создания — нижняя клавиатура с «❌ Отмена» ----------

async def test_repeat_type_keyboard():
    assert rows(kb.get_repeat_type_keyboard()) == [
        ["🗓 Однократно"], ["🔁 Каждые N часов"], ["🔁 Каждые N дней"],
        ["📆 Каждую неделю"], ["📆 Каждый месяц"], [CANCEL],
    ]


async def test_cancel_keyboard():
    assert rows(kb.get_cancel_keyboard()) == [[CANCEL]]


@pytest.mark.parametrize("kind,expected", [
    ("hourly", [["1", "2", "3", "4"], ["6", "8", "12", "24"]]),
    ("daily", [["1", "2", "3", "7"], ["10", "14", "21", "30"]]),
    ("before_end", [["1", "2", "3", "5"], ["7", "10", "14"]]),
])
async def test_interval_keyboard(kind, expected):
    markup = kb.get_interval_keyboard(kind)
    assert rows(markup) == expected + [[CANCEL]]
    assert "число" in markup.input_field_placeholder.lower()


async def test_weekdays_keyboard_marks_selected():
    assert rows(kb.get_weekdays_keyboard()) == [
        ["Пн", "Вт", "Ср", "Чт"], ["Пт", "Сб", "Вс"], ["✔ Подтвердить выбор"], [CANCEL],
    ]
    assert rows(kb.get_weekdays_keyboard({"mon", "sun"}))[:2] == [["✅ Пн", "Вт", "Ср", "Чт"], ["Пт", "Сб", "✅ Вс"]]


async def test_month_mode_keyboard():
    assert rows(kb.get_month_mode_keyboard()) == [
        ["📅 В указанные дни месяца"], ["📆 В последний день месяца"], ["⏳ За N дней до конца месяца"], [CANCEL],
    ]


async def test_month_days_keyboard_marks_selected():
    keyboard = rows(kb.get_month_days_keyboard({5, 29}))
    days = [t for row in keyboard[:-2] for t in row]
    assert days == [f"✅{d}" if d in (5, 29) else str(d) for d in range(1, 32)]
    assert [len(row) for row in keyboard[:-2]] == [7, 7, 7, 7, 3]
    assert keyboard[-2:] == [["✔ Подтвердить выбор"], [CANCEL]]


async def test_time_keyboard_has_hours_8_to_23_and_cancel(main_module):
    keyboard = rows(kb.get_time_keyboard())
    assert keyboard[-1] == [CANCEL]
    hours = [t for row in keyboard[:-1] for t in row]
    assert hours == [f"{h:02d}:00" for h in range(8, 24)]
    assert all(len(row) == 4 for row in keyboard[:-1])
    assert [main_module.parse_time(t) for t in hours] == [time(h, 0) for h in range(8, 24)]


async def test_time_keyboard_with_start_now_button():
    keyboard = rows(kb.get_time_keyboard(start_now=True))
    assert keyboard[0] == ["📍 С текущего момента"]
    assert keyboard[-1] == [CANCEL]
    assert [t for row in keyboard[1:-1] for t in row] == [f"{h:02d}:00" for h in range(8, 24)]


# ---------- Календарь на нижней клавиатуре ----------

async def test_calendar_current_month_starts_today_and_marks_it():
    keyboard = rows(kb.get_calendar_keyboard(2026, 11, today=date(2026, 11, 16)))
    assert keyboard == [
        ["•16•", "17", "18", "19", "20", "21", "22"],
        ["23", "24", "25", "26", "27", "28", "29"],
        ["30"],
        ["December ▶"],  # назад, в прошлое, нельзя
        [CANCEL],
    ]


async def test_calendar_future_month_has_all_days_and_both_arrows():
    keyboard = rows(kb.get_calendar_keyboard(2026, 12, today=date(2026, 11, 16)))
    days = [t for row in keyboard[:-2] for t in row]
    assert days == [str(d) for d in range(1, 32)]
    assert keyboard[-2:] == [["◀ November", "January ▶"], [CANCEL]]


async def test_calendar_wraps_years():
    assert rows(kb.get_calendar_keyboard(2027, 1, today=date(2026, 11, 16)))[-2] == ["◀ December", "February ▶"]


# ---------- Под сработавшим напоминанием ----------

@pytest.mark.parametrize("repeat_type", ["once", "daily", "monthly"])
async def test_snooze_keyboard_like_screenshot(repeat_type):
    markup = kb.get_snooze_keyboard(7, repeat_type)
    keyboard = [[(b.text, b.callback_data) for b in row] for row in markup.inline_keyboard]
    assert keyboard == [
        [("5 мин", "snooze_7_5"), ("15 мин", "snooze_7_15"), ("30 мин", "snooze_7_30"), ("1 час", "snooze_7_60")],
        [("✅ Готово", "done_7")],
    ]  # «Отменить серию» под сработавшим напоминанием больше нет
