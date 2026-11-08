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
    BTN_CANCEL,
    get_timezone_geo_or_manual,
    get_timezone_geo_button,
    get_time_keyboard,
    get_calendar,
    get_main_menu_keyboard,
    get_repeat_type_keyboard,
    get_weekdays_keyboard,
    get_interval_keyboard,
    get_cancel_keyboard,
    get_month_mode_keyboard,
    get_month_days_keyboard,
    get_start_now_keyboard,
    get_reminder_actions_inline,
)
from scheduler import create_scheduler, next_monthly_run
from texts import (
    ATTACHMENT_LABELS,
    REPEAT_LABELS,
    WEEKDAY_ORDER,
    describe_content,
    describe_repeat,
    format_date,
    format_duration,
    month_note,
    order_weekdays,
)

logging.basicConfig(level=logging.INFO)

# Файл с токеном рядом с main.py: строка вида BOT_TOKEN=123456:ABC...
ENV_FILE = Path(__file__).with_name(".env")
UTC = ZoneInfo("UTC")

dp = Dispatcher(storage=MemoryStorage())
router = Router()
tf = TimezoneFinder()

# Типы повторения. hourly/daily — «каждые N часов/дней», N хранится в repeat_config {"every": N}
REPEAT_TYPES = tuple(REPEAT_LABELS)
REPEAT_ICONS = {'once': '🗓', 'hourly': '🔁', 'daily': '🔁', 'weekly': '📆', 'monthly': '📆'}
# Самое большое N: неделя в часах, год в днях, за 27 дней до конца (в феврале остаётся 1-е число)
NUMBER_LIMITS = {'hourly': 168, 'daily': 365, 'before_end': 27}
# Кнопки шагов создания напоминания: работают только пока идёт диалог
# ("time_" — кнопки времени из старых версий бота)
FLOW_BUTTON_PREFIXES = ("repeat_", "weekday_", "cal_", "date_", "time_", "monthmode_", "mday_", "start_now")
NOT_FOUND = "❌ Напоминание не найдено"
TIME_EXAMPLE = "(например: 1015)"
TIME_STEP = f"Во сколько напоминать?\nВыберите время ниже или введите вручную {TIME_EXAMPLE}."
CHOOSE_DAYS = "Можно выбрать несколько дней.\nКогда закончите — нажмите «Подтвердить выбор»."
MONTH_DAYS_STEP = (
    f"Выберите дни месяца.\n{CHOOSE_DAYS}\n\n"
    "ℹ️ Если выбранный день отсутствует в месяце (например, 29 или 30), "
    "напоминание придёт в последний день этого месяца."
)
BUTTONS_HINT = "👆 Выберите вариант кнопками в сообщении выше или нажмите «❌ Отмена»."
# 1430, 930, 14:30, 14.30, 14 30
TIME_RE = re.compile(r"([01]?\d|2[0-3])[:.\s-]?([0-5]\d)")


class ReminderFlow(StatesGroup):
    waiting_for_repeat_type = State()
    waiting_for_text = State()
    waiting_for_interval = State()      # N для «каждые N часов/дней»
    waiting_for_date = State()          # календарь: однократно / дата начала «каждые N дней»
    waiting_for_weekdays = State()
    waiting_for_month_mode = State()
    waiting_for_month_days = State()
    waiting_for_month_offset = State()  # N для «за N дней до конца месяца»
    waiting_for_time = State()
    waiting_for_tz_input = State()


# Шаги, где ответ — кнопки в сообщении (печатать текст бесполезно)
BUTTON_STEPS = (
    ReminderFlow.waiting_for_repeat_type,
    ReminderFlow.waiting_for_date,
    ReminderFlow.waiting_for_weekdays,
    ReminderFlow.waiting_for_month_mode,
    ReminderFlow.waiting_for_month_days,
)


# ---------- Вспомогательные функции ----------

def load_config(raw: str | None) -> dict:
    try:
        config = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return config if isinstance(config, dict) else {}


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


def parse_number(text: str | None, limit: int) -> int | None:
    raw = (text or "").strip()
    n = int(raw) if raw.isdigit() else 0
    return n if 1 <= n <= limit else None


def parse_id(raw: str) -> int | None:
    return int(raw) if raw.isdigit() else None


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


