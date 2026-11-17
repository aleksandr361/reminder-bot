"""Клавиатуры — как в прошлой рабочей версии бота. Каждая кнопка должна попадать в свой обработчик main.py."""
from datetime import date, time

import pytest
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup

import keyboards as kb
from conftest import FakeCallback, FakeMessage, make_user


def inline_buttons(markup):
    assert isinstance(markup, InlineKeyboardMarkup)
    return [b for row in markup.inline_keyboard for b in row]


def callbacks(markup):
    return [b.callback_data for b in inline_buttons(markup)]


def rows_of(markup):
    if isinstance(markup, ReplyKeyboardMarkup):
        return [[b.text for b in row] for row in markup.keyboard]
    return [[b.text for b in row] for row in markup.inline_keyboard]


async def handler_for(main_module, data, raw_state=None):
    """Имя обработчика main.py, который поймает нажатие (без вызова самого обработчика)."""
    cb = FakeCallback(data, FakeMessage(1))
    for handler in main_module.router.callback_query.handlers:
        ok, _ = await handler.check(cb, raw_state=raw_state, state=None, bot=None)
        if ok:
            return handler.callback.__name__
    return None


def flow_state(main_module, name):
    return getattr(main_module.ReminderFlow, name).state


KEYBOARD_CASES = [
    # (как построить клавиатуру, шаг диалога, допустимые обработчики)
    (lambda: kb.get_timezone_geo_or_manual(), None, {"tz_geo_request", "tz_manual"}),
    (lambda: kb.get_timezone_page_keyboard(0), None, {"tz_selected", "tz_cancel", "tz_page_navigate"}),
    (lambda: kb.get_timezone_page_keyboard(3), None, {"tz_selected", "tz_cancel", "tz_page_navigate"}),
    (lambda: kb.get_blocked_reminders_actions_keyboard(), None, {"blocked_resume_all", "blocked_delete_all"}),
    (lambda: kb.get_repeat_type_keyboard(), "waiting_for_repeat_type", {"repeat_type_selected"}),
    (lambda: kb.get_weekdays_keyboard(), "waiting_for_weekdays", {"weekday_toggle"}),
    (lambda: kb.get_weekdays_keyboard({"mon", "sun"}), "waiting_for_weekdays", {"weekday_toggle"}),
    (lambda: kb.get_calendar(2026, 11, -1001234567890, today=date(2026, 11, 8)), "waiting_for_date",
     {"calendar_nav", "date_selected", "ignore_callback"}),
    (lambda: kb.get_reminder_actions_inline(5, "once"), None, {"cancel_single_reminder"}),
    (lambda: kb.get_reminder_actions_inline(5, "daily"), None, {"cancel_series_reminder"}),
    (lambda: kb.get_snooze_keyboard(5, "once"), None, {"snooze_reminder", "done_reminder"}),
    (lambda: kb.get_snooze_keyboard(5, "weekly"), None, {"snooze_reminder", "done_reminder"}),
    (lambda: kb.get_month_mode_keyboard(), "waiting_for_month_mode", {"month_mode_selected"}),
    (lambda: kb.get_month_days_keyboard(), "waiting_for_month_days", {"month_day_toggle"}),
    (lambda: kb.get_month_days_keyboard({1, 31}), "waiting_for_month_days", {"month_day_toggle"}),
]


@pytest.mark.parametrize("build,state_name,allowed", KEYBOARD_CASES)
async def test_every_inline_button_reaches_its_handler(main_module, build, state_name, allowed):
    raw_state = flow_state(main_module, state_name) if state_name else None
    datas = callbacks(build())
    assert datas
    for data in datas:
        assert data and len(data.encode()) <= 64, data  # лимит Telegram на callback_data
        assert await handler_for(main_module, data, raw_state) in allowed, data


async def test_main_menu_three_rows_like_old_bot(main_module, send, fsm):
    await make_user(1)
    markup = kb.get_main_menu_keyboard()
    assert isinstance(markup, ReplyKeyboardMarkup)
    rows = rows_of(markup)
    assert rows == [["➕ Новое напоминание"], ["📋 Мои напоминания"], ["⚙️ Настройки"]]
    for text in [t for row in rows for t in row]:
        msg = FakeMessage(1, text=text)
        result = await send(msg, fsm(1))
        assert result is not UNHANDLED, text
        assert msg.sent, text


