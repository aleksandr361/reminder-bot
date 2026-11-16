"""Клавиатуры бота.

Шаги создания напоминания — на нижней клавиатуре: варианты и «❌ Отмена» последней строкой.
Кнопки в сообщениях — только у часового пояса, сработавших напоминаний и «Моих напоминаний»;
их callback_data должны совпадать с фильтрами в main.py.
"""
from __future__ import annotations

import calendar
from datetime import date

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from texts import MONTHS, REPEAT_LABELS

# Тексты кнопок главного меню — main.py ловит их через F.text == "..."
BTN_NEW = "➕ Новое напоминание"
BTN_LIST = "📋 Мои напоминания"
BTN_SETTINGS = "⚙️ Настройки"
# «❌ Отмена» — на нижней клавиатуре только пока создаётся напоминание
BTN_CANCEL = "❌ Отмена"
BTN_CONFIRM = "✔ Подтвердить выбор"
BTN_START_NOW = "📍 С текущего момента"
# «❌ Отмена» кнопкой в сообщении была в одной из прошлых версий — main.py её ещё понимает
CANCEL_DATA = "cancel_flow"

WEEKDAYS = [
    ("mon", "Пн"), ("tue", "Вт"), ("wed", "Ср"), ("thu", "Чт"),
    ("fri", "Пт"), ("sat", "Сб"), ("sun", "Вс"),
]
# «Каждый месяц»: как напоминать
MONTH_MODES = {
    "days": "📅 В указанные дни месяца",
    "last": "📆 В последний день месяца",
    "before": "⏳ За N дней до конца месяца",
}
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
# Как в календаре показывается сегодняшнее число и кнопки листания месяцев
TODAY_FORMAT = "•{}•"
PREV_MONTH_FORMAT = "◀ {}"
NEXT_MONTH_FORMAT = "{} ▶"
SELECTED_DAY_FORMAT = "✅ {}"   # отмеченный день недели
SELECTED_DATE_FORMAT = "✅{}"   # отмеченное число месяца (кнопки узкие)


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _inline(rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[list(r) for r in rows])


def _chunks(items, size):
    return [items[i:i + size] for i in range(0, len(items), size)]


def _reply_rows(rows, placeholder: str | None = None) -> ReplyKeyboardMarkup:
    """Нижняя клавиатура шага: строки кнопок и «❌ Отмена» последней строкой."""
    keyboard = [[KeyboardButton(text=t) for t in row] for row in rows] + [[KeyboardButton(text=BTN_CANCEL)]]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True, input_field_placeholder=placeholder)


def _reply(texts, columns: int, placeholder: str | None = None) -> ReplyKeyboardMarkup:
    return _reply_rows(_chunks(list(texts), columns), placeholder)


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


# ---------- Создание напоминания (всё на нижней клавиатуре) ----------

def get_cancel_keyboard() -> ReplyKeyboardMarkup:
    return _reply_rows([])


def get_repeat_type_keyboard() -> ReplyKeyboardMarkup:
    return _reply(REPEAT_LABELS.values(), 1)


def get_interval_keyboard(kind: str) -> ReplyKeyboardMarkup:
    """Числа для «Каждые N часов» (hourly), «Каждые N дней» (daily), «За N дней до конца месяца» (before_end).
    Любое другое число можно напечатать."""
    return _reply([str(n) for n in INTERVAL_PRESETS[kind]], 4, "Или введите своё число")


def get_weekdays_keyboard(selected=None) -> ReplyKeyboardMarkup:
    selected = set(selected or ())
    days = [SELECTED_DAY_FORMAT.format(name) if code in selected else name for code, name in WEEKDAYS]
    return _reply_rows([days[:4], days[4:], [BTN_CONFIRM]])


def get_month_mode_keyboard() -> ReplyKeyboardMarkup:
    return _reply(MONTH_MODES.values(), 1)


def get_month_days_keyboard(selected=None) -> ReplyKeyboardMarkup:
    """Числа 1–31 (можно выбрать несколько) и «Подтвердить выбор»."""
    selected = set(selected or ())
    days = [SELECTED_DATE_FORMAT.format(d) if d in selected else str(d) for d in range(1, 32)]
    return _reply_rows(_chunks(days, 7) + [[BTN_CONFIRM]])


def get_calendar_keyboard(year: int, month: int, today: date) -> ReplyKeyboardMarkup:
    """Числа месяца (в текущем — начиная с сегодня, оно выделено) и листание на соседние месяцы.
    Назад в прошлое листать нельзя."""
    first = today.day if (year, month) == (today.year, today.month) else 1
    last = calendar.monthrange(year, month)[1]
    days = [TODAY_FORMAT.format(d) if (year, month, d) == (today.year, today.month, today.day) else str(d)
            for d in range(first, last + 1)]

    prev_m = 12 if month == 1 else month - 1
    next_m = 1 if month == 12 else month + 1
    nav = [NEXT_MONTH_FORMAT.format(MONTHS[next_m - 1])]
    if (year, month) > (today.year, today.month):
        nav.insert(0, PREV_MONTH_FORMAT.format(MONTHS[prev_m - 1]))
    return _reply_rows(_chunks(days, 7) + [nav], "Или введите дату, например 25.11")


def get_time_keyboard(start_now: bool = False) -> ReplyKeyboardMarkup:
    """Клавиатура с часами вместо обычной. Своё время пользователь просто печатает (1015).
    start_now — первой строкой «📍 С текущего момента» (для «Каждые N часов»)."""
    rows = _chunks(TIME_PRESETS, TIME_COLUMNS)
    if start_now:
        rows.insert(0, [BTN_START_NOW])
    return _reply_rows(rows, "Или введите время, например 1015")


# ---------- Напоминания (кнопки в сообщениях) ----------

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
