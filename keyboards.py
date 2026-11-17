"""Клавиатуры бота — как в прошлой рабочей версии (папка remember).
callback_data здесь должны совпадать с фильтрами в main.py."""
from __future__ import annotations

import calendar
from datetime import date, datetime
from zoneinfo import ZoneInfo, available_timezones

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from texts import MONTHS

# Тексты кнопок главного меню — main.py ловит их через F.text == "..."
BTN_NEW = "➕ Новое напоминание"
BTN_LIST = "📋 Мои напоминания"
BTN_SETTINGS = "⚙️ Настройки"
# «❌ Отмена» — на нижней клавиатуре шагов создания; в главном меню её нет
BTN_CANCEL = "❌ Отмена"
BTN_CONFIRM = "✔ Подтвердить выбор"
BTN_START_NOW = "📍 С текущего момента"
# «❌ Отмена» кнопкой в сообщении была в прошлых версиях — main.py её ещё понимает
CANCEL_DATA = "cancel_flow"

WEEKDAYS = [
    ("mon", "Пн"), ("tue", "Вт"), ("wed", "Ср"), ("thu", "Чт"),
    ("fri", "Пт"), ("sat", "Сб"), ("sun", "Вс"),
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
TODAY_FORMAT = "{}*"
TIMEZONES_PER_PAGE = 50


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _inline(rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[list(r) for r in rows])


def _chunks(items, size):
    return [items[i:i + size] for i in range(0, len(items), size)]


def _reply(texts, columns: int) -> ReplyKeyboardMarkup:
    """Нижняя клавиатура шага: кнопки по columns в ряд и «❌ Отмена» последней строкой."""
    rows = _chunks([KeyboardButton(text=t) for t in texts], columns) + [[KeyboardButton(text=BTN_CANCEL)]]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


# ---------- Главное меню ----------

def get_main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_NEW)],
            [KeyboardButton(text=BTN_LIST)],
            [KeyboardButton(text=BTN_SETTINGS)],
        ],
        resize_keyboard=True,
    )


def get_blocked_reminders_actions_keyboard() -> InlineKeyboardMarkup:
    """После разблокировки бота: что сделать с приостановленными напоминаниями."""
    return _inline([
        [_btn("▶️ Возобновить все", "blocked_resume_all")],
        [_btn("🗑 Удалить все", "blocked_delete_all")],
    ])


# ---------- Часовой пояс ----------

def get_timezone_geo_or_manual() -> InlineKeyboardMarkup:
    return _inline([
        [_btn("📍 Определить по геолокации", "tz_geo")],
        [_btn("🌍 Выбрать вручную", "tz_manual")],
    ])


def get_timezone_geo_button() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📍 Отправить геолокацию", request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def _utc_label(offset_seconds: int) -> str:
    """10800 -> «UTC+3», 19800 -> «UTC+5:30», -12600 -> «UTC-3:30»."""
    sign = "-" if offset_seconds < 0 else "+"
    hours, rest = divmod(abs(offset_seconds), 3600)
    minutes = rest // 60
    return f"UTC{sign}{hours}" + (f":{minutes:02d}" if minutes else "")


def build_sorted_timezones() -> list[tuple[str, str]]:
    """[(«Moscow (UTC+3)», «Europe/Moscow»), …] — все пояса, по возрастанию смещения от UTC (на сейчас)."""
    zones = []
    for key in available_timezones() - {"Factory", "localtime"}:
        try:
            offset = datetime.now(ZoneInfo(key)).utcoffset()
        except Exception:
            continue
        if offset is None:
            continue
        seconds = int(offset.total_seconds())
        label = f"{key.replace('_', ' ').split('/')[-1]} ({_utc_label(seconds)})"
        zones.append((seconds, label, key))
    zones.sort()
    return [(label, key) for _, label, key in zones]


ALL_TIMEZONES = build_sorted_timezones()


