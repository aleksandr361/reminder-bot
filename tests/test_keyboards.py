"""Клавиатуры проверяются против main.py: каждая кнопка должна попадать в свой обработчик."""
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
    (lambda: kb.get_repeat_type_keyboard(), "waiting_for_repeat_type", {"repeat_type_selected", "cancel_flow_button"}),
    (lambda: kb.get_weekdays_keyboard(), "waiting_for_weekdays", {"weekday_toggle", "cancel_flow_button"}),
    (lambda: kb.get_weekdays_keyboard({"mon", "sun"}), "waiting_for_weekdays", {"weekday_toggle", "cancel_flow_button"}),
    (lambda: kb.get_calendar(2026, 11, -1001234567890), "waiting_for_date",
     {"calendar_nav", "date_selected", "ignore_callback", "cancel_flow_button"}),
    (lambda: kb.get_reminder_actions_inline(5, "once"), None, {"cancel_single_reminder"}),
    (lambda: kb.get_reminder_actions_inline(5, "daily"), None, {"cancel_series_reminder"}),
    (lambda: kb.get_snooze_keyboard(5, "once"), None, {"snooze_reminder", "done_reminder"}),
    (lambda: kb.get_snooze_keyboard(5, "weekly"), None,
     {"snooze_reminder", "done_reminder", "cancel_series_reminder"}),
    (lambda: kb.get_month_mode_keyboard(), "waiting_for_month_mode", {"month_mode_selected", "cancel_flow_button"}),
    (lambda: kb.get_month_days_keyboard(), "waiting_for_month_days", {"month_day_toggle", "cancel_flow_button"}),
    (lambda: kb.get_month_days_keyboard({1, 31}), "waiting_for_month_days", {"month_day_toggle", "cancel_flow_button"}),
    (lambda: kb.get_reminders_list_keyboard([5, 6, 7, 8]), None, {"list_cancel_reminder"}),
    (lambda: kb.get_reminder_list_keyboard(), None,
     {"new_reminder_callback", "my_reminders_callback", "settings_callback"}),
]


@pytest.mark.parametrize("build,state_name,allowed", KEYBOARD_CASES)
async def test_every_inline_button_reaches_its_handler(main_module, build, state_name, allowed):
    raw_state = flow_state(main_module, state_name) if state_name else None
    datas = callbacks(build())
    assert datas
    for data in datas:
        assert data and len(data.encode()) <= 64, data  # лимит Telegram на callback_data
        assert await handler_for(main_module, data, raw_state) in allowed, data


async def test_repeat_type_keyboard_offers_all_types_one_per_row():
    markup = kb.get_repeat_type_keyboard()
    assert callbacks(markup) == [
        "repeat_once", "repeat_hourly", "repeat_daily", "repeat_weekly", "repeat_monthly", "cancel_flow"
    ]
    assert all(len(row) == 1 for row in markup.inline_keyboard)


@pytest.mark.parametrize("kind", ["hourly", "daily", "before_end"])
async def test_interval_keyboard_is_reply_keyboard_with_numbers_and_cancel(kind):
    markup = kb.get_interval_keyboard(kind)
    assert isinstance(markup, ReplyKeyboardMarkup)
    rows = [[b.text for b in row] for row in markup.keyboard]
    assert rows[-1] == ["❌ Отмена"]
    numbers = [t for row in rows[:-1] for t in row]
    assert numbers and all(t.isdigit() and int(t) >= 1 for t in numbers)
    assert numbers[0] == "1"
    assert markup.input_field_placeholder and "число" in markup.input_field_placeholder.lower()


async def test_weekdays_keyboard_has_all_days_done_and_marks_selected():
    datas = callbacks(kb.get_weekdays_keyboard())
    assert set(datas) == {f"weekday_{d}" for d in
                          ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]} | {"weekday_done", "cancel_flow"}

    def text_of(markup, data):
        return next(b.text for b in inline_buttons(markup) if b.callback_data == data)

    assert "✅" not in text_of(kb.get_weekdays_keyboard(), "weekday_mon")
    assert "✅" in text_of(kb.get_weekdays_keyboard({"mon"}), "weekday_mon")
    assert "✅" not in text_of(kb.get_weekdays_keyboard({"mon"}), "weekday_tue")


async def test_time_keyboard_has_hours_8_to_23_and_cancel(main_module):
    markup = kb.get_time_keyboard()
    assert isinstance(markup, ReplyKeyboardMarkup)  # обычная клавиатура, а не кнопки в сообщении
    rows = [[b.text for b in row] for row in markup.keyboard]
    assert rows[-1] == ["❌ Отмена"]
    hours = [t for row in rows[:-1] for t in row]
    assert hours == [f"{h:02d}:00" for h in range(8, 24)]
    assert all(len(row) == 4 for row in rows[:-1])
    assert [main_module.parse_time(t) for t in hours] == [time(h, 0) for h in range(8, 24)]


async def test_cancel_keyboard():
    markup = kb.get_cancel_keyboard()
    assert [[b.text for b in row] for row in markup.keyboard] == [["❌ Отмена"]]


