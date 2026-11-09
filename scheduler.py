"""Фоновая рассылка напоминаний.

Раз в CHECK_INTERVAL секунд берёт из базы напоминания, время которых наступило,
отправляет их и переносит повторяющиеся на следующий раз.
"""
from __future__ import annotations

import asyncio
import calendar
import json
import logging
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)

from keyboards import get_snooze_keyboard
from storage import get_due_reminders, set_reminder_run_at, update_reminder_status
from texts import month_days

log = logging.getLogger(__name__)

UTC = timezone.utc
CHECK_INTERVAL = 10  # секунд
MAX_MESSAGE_LEN = 4096  # лимит Telegram на длину сообщения
MAX_CAPTION_LEN = 1024  # лимит Telegram на подпись к фото/файлу
BELL = "🔔"  # сработавшее напоминание: «🔔» + текст; «✅ Готово» меняет его на «✅»
# Вложения с подписью и без неё (кружок, стикер) — какой метод Bot их отправляет
CAPTION_SENDERS = {
    "photo": "send_photo", "video": "send_video", "animation": "send_animation",
    "document": "send_document", "audio": "send_audio", "voice": "send_voice",
}
NO_CAPTION_SENDERS = {"video_note": "send_video_note", "sticker": "send_sticker"}
WEEKDAY_INDEX = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _zone(tz_name: str | None):
    try:
        return ZoneInfo(tz_name) if tz_name else UTC
    except Exception:
        return UTC


def _config(repeat_config: str | None) -> dict:
    try:
        config = json.loads(repeat_config) if repeat_config else {}
    except (TypeError, ValueError):
        return {}
    return config if isinstance(config, dict) else {}


def _every(config: dict) -> int:
    """N из «каждые N часов/дней» (у старых напоминаний его нет — значит, 1)."""
    try:
        return max(1, int(config.get("every", 1)))
    except (TypeError, ValueError):
        return 1


def _weekdays(config: dict) -> set[int]:
    days = config.get("weekdays") or []
    return {WEEKDAY_INDEX[d] for d in days if d in WEEKDAY_INDEX} if isinstance(days, list) else set()