def test_timezone_buttons_like_old_bot():
    assert rows_of(kb.get_timezone_geo_or_manual()) == [["📍 Определить по геолокации"], ["🌍 Выбрать вручную"]]


def test_geo_button_requests_location():
    markup = kb.get_timezone_geo_button()
    assert isinstance(markup, ReplyKeyboardMarkup)
    assert any(b.request_location for row in markup.keyboard for b in row)


def test_timezone_list_sorted_by_utc_offset():
    labels = [label for label, _ in kb.ALL_TIMEZONES]
    keys = {key for _, key in kb.ALL_TIMEZONES}
    assert {"Europe/Moscow", "Asia/Kolkata", "America/St_Johns", "UTC"} <= keys
    assert "Factory" not in keys and "localtime" not in keys
    moscow = labels.index(next(l for l, k in kb.ALL_TIMEZONES if k == "Europe/Moscow"))
    new_york = labels.index(next(l for l, k in kb.ALL_TIMEZONES if k == "America/New_York"))
    assert new_york < moscow
    assert "Moscow (UTC+3)" in labels
    assert "Kolkata (UTC+5:30)" in labels


@pytest.mark.parametrize("seconds,label", [
    (10800, "UTC+3"), (0, "UTC+0"), (19800, "UTC+5:30"), (-12600, "UTC-3:30"), (-18000, "UTC-5"),
])
def test_utc_label(seconds, label):
    assert kb._utc_label(seconds) == label


def test_timezone_pages_have_cancel_and_navigation():
    first = rows_of(kb.get_timezone_page_keyboard(0))
    assert len(first) == kb.TIMEZONES_PER_PAGE + 2
    assert first[-2] == ["❌ Отмена"]
    assert first[-1] == ["Вперёд ➡️"]
    last_page = (len(kb.ALL_TIMEZONES) - 1) // kb.TIMEZONES_PER_PAGE
    last = rows_of(kb.get_timezone_page_keyboard(last_page))
    assert last[-1] == ["⬅️ Назад"]
    middle = rows_of(kb.get_timezone_page_keyboard(1))
    assert middle[-1] == ["⬅️ Назад", "Вперёд ➡️"]


def test_blocked_actions_keyboard():
    assert rows_of(kb.get_blocked_reminders_actions_keyboard()) == [["▶️ Возобновить все"], ["🗑 Удалить все"]]


async def test_repeat_type_keyboard_offers_all_types_one_per_row():
    markup = kb.get_repeat_type_keyboard()
    assert rows_of(markup) == [
        ["🗓 Однократно"], ["🔁 Каждые N часов"], ["🔁 Каждые N дней"], ["📆 Каждую неделю"], ["📆 Каждый месяц"],
    ]


@pytest.mark.parametrize("kind,rows", [
    ("hourly", [["1", "2", "3", "4"], ["6", "8", "12", "24"], ["❌ Отмена"]]),
    ("daily", [["1", "2", "3", "7"], ["10", "14", "21", "30"], ["❌ Отмена"]]),
    ("before_end", [["1", "2", "3", "5"], ["7", "10", "14"], ["❌ Отмена"]]),
])
async def test_interval_keyboard_is_bottom_keyboard_with_cancel(kind, rows):
    markup = kb.get_interval_keyboard(kind)
    assert isinstance(markup, ReplyKeyboardMarkup)  # как в прошлой версии — на нижней клавиатуре
    assert rows_of(markup) == rows


async def test_weekdays_keyboard_has_all_days_done_and_marks_selected():
    assert rows_of(kb.get_weekdays_keyboard({"mon"})) == [
        ["✅ Пн", "Вт", "Ср", "Чт"], ["Пт", "Сб", "Вс"], ["✔ Подтвердить выбор"],
    ]


async def test_time_keyboard_has_hours_8_to_23_and_cancel(main_module):
    markup = kb.get_time_keyboard()
    assert isinstance(markup, ReplyKeyboardMarkup)
    assert markup.input_field_placeholder is None
    rows = rows_of(markup)
    assert rows[-1] == ["❌ Отмена"]
    hours = [t for row in rows[:-1] for t in row]
    assert hours == [f"{h:02d}:00" for h in range(8, 24)]
    assert all(len(row) == 4 for row in rows[:-1])
    assert [main_module.parse_time(t) for t in hours] == [time(h, 0) for h in range(8, 24)]


