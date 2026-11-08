"""Тексты и форматирование, общие для диалога (main.py) и рассылки (scheduler.py)."""
from __future__ import annotations

from datetime import date

WEEKDAY_ORDER = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
WEEKDAY_NAMES = {
    'mon': 'Пн', 'tue': 'Вт', 'wed': 'Ср',
    'thu': 'Чт', 'fri': 'Пт', 'sat': 'Сб', 'sun': 'Вс'
}
MONTHS_GENITIVE = [
    'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
]
# Типы повторения — как на кнопках меню
REPEAT_LABELS = {
    'once': '🗓 Однократно',
    'hourly': '🔁 Каждые N часов',
    'daily': '🔁 Каждые N дней',
    'weekly': '📆 Каждую неделю',
    'monthly': '📆 Каждый месяц',
}
# Вложения и как они называются в сообщениях бота.
# Порядок важен: у GIF в Telegram заполнены и animation, и document.
ATTACHMENT_LABELS = {
    'photo': '🖼 Фото',
    'animation': '🎞 GIF',
    'video': '🎬 Видео',
    'video_note': '📹 Кружок',
    'voice': '🎤 Голосовое',
    'audio': '🎵 Аудио',
    'document': '📎 Файл',
    'sticker': '🙂 Стикер',
}


def plural(n: int, one: str, few: str, many: str) -> str:
    """plural(3, 'час', 'часа', 'часов') -> 'часа'"""
    n = abs(n) % 100
    if 11 <= n <= 19:
        return many
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


def format_date(d: date) -> str:
    """08 ноября 2026"""
    return f"{d.day:02d} {MONTHS_GENITIVE[d.month - 1]} {d.year}"


def format_duration(minutes: int) -> str:
    """10 -> «10 минут», 60 -> «1 час», 120 -> «2 часа»"""
    if minutes and minutes % 60 == 0:
        hours = minutes // 60
        return f"{hours} {plural(hours, 'час', 'часа', 'часов')}"
    return f"{minutes} {plural(minutes, 'минуту', 'минуты', 'минут')}"


def join_and(items) -> str:
    """[5, 29] -> «5 и 29», [1, 15, 29] -> «1, 15 и 29»"""
    items = [str(i) for i in items]
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} и {items[-1]}"


def order_weekdays(weekdays) -> list:
    """Дни недели по порядку Пн…Вс (а не по алфавиту английских кодов)."""
    return sorted(weekdays, key=lambda d: WEEKDAY_ORDER.index(d) if d in WEEKDAY_ORDER else len(WEEKDAY_ORDER))


def format_weekdays(weekdays) -> str:
    return ', '.join(WEEKDAY_NAMES.get(day, day) for day in order_weekdays(weekdays))


def _int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def month_days(config: dict) -> list[int]:
    """Дни месяца из настроек (старые напоминания хранили одно число в "day")."""
    days = config.get('days') or ([config['day']] if config.get('day') else [])
    return sorted({d for d in (_int(x, 0) for x in days) if 1 <= d <= 31})


def describe_repeat(repeat_type: str, config: dict) -> str:
    """Правило повторения: «🔁 Каждые 3 часа», «📆 Каждый месяц, 5 и 29 числа»…"""
    if repeat_type in ('hourly', 'daily'):
        n = max(1, _int(config.get('every', 1), 1))
        if repeat_type == 'hourly':
            return "🔁 Каждый час" if n == 1 else f"🔁 Каждые {n} {plural(n, 'час', 'часа', 'часов')}"
        rule = "🔁 Каждый день" if n == 1 else f"🔁 Каждые {n} {plural(n, 'день', 'дня', 'дней')}"
        if config.get('start'):
            try:
                rule += f", начиная с {format_date(date.fromisoformat(config['start']))}"
            except (TypeError, ValueError):
                pass
        return rule
    if repeat_type == 'weekly':
        weekdays = config.get('weekdays')
        return f"📆 Каждую неделю: {format_weekdays(weekdays)}" if weekdays else "📆 Каждую неделю"
    if repeat_type == 'monthly':
        if config.get('last'):
            return "📆 Каждый месяц, последний день"
        if config.get('before_end'):
            n = _int(config['before_end'], 1)
            return f"📆 Каждый месяц, за {n} {plural(n, 'день', 'дня', 'дней')} до конца месяца"
        days = month_days(config)
        return f"📆 Каждый месяц, {join_and(days)} числа" if days else "📆 Каждый месяц"
    rule = REPEAT_LABELS['once']
    if config.get('snoozed'):
        rule += f" (отложено на {format_duration(_int(config['snoozed'], 0))})"
    return rule


def month_note(config: dict) -> str | None:
    """Пояснение для дней 29–31: в коротких месяцах напоминание придёт в последний день."""
    long_days = [d for d in month_days(config) if d > 28] if not (config.get('last') or config.get('before_end')) else []
    if not long_days:
        return None
    return f"ℹ️ В месяцах без {join_and(long_days)} числа напоминание придёт в последний день"


def describe_content(content_type: str | None, text: str | None) -> str:
    """Текст напоминания как есть или «🖼 Фото: подпись» для вложения."""
    text = text or ''
    label = ATTACHMENT_LABELS.get(content_type or 'text')
    if label:
        return f"{label}: {text}" if text else label
    return text