async def set_status_text(message: Message, status: str):
    """Пометить сработавшее напоминание («Готово», «Отложено»…) и убрать его кнопки.
    У фото/файла нет текста — у них меняем подпись."""
    if message.text is not None:
        await message.edit_text(status, reply_markup=None)
    else:
        caption = f"{message.caption}\n\n{status}" if message.caption else status
        await message.edit_caption(caption=caption[:1024], reply_markup=None)


def format_reminder(index: int, reminder: dict, tz: ZoneInfo) -> str:
    run_at = datetime.fromisoformat(reminder['run_at']).replace(tzinfo=UTC)
    local_time = run_at.astimezone(tz)
    repeat_type = reminder.get('repeat_type') or 'once'
    content_type = reminder.get('content_type') or 'text'

    # Текст пользователя экранируем: сообщение уходит с parse_mode="HTML",
    # и символы вроде "<" или "&" иначе ломают отправку.
    content = describe_content(content_type, reminder.get('text'))
    if content_type == 'text':
        content = f"📝 {content}"
    repeat = describe_repeat(repeat_type, load_config(reminder.get('repeat_config')))
    return (
        f"{REPEAT_ICONS.get(repeat_type, '🗓')} <b>Напоминание #{index}</b>\n"
        f"{html.escape(content)}\n"
        f"🕐 {local_time.strftime('%d.%m.%Y %H:%M')}\n"
        f"{html.escape(repeat)}"
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


async def send_settings(message: Message):
    user_tz = await get_user_timezone(message.chat.id)
    await message.answer(
        f"⚙️ Настройки\n\n🕐 Часовой пояс: {user_tz}\n\nДля изменения используйте /timezone"
    )


# ---------- Шаги диалога ----------
# Каждый шаг: «[уже выбранное правило]» → «что сделать сейчас». «❌ Отмена» всегда в нижней клавиатуре.

async def show_step(message: Message, state: FSMContext, rule: str, instruction: str, *,
                    inline=None, keyboard: tuple[str, object] | None = None):
    """Отправить шаг диалога.

    keyboard — нижняя клавиатура шага: (имя, разметка), по умолчанию только «❌ Отмена».
    В одном сообщении Telegram — либо кнопки в сообщении (inline), либо нижняя клавиатура.
    Если шагу нужны обе, а нижняя клавиатура меняется, правило уходит отдельным сообщением
    с новой клавиатурой, а задание — следом, с кнопками.
    """
    name, markup = keyboard or ('cancel', get_cancel_keyboard())
    text = f"{rule}\n\n{instruction}" if rule else instruction
    if inline is None:
        await message.answer(text, reply_markup=markup)
    elif (await state.get_data()).get('keyboard') == name:
        await message.answer(text, reply_markup=inline)
    else:
        await message.answer(rule, reply_markup=markup)
        await message.answer(instruction, reply_markup=inline)
    await state.update_data(keyboard=name)


async def ask_time(message: Message, state: FSMContext, rule: str, instruction: str = TIME_STEP, inline=None):
    await show_step(message, state, rule, instruction, inline=inline, keyboard=('time', get_time_keyboard()))
    await state.set_state(ReminderFlow.waiting_for_time)


async def ask_calendar(message: Message, state: FSMContext, rule: str, instruction: str):
    # Сегодняшняя дата — пользователя, а не сервера
    today = user_now(await get_user_timezone(message.chat.id)).date()
    await show_step(message, state, rule, instruction,
                    inline=get_calendar(today.year, today.month, message.chat.id, today=today))
    await state.set_state(ReminderFlow.waiting_for_date)


async def start_new_reminder(message: Message, state: FSMContext, content: tuple | None = None):
    """Начать создание напоминания. content — если текст уже прислали (раздел 7 ТЗ)."""
    user_tz = await get_user_timezone(message.chat.id)
    if not user_tz:
        await message.answer("❌ Сначала установите часовой пояс: /timezone")
        return

    await state.set_data({})  # если прошлый диалог бросили на середине — его выбор не нужен
    rule = "🆕 Новое напоминание"
    if content:
        content_type, file_id, text = content
        await state.update_data(content_type=content_type, file_id=file_id, text=text)
        rule = describe_content(content_type, text)
    await show_step(message, state, rule, "Выберите тип повторения:", inline=get_repeat_type_keyboard())
    await state.set_state(ReminderFlow.waiting_for_repeat_type)


async def ask_details(message: Message, state: FSMContext):
    """Шаг после текста — свой для каждого типа повторения."""
    repeat_type = (await state.get_data()).get('repeat_type', 'once')
    label = REPEAT_LABELS[repeat_type]

    if repeat_type == 'once':
        await ask_calendar(message, state, label, "Выберите дату:")
    elif repeat_type in ('hourly', 'daily'):
        unit = 'часов' if repeat_type == 'hourly' else 'дней'
        await show_step(
            message, state, label,
            f"Как часто напоминать?\nВыберите количество {unit} из предложенных ниже или введите своё число.",
            keyboard=(repeat_type, get_interval_keyboard(repeat_type)),
        )
        await state.set_state(ReminderFlow.waiting_for_interval)
    elif repeat_type == 'weekly':
        await show_step(message, state, label, f"Выберите дни недели.\n{CHOOSE_DAYS}", inline=get_weekdays_keyboard())
        await state.set_state(ReminderFlow.waiting_for_weekdays)
    else:  # monthly
        await show_step(message, state, label, "Как напоминать каждый месяц?", inline=get_month_mode_keyboard())
        await state.set_state(ReminderFlow.waiting_for_month_mode)


# ---------- Отмена (работает на любом шаге) ----------

@router.message(F.text == BTN_CANCEL)
async def cancel_flow(message: Message, state: FSMContext):
    in_flow = await state.get_state() is not None
    await state.clear()
    await message.answer(
        "❌ Создание напоминания отменено." if in_flow else "Выберите действие:",
        reply_markup=get_main_menu_keyboard()
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
# Тип → текст или вложение → параметры типа → время → «✅ Напоминание установлено».

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
    await callback.answer()
    if 'content_type' in await state.get_data():
        await ask_details(callback.message, state)  # текст уже прислали — сразу дальше
    else:
        await show_step(callback.message, state, REPEAT_LABELS[repeat_type],
                        "Отправьте текст или вложение для напоминания:")
        await state.set_state(ReminderFlow.waiting_for_text)


@router.message(ReminderFlow.waiting_for_text)
async def remind_content(message: Message, state: FSMContext):
    content = extract_content(message)
    if content is None:
        await message.answer("✏️ Отправьте текст, фото, видео, файл, голосовое или стикер.")
        return

    content_type, file_id, text = content
    await state.update_data(content_type=content_type, file_id=file_id, text=text)
    await ask_details(message, state)


@router.message(ReminderFlow.waiting_for_interval)
async def interval_input(message: Message, state: FSMContext):
    repeat_type = (await state.get_data()).get('repeat_type', 'daily')
    limit = NUMBER_LIMITS[repeat_type]
    n = parse_number(message.text, limit)
    if n is None:
        await message.answer(f"❌ Нужно целое число от 1 до {limit}. Выберите число ниже или введите своё.")
        return

    await state.update_data(interval=n)
    rule = describe_repeat(repeat_type, {'every': n})
    if repeat_type == 'hourly':
        await ask_time(
            message, state, rule,
            "От какого момента начать отсчёт?\nМожно начать с текущего момента, выбрать время ниже "
            f"или ввести время вручную {TIME_EXAMPLE}.",
            inline=get_start_now_keyboard(),
        )
    else:
        await ask_calendar(message, state, rule, "Выберите дату начала:")


@router.callback_query(ReminderFlow.waiting_for_weekdays, F.data.startswith("weekday_"))
async def weekday_toggle(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected_weekdays = data.get('selected_weekdays', set())

    if callback.data == "weekday_done":
        if not selected_weekdays:
            await callback.answer("❌ Выберите хотя бы один день!", show_alert=True)
            return

        await state.update_data(selected_weekdays=selected_weekdays)
        await callback.answer()
        await ask_time(callback.message, state,
                       describe_repeat('weekly', {'weekdays': order_weekdays(selected_weekdays)}))
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


@router.callback_query(ReminderFlow.waiting_for_month_mode, F.data.startswith("monthmode_"))
async def month_mode_selected(callback: CallbackQuery, state: FSMContext):
    mode = callback.data.removeprefix("monthmode_")
    await callback.answer()
    if mode == 'days':
        await show_step(callback.message, state, REPEAT_LABELS['monthly'], MONTH_DAYS_STEP,
                        inline=get_month_days_keyboard())
        await state.set_state(ReminderFlow.waiting_for_month_days)
    elif mode == 'last':
        config = {'last': True}
        await state.update_data(month_config=config)
        await ask_time(callback.message, state, describe_repeat('monthly', config))
    elif mode == 'before':
        await show_step(
            callback.message, state, REPEAT_LABELS['monthly'],
            "За сколько дней до конца месяца напоминать?\nВыберите число ниже или введите своё.",
            keyboard=('before_end', get_interval_keyboard('before_end')),
        )
        await state.set_state(ReminderFlow.waiting_for_month_offset)


@router.callback_query(ReminderFlow.waiting_for_month_days, F.data.startswith("mday_"))
async def month_day_toggle(callback: CallbackQuery, state: FSMContext):
    selected = set((await state.get_data()).get('month_days', set()))

    if callback.data == "mday_done":
        if not selected:
            await callback.answer("❌ Выберите хотя бы один день!", show_alert=True)
            return
        config = {'days': sorted(selected)}
        await state.update_data(month_config=config)
        await callback.answer()
        await ask_time(callback.message, state, describe_repeat('monthly', config))
        return

    day = parse_id(callback.data.removeprefix("mday_"))
    if day is None or not 1 <= day <= 31:
        await callback.answer()
        return
    selected ^= {day}
    await state.update_data(month_days=selected)
    await callback.message.edit_reply_markup(reply_markup=get_month_days_keyboard(selected))
    await callback.answer()


@router.message(ReminderFlow.waiting_for_month_offset)
async def month_offset_input(message: Message, state: FSMContext):
    limit = NUMBER_LIMITS['before_end']
    n = parse_number(message.text, limit)
    if n is None:
        await message.answer(f"❌ Нужно целое число от 1 до {limit}. Выберите число ниже или введите своё.")
        return
    config = {'before_end': n}
    await state.update_data(month_config=config)
    await ask_time(message, state, describe_repeat('monthly', config))


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
    await callback.answer()
    data = await state.get_data()
    if data.get('repeat_type') == 'daily':
        rule = describe_repeat('daily', {'every': data.get('interval', 1), 'start': selected_date.isoformat()})
    else:
        rule = f"{REPEAT_LABELS['once']}, {format_date(selected_date)}"
    await ask_time(callback.message, state, rule)


@router.callback_query(ReminderFlow.waiting_for_time, F.data == "start_now")
async def start_now_selected(callback: CallbackQuery, state: FSMContext):
    """«📍 С текущего момента» для «Каждые N часов»."""
    await callback.answer()
    if (await state.get_data()).get('repeat_type') != 'hourly':
        return
    await finalize_reminder(callback.message, state, start_now=True)


# Кнопка шага создания нажата вне диалога (старое сообщение или бот перезапускался)
@router.callback_query(F.data.startswith(FLOW_BUTTON_PREFIXES))
async def stale_flow_button(callback: CallbackQuery):
    await callback.answer("⌛ Эта кнопка устарела. Начните заново: /remind", show_alert=True)


@router.message(StateFilter(*BUTTON_STEPS))
async def typed_on_button_step(message: Message):
    await message.answer(BUTTONS_HINT)


@router.message(ReminderFlow.waiting_for_time)
async def time_input(message: Message, state: FSMContext):
    """Время с клавиатуры (09:00) или набранное вручную (1015, 930, 14:30)."""
    selected_time = parse_time(message.text)
    if selected_time is None:
        await message.answer(f"❌ Не понял время. Выберите время ниже или введите вручную {TIME_EXAMPLE}.")
        return
    await state.update_data(selected_time=selected_time)
    await finalize_reminder(message, state)


async def finalize_reminder(message: Message, state: FSMContext, start_now: bool = False):
    data = await state.get_data()
    user_tz = await get_user_timezone(message.chat.id)
    user_id = await get_or_create_user(message.chat.id)
    tz = ZoneInfo(user_tz)
    now = datetime.now(tz)

    repeat_type = data.get('repeat_type', 'once')
    selected_time = data.get('selected_time')
    text = data.get('text', '')
    content_type = data.get('content_type', 'text')
    config = {}

    if repeat_type == 'hourly':
        # Отсчёт от выбранного момента: T, T+N, T+2N… — берём ближайший в будущем
        n = int(data.get('interval', 1))
        step = timedelta(hours=n)
        config = {'every': n}
        if start_now:
            run_at = now.replace(second=0, microsecond=0) + step
        else:
            run_at = datetime.combine(now.date(), selected_time).replace(tzinfo=tz)
            if run_at <= now:
                run_at += step * ((now - run_at) // step + 1)
    elif repeat_type == 'daily':
        n = int(data.get('interval', 1))
        start = data.get('selected_date')
        config = {'every': n}
        if start:
            config['start'] = start.isoformat()
        day = start or now.date()
        while datetime.combine(day, selected_time).replace(tzinfo=tz) <= now:
            day += timedelta(days=n)  # дата начала сегодня, а время прошло — следующий раз через N дней
        run_at = datetime.combine(day, selected_time).replace(tzinfo=tz)
    elif repeat_type == 'weekly':
        weekdays = order_weekdays(data.get('selected_weekdays', set()))
        run_at = next_weekly_occurrence(selected_time, weekdays, tz)
        config = {'weekdays': weekdays}
    elif repeat_type == 'monthly':
        config = data['month_config']
        run_at = next_monthly_run(config, tz, selected_time, now).astimezone(tz)
    else:  # once
        run_at = datetime.combine(data['selected_date'], selected_time).replace(tzinfo=tz)
        if run_at <= now:
            # Остаёмся на шаге времени, а не бросаем весь диалог
            await message.answer(f"❌ Это время уже прошло. Выберите другое время ниже или введите вручную {TIME_EXAMPLE}.")
            return

    await create_reminder(
        user_id, run_at.astimezone(UTC), text,
        repeat_type=repeat_type,
        repeat_config=json.dumps(config) if config else None,
        content_type=content_type,
        file_id=data.get('file_id'),
    )

    when = f"{format_date(run_at)} в {run_at:%H:%M}"
    lines = ["✅ Напоминание установлено", "", describe_content(content_type, text), describe_repeat(repeat_type, config)]
    if repeat_type == 'monthly' and month_note(config):
        lines.append(month_note(config))
    lines.append(f"⏰ {when}" if repeat_type == 'once' else f"⏰ Следующее: {when}")
    await message.answer("\n".join(lines), reply_markup=get_main_menu_keyboard())
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

    await set_status_text(callback.message, "❌ Напоминание отменено.")
    await callback.answer("✅ Напоминание отменено")


# Обработчик отмены серии напоминаний
@router.callback_query(F.data.startswith("cancel_series_"))
async def cancel_series_reminder(callback: CallbackQuery):
    reminder_id = parse_id(callback.data.removeprefix("cancel_series_"))
    if not await get_own_reminder(reminder_id, callback.message.chat.id):
        await callback.answer(NOT_FOUND, show_alert=True)
        return

    await update_reminder_status(reminder_id, 'canceled')

    await set_status_text(callback.message, "❌ Серия напоминаний отменена.")
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

    # Создаем новое одноразовое напоминание (отложенное); пометка «отложено» попадёт в его текст
    await create_reminder(reminder['user_id'], new_time, reminder['text'], repeat_type='once',
                          repeat_config=json.dumps({'snoozed': minutes}),
                          content_type=reminder.get('content_type') or 'text', file_id=reminder.get('file_id'))

    # Для циклических напоминаний НЕ отменяем основное напоминание
    # Оно продолжит работать по расписанию

    await set_status_text(callback.message, f"⏰ Напоминание отложено на {format_duration(minutes)}.")
    await callback.answer(f"✅ Отложено на {format_duration(minutes)}")


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
        await set_status_text(callback.message, "✅ Готово! Напоминание выполнено.")
    else:
        # Для циклических - просто убираем кнопки, напоминание продолжит работать
        await set_status_text(callback.message, "✅ Готово! Напоминание продолжит работать по расписанию.")

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
    await set_status_text(callback.message, "❌ Напоминание отменено.")
    await callback.answer()


@router.callback_query(F.data == "settings")
async def settings_callback(callback: CallbackQuery):
    await send_settings(callback.message)
    await callback.answer()


@router.callback_query(F.data == "ignore")
async def ignore_callback(callback: CallbackQuery):
    await callback.answer()


# Текст вне диалога сразу становится текстом нового напоминания (раздел 7 ТЗ).
# Регистрируется последним: кнопки меню, команды и шаги диалога обрабатываются раньше.
@router.message(StateFilter(None), F.text, ~F.text.startswith("/"))
async def text_starts_reminder(message: Message, state: FSMContext):
    await start_new_reminder(message, state, content=('text', None, message.text))


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