async def test_time_keyboard_with_start_now_button():
    rows = rows_of(kb.get_time_keyboard(start_now=True))
    assert rows[0] == ["📍 С текущего момента"]
    assert rows[-1] == ["❌ Отмена"]
    assert [t for row in rows[1:-1] for t in row] == [f"{h:02d}:00" for h in range(8, 24)]


async def test_cancel_keyboard():
    assert rows_of(kb.get_cancel_keyboard()) == [["❌ Отмена"]]


async def test_month_days_keyboard_like_old_bot():
    rows = rows_of(kb.get_month_days_keyboard({5, 29}))
    assert rows[0] == ["1", "2", "3", "4", "✅ 5", "6", "7"]
    assert rows[4] == ["✅ 29", "30", "31"]
    assert rows[-1] == ["✔ Подтвердить выбор"]


# ---------- Календарь: как в прошлой версии ----------

def day_buttons(markup):
    """{номер дня: кнопка} для кнопок с числами."""
    out = {}
    for b in inline_buttons(markup):
        if b.text.strip().rstrip("*").isdigit():
            out[int(b.text.rstrip("*"))] = b
    return out


def test_calendar_layout_header_weekdays_days_nav():
    markup = kb.get_calendar(2026, 11, 7, today=date(2026, 11, 8))
    rows = rows_of(markup)
    assert rows[0] == ["November 2026"]  # месяц по-английски, одной кнопкой
    assert rows[1] == ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    assert rows[-1] == ["⬅️", "➡️"]  # навигация — внизу
    assert all(len(row) == 7 for row in rows[2:-1])
    assert rows[2][:6] == [" "] * 6 and rows[2][6] == "1"  # 1 ноября 2026 — воскресенье


def test_calendar_marks_today_with_star_and_blocks_past_days():
    buttons = day_buttons(kb.get_calendar(2026, 11, 7, today=date(2026, 11, 8)))
    assert sorted(buttons) == list(range(1, 31))
    assert buttons[8].text == "8*"
    assert buttons[8].callback_data == "date_7_2026_11_8"
    assert all(buttons[d].callback_data == "ignore" for d in range(1, 8))  # прошедшие не нажимаются
    assert all(buttons[d].callback_data == f"date_7_2026_11_{d}" for d in range(9, 31))
    assert all(buttons[d].text == str(d) for d in buttons if d != 8)


def test_calendar_other_month_has_no_mark():
    future = day_buttons(kb.get_calendar(2026, 12, 7, today=date(2026, 11, 8)))
    assert all(b.text == str(d) and b.callback_data.startswith("date_") for d, b in future.items())
    past = day_buttons(kb.get_calendar(2026, 10, 7, today=date(2026, 11, 8)))
    assert all(b.callback_data == "ignore" for b in past.values())


def test_calendar_navigation_wraps_years():
    assert {"cal_7_2026_10", "cal_7_2026_12"} <= set(callbacks(kb.get_calendar(2026, 11, 7)))
    assert {"cal_7_2026_11", "cal_7_2027_1"} <= set(callbacks(kb.get_calendar(2026, 12, 7)))
    assert {"cal_7_2025_12", "cal_7_2026_2"} <= set(callbacks(kb.get_calendar(2026, 1, 7)))


def test_calendar_accepts_chat_id_as_string():
    # main.calendar_nav передаёт chat_id строкой
    assert callbacks(kb.get_calendar(2026, 11, "7")) == callbacks(kb.get_calendar(2026, 11, 7))


@pytest.mark.parametrize("repeat_type", ["once", "daily", "monthly"])
def test_snooze_keyboard_like_screenshot(repeat_type):
    markup = kb.get_snooze_keyboard(7, repeat_type)
    rows = [[(b.text, b.callback_data) for b in row] for row in markup.inline_keyboard]
    assert rows == [
        [("5 мин", "snooze_7_5"), ("15 мин", "snooze_7_15"), ("30 мин", "snooze_7_30"), ("1 час", "snooze_7_60")],
        [("✅ Готово", "done_7")],
    ]