def get_timezone_page_keyboard(page: int = 0) -> InlineKeyboardMarkup:
    """Страница списка часовых поясов: по одному в ряд, «❌ Отмена» и «⬅️ Назад» / «Вперёд ➡️»."""
    start = page * TIMEZONES_PER_PAGE
    end = start + TIMEZONES_PER_PAGE
    rows = [[_btn(label, f"tz_select_{key}")] for label, key in ALL_TIMEZONES[start:end]]
    rows.append([_btn(BTN_CANCEL, "tz_cancel")])
    nav = []
    if page > 0:
        nav.append(_btn("⬅️ Назад", f"tz_page_{page - 1}"))
    if end < len(ALL_TIMEZONES):
        nav.append(_btn("Вперёд ➡️", f"tz_page_{page + 1}"))
    if nav:
        rows.append(nav)
    return _inline(rows)


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
    """Числа для «Каждые N часов» (hourly), «Каждые N дней» (daily), «За N дней до конца месяца»
    (before_end) — на нижней клавиатуре, по 4 в ряд, и «❌ Отмена». Своё число можно напечатать."""
    return _reply([str(n) for n in INTERVAL_PRESETS[kind]], 4)


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
    """Числа 1–31 по 7 в ряд (можно выбрать несколько) и «Подтвердить выбор»."""
    selected = set(selected or ())
    buttons = [_btn(f"✅ {d}" if d in selected else str(d), f"mday_{d}") for d in range(1, 32)]
    return _inline(_chunks(buttons, 7) + [[_btn(BTN_CONFIRM, "mday_done")]])


def get_time_keyboard(start_now: bool = False) -> ReplyKeyboardMarkup:
    """Клавиатура с часами. Своё время пользователь просто печатает (1015).
    start_now — первой строкой «📍 С текущего момента» (для «Каждые N часов»)."""
    markup = _reply(TIME_PRESETS, TIME_COLUMNS)
    if start_now:
        markup.keyboard.insert(0, [KeyboardButton(text=BTN_START_NOW)])
    return markup


def get_calendar(year: int, month: int, chat_id, today: date | None = None) -> InlineKeyboardMarkup:
    """Календарь на месяц, как в прошлой версии: сверху «November 2026» и дни недели,
    сегодня — со звёздочкой (TODAY_FORMAT), прошедшие числа не нажимаются, внизу ⬅️ ➡️.

    today — сегодняшняя дата пользователя. chat_id зашит в кнопки, чтобы main.py проверял владельца.
    """
    year, month = int(year), int(month)

    def day_button(day: int) -> InlineKeyboardButton:
        if not day:
            return _btn(" ", "ignore")
        current = date(year, month, day)
        text = TODAY_FORMAT.format(day) if today is not None and current == today else str(day)
        if today is not None and current < today:
            return _btn(text, "ignore")
        return _btn(text, f"date_{chat_id}_{year}_{month}_{day}")

    prev_y, prev_m = (year - 1, 12) if month == 1 else (year, month - 1)
    next_y, next_m = (year + 1, 1) if month == 12 else (year, month + 1)

    rows = [
        [_btn(f"{MONTHS[month - 1]} {year}", "ignore")],
        [_btn(name, "ignore") for _, name in WEEKDAYS],
    ]
    rows += [[day_button(day) for day in week] for week in calendar.monthcalendar(year, month)]
    rows.append([
        _btn("⬅️", f"cal_{chat_id}_{prev_y}_{prev_m}"),
        _btn("➡️", f"cal_{chat_id}_{next_y}_{next_m}"),
    ])
    return _inline(rows)


# ---------- Напоминания ----------

def get_reminder_actions_inline(reminder_id: int, repeat_type: str = "once") -> InlineKeyboardMarkup:
    """Кнопка под напоминанием в списке."""
    if repeat_type == "once":
        return _inline([[_btn("❌ Отменить", f"cancel_reminder_{reminder_id}")]])
    return _inline([[_btn("❌ Отменить серию", f"cancel_series_{reminder_id}")]])


def get_snooze_keyboard(reminder_id: int, repeat_type: str = "once") -> InlineKeyboardMarkup:
    """Кнопки под сработавшим напоминанием: отложить и «Готово»."""
    return _inline([
        [_btn(label, f"snooze_{reminder_id}_{minutes}") for minutes, label in SNOOZE_MINUTES],
        [_btn("✅ Готово", f"done_{reminder_id}")],
    ])
