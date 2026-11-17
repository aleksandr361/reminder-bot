"""Тексты и форматирование, общие для диалога (main.py) и рассылки (scheduler.py)."""
from __future__ import annotations

from datetime import date

WEEKDAY_ORDER = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
WEEKDAY_NAMES = {
    'mon': 'Пн', 'tue': 'Вт', 'wed': 'Ср',
    'thu': 'Чт', 'fri': 'Пт', 'sat': 'Сб', 'sun': 'Вс'
}
# Месяц в датах сообщений — по-английски, как в образце: «16 September 2026»
MONTHS = [
    'January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December',
]
# Вложения, которые принимает бот (main.extract_content ищет их в этом порядке).
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
    """08 November 2026"""
    return f"{d.day:02d} {MONTHS[d.month - 1]} {d.year}"


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


# Значок правила в «✅ Напоминание установлено» — как в прошлой версии бота
CONFIRM_ICONS = {'once': '🗓', 'hourly': '🔃', 'daily': '🔃', 'weekly': '📆', 'monthly': '📆'}
# Значок напоминания в «Моих напоминаниях»
LIST_ICONS = {'once': '📆', 'hourly': '🔄🕰', 'daily': '🔄📆', 'weekly': '🔄📅', 'monthly': '📆'}
# Текст напоминания, если это вложение без подписи
ATTACHMENT_FALLBACK = "📎 Вложение"


def every_text(repeat_type: str, n: int) -> str:
    """«Каждые 3 часа», «Каждый час», «Каждые 7 дней», «Каждый день»."""
    n = max(1, _int(n, 1))
    if repeat_type == 'hourly':
        return "Каждый час" if n == 1 else f"Каждые {n} {plural(n, 'час', 'часа', 'часов')}"
    return "Каждый день" if n == 1 else f"Каждые {n} {plural(n, 'день', 'дня', 'дней')}"


def before_end_text(n: int) -> str:
    """«за 3 дня»"""
    return f"за {n} {plural(n, 'день', 'дня', 'дней')}"


def rule_text(repeat_type: str, config: dict) -> str:
    """Правило без значка: «Каждые 3 часа», «Каждую неделю: Пн, Ср», «Каждый месяц, 5 и 29 числа»…"""
    if repeat_type in ('hourly', 'daily'):
        return every_text(repeat_type, config.get('every', 1))
    if repeat_type == 'weekly':
        weekdays = config.get('weekdays')
        return f"Каждую неделю: {format_weekdays(weekdays)}" if weekdays else "Каждую неделю"
    if repeat_type == 'monthly':
        if config.get('last'):
            return "Каждый месяц, последний день"
        if config.get('before_end'):
            return f"Каждый месяц, {before_end_text(_int(config['before_end'], 1))} до конца месяца"
        days = month_days(config)
        return f"Каждый месяц, {join_and(days)} числа" if days else "Каждый месяц"
    return "Однократно"


def describe_repeat(repeat_type: str, config: dict) -> str:
    """Правило в «✅ Напоминание установлено»: «🔃 Каждые 3 часа», «📆 Каждый месяц, 5 и 29 числа»…"""
    return f"{CONFIRM_ICONS.get(repeat_type, '🗓')} {rule_text(repeat_type, config)}"


def list_repeat_text(repeat_type: str, config: dict) -> str:
    """Правило в «Моих напоминаниях» (после «🔄»): дни недели идут отдельной строкой,
    у «за N дней» — без слова «месяца», как в прошлой версии бота."""
    if repeat_type == 'weekly':
        return "Каждую неделю"
    if repeat_type == 'monthly' and config.get('before_end') and not config.get('last'):
        return f"Каждый месяц, {before_end_text(_int(config['before_end'], 1))} до конца"
    return rule_text(repeat_type, config)


def month_note(config: dict) -> str | None:
    """Пояснение для дней 29–31: в коротких месяцах напоминание придёт в последний день."""
    if config.get('last') or config.get('before_end'):
        return None
    if not any(d > 28 for d in month_days(config)):
        return None
    return "ℹ️ В месяцах без указанного числа напоминание придёт в последний день"


def display_text(text: str | None) -> str:
    """Текст напоминания (или подпись к вложению); у вложения без подписи — «📎 Вложение»."""
    return text or ATTACHMENT_FALLBACK