def compute_next_run(repeat_type: str, run_at: datetime, tz_name: str | None,
                     repeat_config: str | None, now: datetime) -> datetime | None:
    """Следующее срабатывание (в UTC) после now, или None для однократного.

    Если бот был выключен, пропущенные разы не догоняются — берётся ближайший в будущем.
    Дни, недели и месяцы сохраняют местное время пользователя (и при переводе часов).
    """
    config = _config(repeat_config)

    if repeat_type == "hourly":
        step = timedelta(hours=_every(config))
        missed = max(0, (now - run_at) // step)
        return run_at + step * (missed + 1)

    tz = _zone(tz_name)
    local = run_at.astimezone(tz)
    now_local_date = now.astimezone(tz).date()

    if repeat_type == "daily":
        n = _every(config)
        steps = max(1, (now_local_date - local.date()).days // n)  # сразу перескочить пропущенное
        day = local.date() + timedelta(days=n * steps)
        while True:
            candidate = datetime.combine(day, local.time(), tzinfo=tz)
            if candidate > now:
                return candidate.astimezone(UTC)
            day += timedelta(days=n)

    if repeat_type == "weekly":
        days = _weekdays(config) or {local.weekday()}
        day = max(local.date() + timedelta(days=1), now_local_date)
        for _ in range(15):
            candidate = datetime.combine(day, local.time(), tzinfo=tz)
            if candidate > now and day.weekday() in days:
                return candidate.astimezone(UTC)
            day += timedelta(days=1)

    if repeat_type == "monthly":
        if not (config.get("last") or config.get("before_end") or month_days(config)):
            config = {"days": [local.day]}
        return next_monthly_run(config, tz, local.time(), now)

    return None


def _dates_in_month(year: int, month: int, config: dict) -> list[int]:
    """Числа месяца, в которые срабатывает «каждый месяц». Нет такого числа (31-е в феврале) — последний день."""
    last_day = calendar.monthrange(year, month)[1]
    if config.get("last"):
        return [last_day]
    if config.get("before_end"):
        try:
            n = int(config["before_end"])
        except (TypeError, ValueError):
            n = 1
        return [max(1, last_day - n)]
    return sorted({min(day, last_day) for day in month_days(config)})


def next_monthly_run(config: dict, tz, at: time, after: datetime) -> datetime | None:
    """Ближайшее срабатывание «каждый месяц» в местное время at строго после after (результат в UTC)."""
    local_after = after.astimezone(tz)
    year, month = local_after.year, local_after.month
    for _ in range(1200):
        for day in _dates_in_month(year, month, config):
            candidate = datetime.combine(date(year, month, day), at, tzinfo=tz)
            if candidate > after:
                return candidate.astimezone(UTC)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return None


async def _send(bot, reminder: dict):
    """Отправить напоминание: текстом или тем же вложением, с кнопками «Отложить / Готово»."""
    chat_id = reminder["chat_id"]
    kind = reminder.get("content_type") or "text"
    file_id = reminder.get("file_id")
    text = reminder.get("text") or ""
    keyboard = get_snooze_keyboard(reminder["id"], reminder.get("repeat_type") or "once")

    # Только колокольчик и само напоминание: «🔔Купить хлеб»
    if kind in CAPTION_SENDERS and file_id:
        await getattr(bot, CAPTION_SENDERS[kind])(
            chat_id, file_id, caption=f"{BELL}{text}"[:MAX_CAPTION_LEN], parse_mode=None, reply_markup=keyboard
        )
    elif kind in NO_CAPTION_SENDERS and file_id:
        # У кружков и стикеров подписи нет — кнопки прямо под ними
        await getattr(bot, NO_CAPTION_SENDERS[kind])(chat_id, file_id, reply_markup=keyboard)
    else:
        await bot.send_message(
            chat_id,
            f"{BELL}{text}"[:MAX_MESSAGE_LEN],
            parse_mode=None,  # текст пользователя отправляем как есть
            reply_markup=keyboard,
        )


async def _deliver(bot, reminder: dict, now: datetime):
    reminder_id = reminder["id"]
    repeat_type = reminder.get("repeat_type") or "once"

    try:
        await _send(bot, reminder)
    except TelegramRetryAfter as e:
        log.warning("Telegram просит подождать %s с, напоминание %s отправим позже", e.retry_after, reminder_id)
        return
    except TelegramNetworkError as e:
        log.warning("Нет связи с Telegram (%s), напоминание %s отправим позже", e, reminder_id)
        return
    except (TelegramForbiddenError, TelegramBadRequest) as e:
        # Бот заблокирован, чат удалён, файл недоступен и т.п. — повторять бессмысленно
        log.warning("Напоминание %s не доставлено: %s", reminder_id, e)
        await update_reminder_status(reminder_id, "failed")
        return

    run_at = datetime.fromisoformat(reminder["run_at"]).replace(tzinfo=UTC)
    next_run = compute_next_run(repeat_type, run_at, reminder.get("timezone"),
                                reminder.get("repeat_config"), now)
    if next_run is None:
        await update_reminder_status(reminder_id, "sent")
    else:
        await set_reminder_run_at(reminder_id, next_run)


async def check_due_reminders(bot):
    """Один проход: отправить всё, что пора отправить."""
    now = datetime.now(UTC)
    for reminder in await get_due_reminders(now):
        try:
            await _deliver(bot, reminder, now)
        except Exception:
            log.exception("Ошибка при отправке напоминания %s", reminder.get("id"))


class ReminderScheduler:
    def __init__(self, bot, interval: float = CHECK_INTERVAL):
        self.bot = bot
        self.interval = interval
        self._task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self):
        """Запустить в фоне. Вызывать внутри работающего asyncio (как в main.py)."""
        if not self.running:
            self._task = asyncio.get_running_loop().create_task(self._run(), name="reminder-scheduler")

    def shutdown(self):
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _run(self):
        log.info("Планировщик напоминаний запущен (проверка каждые %s с)", self.interval)
        while True:
            try:
                await check_due_reminders(self.bot)
            except Exception:
                log.exception("Ошибка при проверке напоминаний")
            await asyncio.sleep(self.interval)


def create_scheduler(bot, interval: float = CHECK_INTERVAL) -> ReminderScheduler:
    return ReminderScheduler(bot, interval)