async def test_month_days_keyboard_marks_selected():
    texts = {b.callback_data: b.text for row in kb.get_month_days_keyboard({5, 29}).inline_keyboard for b in row}
    assert texts["mday_5"] != "5" and "5" in texts["mday_5"]
    assert texts["mday_6"] == "6"
    assert texts["mday_done"] == "✔ Подтвердить выбор"


async def test_calendar_has_every_day_of_month():
    datas = callbacks(kb.get_calendar(2026, 11, 7))
    days = [int(d.split("_")[4]) for d in datas if d.startswith("date_")]
    assert days == list(range(1, 31))
    assert all(d.startswith("date_7_2026_11_") for d in datas if d.startswith("date_"))


async def test_calendar_navigation_wraps_years():
    assert {"cal_7_2026_10", "cal_7_2026_12"} <= set(callbacks(kb.get_calendar(2026, 11, 7)))
    assert {"cal_7_2026_11", "cal_7_2027_1"} <= set(callbacks(kb.get_calendar(2026, 12, 7)))
    assert {"cal_7_2025_12", "cal_7_2026_2"} <= set(callbacks(kb.get_calendar(2026, 1, 7)))


async def test_calendar_accepts_chat_id_as_string():
    # main.calendar_nav передаёт chat_id строкой
    assert callbacks(kb.get_calendar(2026, 11, "7")) == callbacks(kb.get_calendar(2026, 11, 7))


async def test_calendar_shows_month_name():
    texts = [b.text for b in inline_buttons(kb.get_calendar(2026, 11, 7))]
    assert any("Ноябрь 2026" in t for t in texts)


async def test_main_menu_buttons_reach_main_handlers(main_module, send, fsm):
    await make_user(1)
    markup = kb.get_main_menu_keyboard()
    assert isinstance(markup, ReplyKeyboardMarkup)
    texts = [b.text for row in markup.keyboard for b in row]
    assert len(texts) == 3
    for text in texts:
        msg = FakeMessage(1, text=text)
        result = await send(msg, fsm(1))
        assert result is not UNHANDLED, text
        assert msg.sent, text


async def test_geo_button_requests_location():
    markup = kb.get_timezone_geo_button()
    assert isinstance(markup, ReplyKeyboardMarkup)
    buttons = [b for row in markup.keyboard for b in row]
    assert any(b.request_location for b in buttons)


# ---------- Выделение сегодняшнего числа ----------

def day_button_texts(markup):
    """{номер дня: текст кнопки} для кнопок с датами."""
    return {int(b.callback_data.split("_")[4]): b.text
            for b in inline_buttons(markup) if b.callback_data.startswith("date_")}


async def test_calendar_marks_today():
    texts = day_button_texts(kb.get_calendar(2026, 11, 7, today=date(2026, 11, 8)))
    assert texts[8] != "8" and "8" in texts[8]
    assert all(texts[d] == str(d) for d in texts if d != 8)


async def test_calendar_today_keeps_its_callback():
    datas = callbacks(kb.get_calendar(2026, 11, 7, today=date(2026, 11, 8)))
    assert "date_7_2026_11_8" in datas


async def test_calendar_other_month_has_no_mark():
    texts = day_button_texts(kb.get_calendar(2026, 12, 7, today=date(2026, 11, 8)))
    assert all(texts[d] == str(d) for d in texts)
    texts = day_button_texts(kb.get_calendar(2025, 11, 7, today=date(2026, 11, 8)))
    assert all(texts[d] == str(d) for d in texts)


async def test_calendar_without_today_has_no_mark():
    texts = day_button_texts(kb.get_calendar(2026, 11, 7))
    assert all(texts[d] == str(d) for d in texts)



async def test_time_keyboard_with_start_now_button():
    rows = [[b.text for b in row] for row in kb.get_time_keyboard(start_now=True).keyboard]
    assert rows[0] == ["📍 С текущего момента"]
    assert rows[-1] == ["❌ Отмена"]
    assert [t for row in rows[1:-1] for t in row] == [f"{h:02d}:00" for h in range(8, 24)]


async def test_flow_inline_keyboards_end_with_cancel():
    for markup in [kb.get_repeat_type_keyboard(), kb.get_weekdays_keyboard(), kb.get_month_mode_keyboard(),
                   kb.get_month_days_keyboard(), kb.get_calendar(2026, 11, 7)]:
        last_row = markup.inline_keyboard[-1]
        assert [(b.text, b.callback_data) for b in last_row] == [("❌ Отмена", "cancel_flow")]


async def test_reminders_list_keyboard_numbers_buttons():
    markup = kb.get_reminders_list_keyboard([11, 12, 13, 14])
    buttons = [(b.text, b.callback_data) for row in markup.inline_keyboard for b in row]
    assert buttons == [("❌ №1", "lcancel_11"), ("❌ №2", "lcancel_12"), ("❌ №3", "lcancel_13"), ("❌ №4", "lcancel_14")]
