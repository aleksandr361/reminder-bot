"""Клавиатуры бота. callback_data здесь должны совпадать с фильтрами в main.py."""
from __future__ import annotations

import calendar
from datetime import date

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

# Тексты кнопок главного меню — main.py ловит их через F.text == "..."
BTN_NEW = "➕ Новое напоминание"
BTN_LIST = "📋 Мои напоминания"
BTN_SETTINGS = "⚙️ Настройки"

WEEKDAYS = [
    ("mon", "Пн"), ("tue", "Вт"), ("wed", "Ср"), ("thu", "Чт"),
    ("fri", "Пт"), ("sat", "Сб"), ("sun", "Вс"),
]
MONTHS = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]
# Время на клавиатуре: каждый час, по 4 в ряд
TIME_PRESETS = [f"{hour:02d}:00" for hour in range(24)]
TIME_COLUMNS = 4
SNOOZE_MINUTES = [(10, "10 мин"), (30, "30 мин"), (60, "1 час")]
# Частые значения N для «Каждые N часов» (hourly) и «Каждые N дней» (daily)
INTERVAL_PRESETS = {
    "hourly": [1, 2, 3, 4, 6, 8, 12, 24],
    "daily": [1, 2, 3, 4, 5, 7, 10, 14],
}
# Как в календаре показывается сегодняшнее число
TODAY_FORMAT = "•{}•"


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _inline(rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[list(r) for r in rows])


def _chunks(items, size):
    return [items[i:i + size] for i in range(0, len(items), size)]


# ---------- Главное меню и часовой пояс ----------

def get_main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_NEW)],
            [KeyboardButton(text=BTN_LIST), KeyboardButton(text=BTN_SETTINGS)],
        ],
        resize_keyboard=True,
    )


def get_timezone_geo_or_manual() -> InlineKeyboardMarkup:
    return _inline([
        [_btn("📍 По геолокации", "tz_geo")],
        [_btn("⌨️ Ввести вручную", "tz_manual")],
    ])


def get_timezone_geo_button() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📍 Отправить геолокацию", request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


# ---------- Создание напоминания ----------

def get_repeat_type_keyboard() -> InlineKeyboardMarkup:
    return _inline([
        [_btn("📅 Однократно", "repeat_once")],
        [_btn("🔁 Каждые N часов", "repeat_hourly")],
        [_btn("🔁 Каждые N дней", "repeat_daily")],
        [_btn("📆 Каждую неделю", "repeat_weekly")],
        [_btn("📆 Каждый месяц", "repeat_monthly")],
    ])


def get_interval_keyboard(repeat_type: str) -> ReplyKeyboardMarkup:
    """Числа для «Каждые N часов» / «Каждые N дней». Любое другое число можно напечатать."""
    numbers = INTERVAL_PRESETS[repeat_type]
    buttons = [KeyboardButton(text=str(n)) for n in numbers]
    return ReplyKeyboardMarkup(
        keyboard=_chunks(buttons, 4),
        resize_keyboard=True,
        input_field_placeholder="Или введите своё число",
    )


def get_weekdays_keyboard(selected=None) -> InlineKeyboardMarkup:
    selected = set(selected or ())
    buttons = [
        _btn(f"✅ {name}" if code in selected else name, f"weekday_{code}")
        for code, name in WEEKDAYS
    ]
    return _inline([buttons[:4], buttons[4:], [_btn("Готово ➡️", "weekday_done")]])


def get_time_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура с часами вместо обычной. Своё время пользователь просто печатает (ЧЧММ)."""
    buttons = [KeyboardButton(text=t) for t in TIME_PRESETS]
    return ReplyKeyboardMarkup(
        keyboard=_chunks(buttons, TIME_COLUMNS),
        resize_keyboard=True,
        input_field_placeholder="Или введите ЧЧММ, например 1430",
    )


def get_calendar(year: int, month: int, chat_id, today: date | None = None) -> InlineKeyboardMarkup:
    """Календарь на месяц. chat_id зашит в кнопки, чтобы main.py проверял владельца.

    today — сегодняшняя дата пользователя; это число выделяется (TODAY_FORMAT).
    """
    year, month = int(year), int(month)

    def day_text(day: int) -> str:
        if today is not None and (today.year, today.month, today.day) == (year, month, day):
            return TODAY_FORMAT.format(day)
        return str(day)

    prev_y, prev_m = (year - 1, 12) if month == 1 else (year, month - 1)
    next_y, next_m = (year + 1, 1) if month == 12 else (year, month + 1)

    rows = [
        [
            _btn("◀️", f"cal_{chat_id}_{prev_y}_{prev_m}"),
            _btn(f"{MONTHS[month - 1]} {year}", "ignore"),
            _btn("▶️", f"cal_{chat_id}_{next_y}_{next_m}"),
        ],
        [_btn(name, "ignore") for _, name in WEEKDAYS],
    ]
    for week in calendar.monthcalendar(year, month):
        rows.append([
            _btn(day_text(day), f"date_{chat_id}_{year}_{month}_{day}") if day else _btn(" ", "ignore")
            for day in week
        ])
    return _inline(rows)


# ---------- Напоминания ----------

def get_reminder_actions_inline(reminder_id: int, repeat_type: str = "once") -> InlineKeyboardMarkup:
    """Кнопки под напоминанием в списке."""
    if repeat_type == "once":
        return _inline([[_btn("❌ Отменить", f"cancel_reminder_{reminder_id}")]])
    return _inline([[_btn("❌ Отменить серию", f"cancel_series_{reminder_id}")]])


def get_snooze_keyboard(reminder_id: int, repeat_type: str = "once") -> InlineKeyboardMarkup:
    """Кнопки под сработавшим напоминанием."""
    rows = [
        [_btn(f"⏰ {label}", f"snooze_{reminder_id}_{minutes}") for minutes, label in SNOOZE_MINUTES],
        [_btn("✅ Готово", f"done_{reminder_id}")],
    ]
    if repeat_type != "once":
        rows.append([_btn("❌ Отменить серию", f"cancel_series_{reminder_id}")])
    return _inline(rows)


def get_reminder_list_keyboard() -> InlineKeyboardMarkup:
    """Быстрые действия (обработчики new_reminder / my_reminders / settings в main.py)."""
    return _inline([
        [_btn("➕ Новое", "new_reminder"), _btn("🔄 Обновить список", "my_reminders")],
        [_btn("⚙️ Настройки", "settings")],
    ])
