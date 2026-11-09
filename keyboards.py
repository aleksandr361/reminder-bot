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
# Во время создания напоминания эта кнопка всегда внизу
BTN_CANCEL = "❌ Отмена"
BTN_CONFIRM = "✔ Подтвердить выбор"
BTN_START_NOW = "📍 С текущего момента"
# «❌ Отмена» кнопкой в сообщении была в одной из прошлых версий — main.py её ещё понимает
CANCEL_DATA = "cancel_flow"

WEEKDAYS = [
    ("mon", "Пн"), ("tue", "Вт"), ("wed", "Ср"), ("thu", "Чт"),
    ("fri", "Пт"), ("sat", "Сб"), ("sun", "Вс"),
]
MONTHS = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]
# Время на клавиатуре: с 08:00 до 23:00, по 4 в ряд
TIME_PRESETS = [f"{hour:02d}:00" for hour in range(8, 24)]
TIME_COLUMNS = 4
SNOOZE_MINUTES = [(5, "5 мин"), (15, "15 мин"), (30, "30 мин"), (60, "1 час")]
# Частые значения N: «Каждые N часов», «Каждые N дней», «За N дней до конца месяца»
INTERVAL_PRESETS = {
    "hourly": [1, 2, 3, 4, 6, 8, 12, 24],
    "daily": [1, 2, 3, 7, 10, 14, 21, 30],
    "before_end": [1, 2, 3, 5, 7, 10, 14],
}
# Как в календаре показывается сегодняшнее число
TODAY_FORMAT = "•{}•"


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _inline(rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[list(r) for r in rows])



def _chunks(items, size):
    return [items[i:i + size] for i in range(0, len(items), size)]


def _reply(texts, columns: int, placeholder: str | None = None) -> ReplyKeyboardMarkup:
    """Нижняя клавиатура шага: кнопки по columns в ряд и «❌ Отмена» последней строкой."""
    rows = _chunks([KeyboardButton(text=t) for t in texts], columns) + [[KeyboardButton(text=BTN_CANCEL)]]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, input_field_placeholder=placeholder)


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

def get_cancel_keyboard() -> ReplyKeyboardMarkup:
    return _reply([], 1)


def get_repeat_type_keyboard() -> InlineKeyboardMarkup:
    return _inline([
        [_btn("🗓 Однократно", "repeat_once")],
        [_btn("🔁 Каждые N часов", "repeat_hourly")],
        [_btn("🔁 Каждые N дней", "repeat_daily")],
        [_btn("📆 Каждую неделю", "repeat_weekly")],
        [_btn("📆 Каждый месяц", "repeat_monthly")],
    ])


def get_interval_keyboard(kind: str) -> ReplyKeyboardMarkup:
    """Числа для «Каждые N часов» (hourly), «Каждые N дней» (daily), «За N дней до конца месяца» (before_end).
    Любое другое число можно напечатать."""
    return _reply([str(n) for n in INTERVAL_PRESETS[kind]], 4, "Или введите своё число")


def get_weekdays_keyboard(selected=None) -> InlineKeyboardMarkup:
    selected = set(selected or ())
    buttons = [
        _btn(f"✅ {name}" if code in selected else name, f"weekday_{code}")
        for code, name in WEEKDAYS
    ]
    return _inline([buttons[:4], buttons[4:], [_btn(BTN_CONFIRM, "weekday_done")]])


def get_month_mode_keyboard() -> InlineKeyboardMarkup:
    return _inline([
        [_btn("📅 В указанные дни месяца", "monthmode_days")],
        [_btn("📆 В последний день месяца", "monthmode_last")],
        [_btn("⏳ За N дней до конца месяца", "monthmode_before")],
    ])


def get_month_days_keyboard(selected=None) -> InlineKeyboardMarkup:
    """Числа 1–31 (можно выбрать несколько) и «Подтвердить выбор»."""
    selected = set(selected or ())
    buttons = [_btn(f"✅{d}" if d in selected else str(d), f"mday_{d}") for d in range(1, 32)]
    return _inline(_chunks(buttons, 7) + [[_btn(BTN_CONFIRM, "mday_done")]])


def get_time_keyboard(start_now: bool = False) -> ReplyKeyboardMarkup:
    """Клавиатура с часами вместо обычной. Своё время пользователь просто печатает (1015).
    start_now — первой строкой «📍 С текущего момента» (для «Каждые N часов»)."""
    markup = _reply(TIME_PRESETS, TIME_COLUMNS, "Или введите время, например 1015")
    if start_now:
        markup.keyboard.insert(0, [KeyboardButton(text=BTN_START_NOW)])
    return markup


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
    """Кнопки под сработавшим напоминанием: отложить и «Готово» (одинаково для разовых и серий;
    отменить серию можно в «Мои напоминания»)."""
    return _inline([
        [_btn(label, f"snooze_{reminder_id}_{minutes}") for minutes, label in SNOOZE_MINUTES],
        [_btn("✅ Готово", f"done_{reminder_id}")],
    ])


def get_reminder_list_keyboard() -> InlineKeyboardMarkup:
    """Быстрые действия (обработчики new_reminder / my_reminders / settings в main.py)."""
    return _inline([
        [_btn("➕ Новое", "new_reminder"), _btn("🔄 Обновить список", "my_reminders")],
        [_btn("⚙️ Настройки", "settings")],
    ])
