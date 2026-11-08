from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
from datetime import datetime, time, date, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, Router, F
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.token import TokenValidationError
from timezonefinderL import TimezoneFinder

from storage import (
    init_db, get_or_create_user, set_user_timezone, get_user_timezone,
    create_reminder, get_user_reminders, update_reminder_status, get_reminder_by_id
)
from keyboards import (
    get_timezone_geo_or_manual,
    get_timezone_geo_button,
    get_time_keyboard,
    get_calendar,
    get_main_menu_keyboard,
    get_repeat_type_keyboard,
    get_weekdays_keyboard,
    get_interval_keyboard,
    get_reminder_actions_inline,
)
from scheduler import create_scheduler

logging.basicConfig(level=logging.INFO)

# Файл с токеном рядом с main.py: строка вида BOT_TOKEN=123456:ABC...
ENV_FILE = Path(__file__).with_name(".env")
UTC = ZoneInfo("UTC")

dp = Dispatcher(storage=MemoryStorage())
router = Router()
tf = TimezoneFinder()

WEEKDAY_ORDER = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
WEEKDAY_NAMES = {
    'mon': 'Пн', 'tue': 'Вт', 'wed': 'Ср',
    'thu': 'Чт', 'fri': 'Пт', 'sat': 'Сб', 'sun': 'Вс'
}
MONTHS_GENITIVE = [
    'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
]
# Типы повторения. hourly/daily — «каждые N часов/дней», N хранится в repeat_config {"every": N}
REPEAT_TYPES = ('once', 'hourly', 'daily', 'weekly', 'monthly')
REPEAT_ICONS = {'once': '📅', 'hourly': '🔁', 'daily': '🔁', 'weekly': '📆', 'monthly': '📆'}
INTERVAL_LIMITS = {'hourly': 168, 'daily': 365}  # самое большое N: неделя в часах, год в днях
INTERVAL_QUESTIONS = {
    'hourly': "🔁 Каждые сколько часов напоминать?",
    'daily': "🔁 Каждые сколько дней напоминать?",
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
# Кнопки шагов создания напоминания: работают только пока идёт диалог
# ("time_" — кнопки времени из старых версий бота)
FLOW_BUTTON_PREFIXES = ("repeat_", "weekday_", "cal_", "date_", "time_")
NOT_FOUND = "❌ Напоминание не найдено"
TIME_HINT = "Выберите на клавиатуре или введите ЧЧММ (например, 1430)."
# 1430, 930, 14:30, 14.30, 14 30
TIME_RE = re.compile(r"([01]?\d|2[0-3])[:.\s-]?([0-5]\d)")


class ReminderFlow(StatesGroup):
    waiting_for_repeat_type = State()
    waiting_for_text = State()
    waiting_for_interval = State()
    waiting_for_weekdays = State()
    waiting_for_date = State()
    waiting_for_time = State()
    waiting_for_tz_input = State()


# ---------- Вспомогательные функции ----------

def order_weekdays(weekdays) -> list:
    """Дни недели по порядку Пн…Вс (а не по алфавиту английских кодов)."""
    return sorted(weekdays, key=lambda d: WEEKDAY_ORDER.index(d) if d in WEEKDAY_ORDER else len(WEEKDAY_ORDER))


def format_weekdays(weekdays) -> str:
    return ', '.join(WEEKDAY_NAMES.get(day, day) for day in order_weekdays(weekdays))


def format_date(d: date) -> str:
    """08 ноября 2026"""
    return f"{d.day:02d} {MONTHS_GENITIVE[d.month - 1]} {d.year}"


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


def load_config(raw: str | None) -> dict:
    try:
        config = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return config if isinstance(config, dict) else {}


def describe_repeat(repeat_type: str, config: dict) -> str:
    """Строка про повторение: «🔁 Каждые 3 часа», «📆 Каждую неделю: Пн, Ср»…"""
    if repeat_type in ('hourly', 'daily'):
        try:
            n = max(1, int(config.get('every', 1)))
        except (TypeError, ValueError):
            n = 1
        if repeat_type == 'hourly':
            return "🔁 Каждый час" if n == 1 else f"🔁 Каждые {n} {plural(n, 'час', 'часа', 'часов')}"
        return "🔁 Каждый день" if n == 1 else f"🔁 Каждые {n} {plural(n, 'день', 'дня', 'дней')}"
    if repeat_type == 'weekly':
        weekdays = config.get('weekdays')
        return f"📆 Каждую неделю: {format_weekdays(weekdays)}" if weekdays else "📆 Каждую неделю"
    if repeat_type == 'monthly':
        day = config.get('day')
        return f"📆 Каждый месяц, {day} числа" if day else "📆 Каждый месяц"
    return "📅 Однократно"


def describe_content(content_type: str | None, text: str | None) -> str:
    """«📝 текст» или «🖼 Фото: подпись»."""
    text = text or ''
    label = ATTACHMENT_LABELS.get(content_type or 'text')
    if label:
        return f"{label}: {text}" if text else label
    return f"📝 {text}"


def extract_content(message: Message) -> tuple[str, str | None, str] | None:
    """(тип, file_id, текст или подпись) из сообщения пользователя. None — если такое не поддерживаем."""
    if message.text:
        return 'text', None, message.text
    caption = getattr(message, 'caption', None) or ''
    photo = getattr(message, 'photo', None)
    if photo:
        return 'photo', photo[-1].file_id, caption  # последнее — самое большое
    for kind in ATTACHMENT_LABELS:
        attachment = getattr(message, kind, None)
        if kind != 'photo' and attachment:
            return kind, attachment.file_id, caption
    return None


def user_now(user_tz: str | None) -> datetime:
    return datetime.now(ZoneInfo(user_tz) if user_tz else UTC)


def next_occurrence(selected_time: time, tz: ZoneInfo) -> datetime:
    """Ближайший момент в будущем, когда в поясе tz наступит selected_time."""
    now = datetime.now(tz)
    candidate = datetime.combine(now.date(), selected_time).replace(tzinfo=tz)
    if candidate <= now:
        candidate = datetime.combine(now.date() + timedelta(days=1), selected_time).replace(tzinfo=tz)
    return candidate


def next_weekly_occurrence(selected_time: time, weekdays, tz: ZoneInfo) -> datetime:
    """Ближайший выбранный день недели (включая сегодня, если время ещё не прошло)."""
    days = {WEEKDAY_ORDER.index(day) for day in weekdays}
    now = datetime.now(tz)
    for offset in range(8):
        candidate = datetime.combine(now.date() + timedelta(days=offset), selected_time).replace(tzinfo=tz)
        if candidate.weekday() in days and candidate > now:
            return candidate
    raise ValueError("Не выбрано ни одного дня недели")


def parse_time(text: str | None) -> time | None:
    """Время из «1430», «930», «14:30», «14.30», «14 30». None — если это не время."""
    match = TIME_RE.fullmatch((text or "").strip())
    return time(int(match[1]), int(match[2])) if match else None


def parse_id(raw: str) -> int | None:
    return int(raw) if raw.isdigit() else None


async def ask_time(message: Message, state: FSMContext, summary: str, question: str = "Во сколько напоминать?"):
    """Шаг времени: что уже выбрано + вопрос + клавиатура с часами."""
    await message.answer(f"{summary}\n⏰ {question}\n{TIME_HINT}", reply_markup=get_time_keyboard())
    await state.set_state(ReminderFlow.waiting_for_time)


async def get_own_reminder(reminder_id: int | None, chat_id: int) -> dict | None:
    """Напоминание, только если оно принадлежит этому чату. Иначе None.

    callback_data присылает клиент, поэтому id из кнопки нельзя принимать на веру.
    """
    if reminder_id is None:
        return None
    reminder = await get_reminder_by_id(reminder_id)
    if not reminder or reminder['user_id'] != await get_or_create_user(chat_id):
        return None
    return reminder


def format_reminder(index: int, reminder: dict, tz: ZoneInfo) -> str:
    run_at = datetime.fromisoformat(reminder['run_at']).replace(tzinfo=UTC)
    local_time = run_at.astimezone(tz)
    repeat_type = reminder.get('repeat_type') or 'once'

    # Текст пользователя экранируем: сообщение уходит с parse_mode="HTML",
    # и символы вроде "<" или "&" иначе ломают отправку.
    content = html.escape(describe_content(reminder.get('content_type'), reminder.get('text')))
    repeat = html.escape(describe_repeat(repeat_type, load_config(reminder.get('repeat_config'))))
    return (
        f"{REPEAT_ICONS.get(repeat_type, '📅')} <b>Напоминание #{index}</b>\n"
        f"{content}\n"
        f"🕐 {local_time.strftime('%d.%m.%Y %H:%M')}\n"
        f"{repeat}"
    )


async def send_reminders_list(message: Message):
    reminders = await get_user_reminders(message.chat.id)
    if not reminders:
        await message.answer("📭 Нет активных напоминаний.\n\nСоздайте новое!")
        return

    user_tz = await get_user_timezone(message.chat.id)
    tz = ZoneInfo(user_tz) if user_tz else UTC

    await message.answer(f"📋 У вас {len(reminders)} активных напоминаний:\n")

    for i, reminder in enumerate(reminders, 1):
        await message.answer(
            format_reminder(i, reminder, tz),
            parse_mode="HTML",
            reply_markup=get_reminder_actions_inline(reminder['id'], reminder.get('repeat_type', 'once'))
        )


async def start_new_reminder(message: Message, state: FSMContext):
    user_tz = await get_user_timezone(message.chat.id)
    if not user_tz:
        await message.answer("❌ Сначала установите часовой пояс: /timezone")
        return

    await state.set_data({})  # если прошлый диалог бросили на середине — его выбор не нужен
    await message.answer("🔄 Выберите тип повторения:", reply_markup=get_repeat_type_keyboard())
    await state.set_state(ReminderFlow.waiting_for_repeat_type)


async def send_settings(message: Message):
    user_tz = await get_user_timezone(message.chat.id)
    await message.answer(
        f"⚙️ Настройки\n\n🕐 Часовой пояс: {user_tz}\n\nДля изменения используйте /timezone"
    )


# ---------- Часовой пояс ----------

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await get_or_create_user(message.chat.id)
    user_tz = await get_user_timezone(message.chat.id)
    if user_tz:
        await message.answer(
            "✅ Часовой пояс уже установлен.\n\nВыберите действие:",
            reply_markup=get_main_menu_keyboard()
        )
    else:
        await message.answer(
            "👋 Привет! Чтобы напоминания приходили вовремя, укажите ваш часовой пояс.",
            reply_markup=get_timezone_geo_or_manual()
        )


@router.callback_query(F.data == "tz_geo")
async def tz_geo_request(callback: CallbackQuery):
    """Запрос геолокации через ReplyKeyboard (кнопку геолокации можно показать только новым сообщением)"""
    await callback.message.answer(
        "📍 Нажмите кнопку ниже, чтобы отправить геолокацию:",
        reply_markup=get_timezone_geo_button()
    )
    await callback.answer()


@router.message(lambda msg: msg.location is not None)
async def handle_location(message: Message, state: FSMContext):
    lat, lon = message.location.latitude, message.location.longitude
    tz_name = tf.timezone_at(lat=lat, lng=lon)

    if tz_name:
        await get_or_create_user(message.chat.id)
        await set_user_timezone(message.chat.id, tz_name)
        await message.answer(
            f"🕗 Ваш часовой пояс: {tz_name}\n\nВыберите действие:",
            reply_markup=get_main_menu_keyboard()
        )
    else:
        await message.answer(
            "❌ Не удалось определить часовой пояс. Пожалуйста, выберите другой способ.",
            reply_markup=ReplyKeyboardRemove()
        )
        await message.answer("Выберите способ:", reply_markup=get_timezone_geo_or_manual())


@router.callback_query(F.data == "tz_manual")
async def tz_manual(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "Введите название часового пояса (например, Europe/Moscow, Asia/Tokyo, America/New_York):")
    await state.set_state(ReminderFlow.waiting_for_tz_input)
    await callback.answer()


@router.message(ReminderFlow.waiting_for_tz_input)
async def handle_tz_input(message: Message, state: FSMContext):
    tz_examples = "Примеры:\n• Europe/Moscow\n• Asia/Tokyo\n• America/New_York"
    if not message.text:
        await message.answer(f"✏️ Отправьте название часового пояса текстом.\n\n{tz_examples}")
        return

    tz_input = message.text.strip()
    try:
        ZoneInfo(tz_input)
    except Exception:
        await message.answer(f"❌ Неверный часовой пояс. Попробуйте ещё раз.\n\n{tz_examples}")
        return

    await get_or_create_user(message.chat.id)
    await set_user_timezone(message.chat.id, tz_input)
    await message.answer(
        f"🕗 Часовой пояс установлен: {tz_input}\n\nВыберите действие:",
        reply_markup=get_main_menu_keyboard()
    )
    await state.clear()


@router.message(Command("timezone"))
async def cmd_timezone(message: Message):
    current_tz = await get_user_timezone(message.chat.id)
    if current_tz:
        await message.answer(
            f"Ваш текущий часовой пояс: {current_tz}\n\nВыберите способ изменения:",
            reply_markup=get_timezone_geo_or_manual()
        )
    else:
        await message.answer("Выберите способ установки часового пояса:", reply_markup=get_timezone_geo_or_manual())


# ---------- Главное меню ----------

# Обработчик текстовой кнопки "➕ Новое напоминание"
@router.message(F.text == "➕ Новое напоминание")
async def new_reminder_text(message: Message, state: FSMContext):
    await start_new_reminder(message, state)


# Обработчик текстовой кнопки "📋 Мои напоминания"
@router.message(F.text == "📋 Мои напоминания")
async def my_reminders_text(message: Message):
    await send_reminders_list(message)


# Обработчик текстовой кнопки "⚙️ Настройки"
@router.message(F.text == "⚙️ Настройки")
async def settings_text(message: Message):
    await send_settings(message)


@router.callback_query(F.data == "new_reminder")
async def new_reminder_callback(callback: CallbackQuery, state: FSMContext):
    await start_new_reminder(callback.message, state)
    await callback.answer()


@router.message(Command("remind"))
async def cmd_remind(message: Message, state: FSMContext):
    await start_new_reminder(message, state)


# ---------- Создание напоминания ----------
# Тип повторения → текст или вложение → дата / число N / дни недели → время → «✅ Напоминание установлено».
# Все сообщения диалога остаются в чате.

# Тип можно выбрать и передумать, пока бот ждёт текст
@router.callback_query(
    StateFilter(ReminderFlow.waiting_for_repeat_type, ReminderFlow.waiting_for_text),
    F.data.startswith("repeat_"),
)
async def repeat_type_selected(callback: CallbackQuery, state: FSMContext):
    repeat_type = callback.data.removeprefix("repeat_")
    if repeat_type not in REPEAT_TYPES:
        await callback.answer()
        return

    await state.update_data(repeat_type=repeat_type)
    await callback.message.answer(
        "📝 Отправьте текст или вложение для напоминания:",
        reply_markup=ReplyKeyboardRemove()
    )
    await state.set_state(ReminderFlow.waiting_for_text)
    await callback.answer()


@router.message(ReminderFlow.waiting_for_text)
async def remind_content(message: Message, state: FSMContext):
    content = extract_content(message)
    if content is None:
        await message.answer("✏️ Отправьте текст, фото, видео, файл, голосовое или стикер.")
        return

    content_type, file_id, text = content
    await state.update_data(content_type=content_type, file_id=file_id, text=text)
    repeat_type = (await state.get_data()).get('repeat_type', 'once')

    if repeat_type in ('once', 'monthly'):
        # Сегодняшняя дата — пользователя, а не сервера
        today = user_now(await get_user_timezone(message.chat.id)).date()
        prompt = ("📅 Выберите дату:" if repeat_type == 'once' else
                  "📆 Выберите дату первого напоминания — дальше оно будет повторяться в это число каждый месяц:")
        await message.answer(prompt, reply_markup=get_calendar(today.year, today.month, message.chat.id, today=today))
        await state.set_state(ReminderFlow.waiting_for_date)
    elif repeat_type == 'weekly':
        await message.answer("📆 Выберите дни недели для повторения:", reply_markup=get_weekdays_keyboard())
        await state.set_state(ReminderFlow.waiting_for_weekdays)
    else:  # hourly / daily
        await message.answer(
            f"{INTERVAL_QUESTIONS[repeat_type]}\nВыберите на клавиатуре или введите своё число.",
            reply_markup=get_interval_keyboard(repeat_type)
        )
        await state.set_state(ReminderFlow.waiting_for_interval)


@router.message(ReminderFlow.waiting_for_interval)
async def interval_input(message: Message, state: FSMContext):
    repeat_type = (await state.get_data()).get('repeat_type', 'daily')
    limit = INTERVAL_LIMITS.get(repeat_type, 365)
    raw = (message.text or "").strip()
    n = int(raw) if raw.isdigit() else 0
    if not 1 <= n <= limit:
        await message.answer(f"❌ Нужно целое число от 1 до {limit}. Выберите на клавиатуре или введите число.")
        return

    await state.update_data(interval=n)
    question = "Во сколько начать?" if repeat_type == 'hourly' else "Во сколько напоминать?"
    await ask_time(message, state, describe_repeat(repeat_type, {'every': n}), question)


@router.callback_query(ReminderFlow.waiting_for_weekdays, F.data.startswith("weekday_"))
async def weekday_toggle(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected_weekdays = data.get('selected_weekdays', set())

    if callback.data == "weekday_done":
        if not selected_weekdays:
            await callback.answer("❌ Выберите хотя бы один день!", show_alert=True)
            return

        await state.update_data(selected_weekdays=selected_weekdays)
        await ask_time(callback.message, state,
                       describe_repeat('weekly', {'weekdays': order_weekdays(selected_weekdays)}))
        await callback.answer()
        return

    weekday = callback.data.split("_", 1)[1]

    if weekday in selected_weekdays:
        selected_weekdays.remove(weekday)
    else:
        selected_weekdays.add(weekday)

    await state.update_data(selected_weekdays=selected_weekdays)
    await callback.message.edit_reply_markup(
        reply_markup=get_weekdays_keyboard(selected_weekdays)
    )
    await callback.answer()


@router.callback_query(ReminderFlow.waiting_for_date, F.data.startswith("cal_"))
async def calendar_nav(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    if len(parts) != 4:
        await callback.answer()
        return
    _, chat_id, year, month = parts
    if chat_id != str(callback.message.chat.id):
        await callback.answer("Это не ваш календарь!", show_alert=True)
        return
    today = user_now(await get_user_timezone(callback.message.chat.id)).date()
    await callback.message.edit_reply_markup(
        reply_markup=get_calendar(int(year), int(month), chat_id, today=today)
    )
    await callback.answer()


@router.callback_query(ReminderFlow.waiting_for_date, F.data.startswith("date_"))
async def date_selected(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    if len(parts) != 5:
        await callback.answer()
        return
    _, chat_id, year, month, day = parts
    if chat_id != str(callback.message.chat.id):
        await callback.answer("Это не ваш календарь!", show_alert=True)
        return
    selected_date = date(int(year), int(month), int(day))
    today = user_now(await get_user_timezone(callback.message.chat.id)).date()
    if selected_date < today:
        await callback.answer("❌ Эта дата уже прошла, выберите другую", show_alert=True)
        return

    await state.update_data(selected_date=selected_date)
    repeat_type = (await state.get_data()).get('repeat_type', 'once')
    if repeat_type == 'monthly':
        summary = f"📆 Каждый месяц, начиная с {format_date(selected_date)}"
    else:
        summary = f"📅 Однократно, {format_date(selected_date)}"
    await ask_time(callback.message, state, summary)
    await callback.answer()


# Кнопка шага создания нажата вне диалога (старое сообщение или бот перезапускался)
@router.callback_query(F.data.startswith(FLOW_BUTTON_PREFIXES))
async def stale_flow_button(callback: CallbackQuery):
    await callback.answer("⌛ Эта кнопка устарела. Начните заново: /remind", show_alert=True)


@router.message(ReminderFlow.waiting_for_time)
async def time_input(message: Message, state: FSMContext):
    """Время с клавиатуры (09:00) или набранное вручную (1430, 930, 14:30)."""
    selected_time = parse_time(message.text)
    if selected_time is None:
        await message.answer(f"❌ Не понял время. {TIME_HINT}")
        return
    await state.update_data(selected_time=selected_time)
    await finalize_reminder(message, state)


async def finalize_reminder(message: Message, state: FSMContext):
    data = await state.get_data()
    user_tz = await get_user_timezone(message.chat.id)
    user_id = await get_or_create_user(message.chat.id)
    tz = ZoneInfo(user_tz)

    repeat_type = data.get('repeat_type', 'once')
    selected_time = data['selected_time']
    text = data.get('text', '')
    content_type = data.get('content_type', 'text')
    config = {}

    if repeat_type in ('once', 'monthly'):
        run_at = datetime.combine(data['selected_date'], selected_time).replace(tzinfo=tz)
        if run_at <= datetime.now(tz):
            # Остаёмся на шаге времени, а не бросаем весь диалог
            await message.answer("❌ Это время уже прошло. Выберите другое время или введите ЧЧММ.")
            return
        if repeat_type == 'monthly':
            config = {'day': data['selected_date'].day}
    elif repeat_type in ('hourly', 'daily'):
        # Первое срабатывание — в выбранное время (сегодня или, если уже прошло, завтра)
        run_at = next_occurrence(selected_time, tz)
        config = {'every': int(data.get('interval', 1))}
    else:  # weekly
        weekdays = order_weekdays(data.get('selected_weekdays', set()))
        run_at = next_weekly_occurrence(selected_time, weekdays, tz)
        config = {'weekdays': weekdays}

    await create_reminder(
        user_id, run_at.astimezone(UTC), text,
        repeat_type=repeat_type,
        repeat_config=json.dumps(config) if config else None,
        content_type=content_type,
        file_id=data.get('file_id'),
    )

    when = f"{format_date(run_at)} в {run_at:%H:%M}"
    await message.answer(
        "✅ Напоминание установлено\n"
        f"{describe_content(content_type, text)}\n"
        f"{describe_repeat(repeat_type, config)}\n"
        f"⏰ {when if repeat_type == 'once' else 'Первое — ' + when}",
        reply_markup=get_main_menu_keyboard()
    )
    await state.clear()


# ---------- Список напоминаний ----------

@router.callback_query(F.data == "my_reminders")
async def my_reminders_callback(callback: CallbackQuery):
    await send_reminders_list(callback.message)
    await callback.answer()


@router.message(Command("list"))
async def cmd_list(message: Message):
    await send_reminders_list(message)


# ---------- Кнопки у напоминаний ----------

# Обработчик отмены одного напоминания (из списка)
@router.callback_query(F.data.startswith("cancel_reminder_"))
async def cancel_single_reminder(callback: CallbackQuery):
    reminder_id = parse_id(callback.data.removeprefix("cancel_reminder_"))
    if not await get_own_reminder(reminder_id, callback.message.chat.id):
        await callback.answer(NOT_FOUND, show_alert=True)
        return

    await update_reminder_status(reminder_id, 'canceled')

    await callback.message.edit_text(
        "❌ Напоминание отменено.",
        reply_markup=None
    )
    await callback.answer("✅ Напоминание отменено")


# Обработчик отмены серии напоминаний
@router.callback_query(F.data.startswith("cancel_series_"))
async def cancel_series_reminder(callback: CallbackQuery):
    reminder_id = parse_id(callback.data.removeprefix("cancel_series_"))
    if not await get_own_reminder(reminder_id, callback.message.chat.id):
        await callback.answer(NOT_FOUND, show_alert=True)
        return

    await update_reminder_status(reminder_id, 'canceled')

    await callback.message.edit_text(
        "❌ Серия напоминаний отменена.",
        reply_markup=None
    )
    await callback.answer("✅ Серия отменена")


# Обработчик отложения напоминания
@router.callback_query(F.data.startswith("snooze_"))
async def snooze_reminder(callback: CallbackQuery):
    parts = callback.data.split("_")
    if len(parts) != 3:
        await callback.answer()
        return
    _, raw_id, raw_minutes = parts
    reminder_id = parse_id(raw_id)
    minutes = parse_id(raw_minutes)

    reminder = await get_own_reminder(reminder_id, callback.message.chat.id)
    if not reminder or minutes is None:
        await callback.answer(NOT_FOUND, show_alert=True)
        return

    new_time = datetime.now(timezone.utc) + timedelta(minutes=minutes)

    # Создаем новое одноразовое напоминание (отложенное)
    await create_reminder(reminder['user_id'], new_time, reminder['text'], repeat_type='once',
                          content_type=reminder.get('content_type') or 'text', file_id=reminder.get('file_id'))

    # Для циклических напоминаний НЕ отменяем основное напоминание
    # Оно продолжит работать по расписанию

    await callback.message.edit_text(f"⏰ Напоминание отложено на {minutes} минут.")
    await callback.answer(f"✅ Отложено на {minutes} мин")


# Обработчик кнопки "Готово"
@router.callback_query(F.data.startswith("done_"))
async def done_reminder(callback: CallbackQuery):
    reminder_id = parse_id(callback.data.removeprefix("done_"))
    reminder = await get_own_reminder(reminder_id, callback.message.chat.id)
    if not reminder:
        await callback.answer(NOT_FOUND, show_alert=True)
        return

    repeat_type = reminder.get('repeat_type', 'once')

    # Для однократных напоминаний - помечаем как выполненное
    if repeat_type == 'once':
        await update_reminder_status(reminder_id, 'sent')
        await callback.message.edit_text("✅ Готово! Напоминание выполнено.")
    else:
        # Для циклических - просто убираем сообщение, напоминание продолжит работать
        await callback.message.edit_text("✅ Готово! Напоминание продолжит работать по расписанию.")

    await callback.answer("✅ Готово")


# Старый обработчик cancel_ (для совместимости)
@router.callback_query(
    F.data.startswith("cancel_"),
    ~F.data.startswith("cancel_series_"),
    ~F.data.startswith("cancel_reminder_"),
)
async def cancel_reminder(callback: CallbackQuery):
    reminder_id = parse_id(callback.data.removeprefix("cancel_"))
    if not await get_own_reminder(reminder_id, callback.message.chat.id):
        await callback.answer(NOT_FOUND, show_alert=True)
        return

    await update_reminder_status(reminder_id, 'canceled')
    await callback.message.edit_text("❌ Напоминание отменено.")
    await callback.answer()


@router.callback_query(F.data == "settings")
async def settings_callback(callback: CallbackQuery):
    await send_settings(callback.message)
    await callback.answer()


@router.callback_query(F.data == "ignore")
async def ignore_callback(callback: CallbackQuery):
    await callback.answer()


dp.include_router(router)


def load_token() -> str | None:
    """Токен из переменной окружения BOT_TOKEN, а если её нет — из файла .env рядом с ботом."""
    token = os.getenv("BOT_TOKEN", "").strip()
    if token:
        return token
    try:
        # utf-8-sig: Блокнот Windows может сохранить файл с BOM
        lines = ENV_FILE.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return None
    for line in lines:
        key, sep, value = line.partition("=")
        if sep and key.strip() == "BOT_TOKEN":
            return value.strip().strip('"').strip("'") or None
    return None


async def main():
    # Токен не храним в коде
    token = load_token()
    if not token:
        raise SystemExit(
            "❌ Не задан токен бота.\n"
            f"Создайте файл {ENV_FILE} со строкой:\n"
            "    BOT_TOKEN=123456:ABC...\n"
            "или задайте переменную окружения BOT_TOKEN."
        )
    try:
        bot = Bot(token=token)
    except TokenValidationError:
        raise SystemExit(
            "❌ Токен бота указан неверно.\n"
            f"Проверьте строку BOT_TOKEN в {ENV_FILE}: токен от @BotFather выглядит как 123456789:AAH..."
        )

    await init_db()
    scheduler = create_scheduler(bot)
    scheduler.start()
    try:
        await dp.start_polling(bot)
    except TelegramUnauthorizedError:
        raise SystemExit(
            "❌ Telegram не принял токен. Проверьте BOT_TOKEN — возможно, токен отозван "
            "или скопирован не полностью. Новый можно получить у @BotFather."
        )
    finally:
        scheduler.shutdown()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
