"""Telegram-бот напоминаний. Шаги, тексты и кнопки — как в прошлой рабочей версии бота
(папка remember): текст или вложение → тип повторения → параметры → время → «✅ Напоминание установлено»."""
from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramUnauthorizedError
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, ReplyKeyboardRemove
from aiogram.utils.token import TokenValidationError
from timezonefinderL import TimezoneFinder

from keyboards import (
    BTN_CANCEL,
    BTN_LIST,
    BTN_NEW,
    BTN_SETTINGS,
    BTN_START_NOW,
    CANCEL_DATA,
    get_blocked_reminders_actions_keyboard,
    get_calendar,
    get_cancel_keyboard,
    get_interval_keyboard,
    get_main_menu_keyboard,
    get_month_days_keyboard,
    get_month_mode_keyboard,
    get_reminder_actions_inline,
    get_repeat_type_keyboard,
    get_time_keyboard,
    get_timezone_geo_button,
    get_timezone_geo_or_manual,
    get_timezone_page_keyboard,
    get_weekdays_keyboard,
)
from scheduler import compute_next_run, create_scheduler, next_monthly_run
from storage import (
    count_paused_reminders,
    create_reminder,
    delete_paused_reminders,
    get_admin_stats,
    get_or_create_user,
    get_paused_reminders,
    get_reminder_by_id,
    get_user_reminders,
    get_user_timezone,
    init_db,
    is_user_blocked,
    resume_reminder,
    set_user_blocked,
    set_user_timezone,
    update_reminder_status,
)
from texts import (
    ATTACHMENT_LABELS,
    LIST_ICONS,
    WEEKDAY_ORDER,
    before_end_text,
    describe_repeat,
    display_text,
    every_text,
    format_date,
    format_weekdays,
    list_repeat_text,
    month_note,
    order_weekdays,
    plural,
    rule_text,
)

logging.basicConfig(level=logging.INFO)

# Файл с токеном рядом с main.py: строка вида BOT_TOKEN=123456:ABC...
ENV_FILE = Path(__file__).with_name(".env")
UTC = ZoneInfo("UTC")

dp = Dispatcher(storage=MemoryStorage())
router = Router()
tf = TimezoneFinder()

REPEAT_TYPES = ('once', 'hourly', 'daily', 'weekly', 'monthly')
# Самое большое N: неделя в часах, год в днях, 28 дней до конца месяца (в феврале — 1-е число)
NUMBER_LIMITS = {'hourly': 168, 'daily': 365, 'before_end': 28}
# Кнопки шагов создания напоминания: работают только пока идёт диалог
# ("time_", "start_now", "num_" — кнопки из прошлых версий бота)
FLOW_BUTTON_PREFIXES = ("repeat_", "weekday_", "cal_", "date_", "time_", "monthmode_", "mday_", "start_now", "num_")
NOT_FOUND = "❌ Напоминание не найдено"
CHOOSE_ACTION = "Выберите действие:"
NO_TIMEZONE = "❌ Сначала установите часовой пояс: /timezone"
CANCELED = "🛑 Создание напоминания отменено.\nВыберите действие:"
# Шаги создания — тексты прошлой версии бота
TEXT_STEP = "📝 Отправьте текст или вложение для напоминания:"
TYPE_STEP = "🔄 Выберите тип повторения:"
FORWARD_TYPE_STEP = "↪️ Пересланное сообщение принято. Выберите тип повторения:"
UNSUPPORTED_CONTENT = "❌ Поддерживаются текст, фото, видео, аудио, голосовые, документы и другие вложения."
TIME_STEP = "Во сколько напоминать?\nВыберите время ниже или введите вручную (например: 1015)."
WEEKLY_TIME_STEP = "Укажите время напоминания из предложенного или введите своё время (пример: 1015)."
MONTH_DAYS_STEP = (
    "Выберите дни месяца.\nМожно выбрать несколько дней.\nКогда закончите — нажмите «Подтвердить выбор».\n"
    "ℹ️ Если выбранный день отсутствует в месяце (например, 29 или 30), "
    "напоминание придёт в последний день этого месяца."
)
TIME_ERROR = "❌ Неверный формат времени.\nВведите в формате ЧЧММ (например, 1015) или ЧЧ:ММ (например, 10:15)."
# У «Однократно» и «Каждую неделю» ошибка подробнее — как в прошлой версии
TIME_ERROR_DETAILED = (
    "❌ Неверный формат времени.\nПожалуйста, выберите время из кнопок ниже или введите в формате:\n"
    "• ЧЧММ (например, `1015`)\n• ЧЧ:ММ (например, `10:15`)"
)
TIME_PASSED = "❌ Время уже прошло. Введите другое:"
NO_DAYS = "❌ Выберите хотя бы один день!"
NO_REMINDERS = "📭 Нет активных напоминаний.\nСоздайте новое!"
# 1430, 930, 14:30, 14.30, 14 30
TIME_RE = re.compile(r"([01]?\d|2[0-3])[:.\s-]?([0-5]\d)")
MENU_TEXTS = {BTN_NEW, BTN_LIST, BTN_SETTINGS, BTN_CANCEL}


class ReminderFlow(StatesGroup):
    waiting_for_text = State()
    waiting_for_repeat_type = State()
    waiting_for_interval = State()      # N для «каждые N часов/дней»
    waiting_for_date = State()          # календарь: однократно / дата начала «каждые N дней»
    waiting_for_weekdays = State()
    waiting_for_month_mode = State()
    waiting_for_month_days = State()
    waiting_for_month_offset = State()  # N для «за N дней до конца месяца»
    waiting_for_time = State()


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


def is_forwarded(message: Message) -> bool:
    return any(getattr(message, attr, None) for attr in
               ("forward_origin", "forward_from", "forward_from_chat", "forward_sender_name"))


def user_now(user_tz: str | None) -> datetime:
    return datetime.now(ZoneInfo(user_tz) if user_tz else UTC)


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


def step_text(icon: str, rule: str, instruction: str | None = None) -> str:
    """Сообщение шага, как в прошлой версии: «🔁 <b>Каждые N часов</b>» и с новой строки — что сделать."""
    text = f"{icon} <b>{html.escape(rule)}</b>"
    return f"{text}\n{instruction}" if instruction else text


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


async def delete_message(message: Message):
    """Убрать сообщение шага с кнопками, когда выбор сделан. Если уже не удалить — не страшно."""
    try:
        await message.delete()
    except TelegramAPIError as e:
        logging.debug("Не удалось удалить сообщение: %s", e)


def mark_done(text: str | None) -> str:
    """«🔔 Напоминание…» → «✅ Напоминание…» (остальной текст не трогаем)."""
    text = text or ""
    if "🔔" in text:
        return text.replace("🔔", "✅", 1)
    return f"✅ {text}" if text else "✅"


async def append_status(message: Message, status: str):
    """Дописать к сообщению строку («❌ Напоминание отменено.») и убрать его кнопки."""
    if message.text is not None:
        await message.edit_text(f"{message.text[:4095 - len(status)]}\n{status}", reply_markup=None)
    elif message.caption is not None:
        await message.edit_caption(caption=f"{message.caption[:1023 - len(status)]}\n{status}", reply_markup=None)
    else:
        await message.edit_reply_markup(reply_markup=None)


def format_reminder(reminder: dict, tz: ZoneInfo) -> str:
    """Напоминание в «Моих напоминаниях», как в прошлой версии:
    «📆 Напоминание» / «📝 текст» / «🕐 когда» / «🔄 правило» и, если есть, дни недели, вложение, пересылка."""
    local_time = datetime.fromisoformat(reminder['run_at']).replace(tzinfo=UTC).astimezone(tz)
    repeat_type = reminder.get('repeat_type') or 'once'
    config = load_config(reminder.get('repeat_config'))

    # Текст пользователя экранируем: сообщение уходит с parse_mode="HTML",
    # и символы вроде "<" или "&" иначе ломают отправку.
    lines = [
        f"{LIST_ICONS.get(repeat_type, '📆')} <b>Напоминание</b>",
        f"📝 {html.escape(display_text(reminder.get('text')))}",
        f"🕐 {local_time:%d.%m.%Y %H:%M}",
        f"🔄 {html.escape(list_repeat_text(repeat_type, config))}",
    ]
    if repeat_type == 'weekly' and config.get('weekdays'):
        lines.append(f"📅 Дни: {format_weekdays(config['weekdays'])}")
    content_type = reminder.get('content_type') or 'text'
    if content_type != 'text' and reminder.get('file_id'):
        lines.append(f"📎 Вложения: {content_type} (1)")
    if reminder.get('is_forward'):
        lines.append("↪️ Из пересланного сообщения")
    return "\n".join(lines)


async def send_reminders_list(message: Message):
    """Каждое напоминание — отдельным сообщением со своей кнопкой отмены."""
    reminders = await get_user_reminders(message.chat.id)
    if not reminders:
        await message.answer(NO_REMINDERS)
        return

    user_tz = await get_user_timezone(message.chat.id)
    tz = ZoneInfo(user_tz) if user_tz else UTC
    for reminder in reminders:
        await message.answer(
            format_reminder(reminder, tz),
            parse_mode="HTML",
            reply_markup=get_reminder_actions_inline(reminder['id'], reminder.get('repeat_type', 'once'))
        )


async def send_settings(message: Message, state: FSMContext):
    """«⚙️ Настройки» и /timezone: текущий часовой пояс и как его сменить."""
    await state.clear()
    current_tz = await get_user_timezone(message.chat.id)
    if current_tz:
        text = f"🕗 Текущий часовой пояс: {current_tz}\nХотите изменить его?"
    else:
        text = "🕗 Часовой пояс не установлен.\nВыберите способ:"
    await message.answer(text, reply_markup=get_timezone_geo_or_manual())


# ---------- Отмена (работает на любом шаге) ----------

@router.message(F.text == BTN_CANCEL)
async def cancel_flow(message: Message, state: FSMContext):
    # Вне диалога «❌ Отмена» бывает внизу, если бот перезапускался и забыл диалог, — тоже возвращаем меню
    await state.clear()
    await message.answer(CANCELED, reply_markup=get_main_menu_keyboard())


@router.callback_query(F.data.in_({CANCEL_DATA, "cancel_action"}))
async def cancel_flow_button(callback: CallbackQuery, state: FSMContext):
    """«❌ Отмена» кнопкой в сообщении (из прошлых версий бота)."""
    await state.clear()
    await callback.message.answer(CANCELED, reply_markup=get_main_menu_keyboard())
    await callback.answer()


# ---------- Начало и приостановленные напоминания ----------

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await get_or_create_user(message.chat.id)
    if await is_user_blocked(message.chat.id):
        await set_user_blocked(message.chat.id, False)  # раз пишет боту — уже не заблокировал

    paused = await count_paused_reminders(message.chat.id)
    if paused:
        noun = plural(paused, 'приостановленное напоминание', 'приостановленных напоминания',
                      'приостановленных напоминаний')
        await message.answer(f"У вас {paused} {noun}. Что сделать?",
                             reply_markup=get_blocked_reminders_actions_keyboard())
        return

    if await get_user_timezone(message.chat.id):
        await message.answer(f"✅ Часовой пояс уже установлен.\n{CHOOSE_ACTION}", reply_markup=get_main_menu_keyboard())
    else:
        await message.answer(
            "👋 Привет! Чтобы напоминания приходили вовремя, укажите ваш часовой пояс.",
            reply_markup=get_timezone_geo_or_manual()
        )


def resumed_run_at(reminder: dict, user_tz: str | None, now: datetime) -> datetime:
    """Когда сработать напоминанию после паузы: пропущенные разы не догоняем — берём ближайший в будущем."""
    run_at = datetime.fromisoformat(reminder['run_at']).replace(tzinfo=timezone.utc)
    if run_at > now:
        return run_at
    next_run = compute_next_run(reminder.get('repeat_type') or 'once', run_at, user_tz,
                                reminder.get('repeat_config'), now)
    return next_run or now + timedelta(minutes=1)  # однократное — придёт через минуту


@router.callback_query(F.data == "blocked_resume_all")
async def blocked_resume_all(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    now = datetime.now(timezone.utc)
    user_tz = await get_user_timezone(chat_id)
    reminders = await get_paused_reminders(chat_id)
    for reminder in reminders:
        await resume_reminder(reminder['id'], resumed_run_at(reminder, user_tz, now))
    await callback.message.edit_text(f"✅ Возобновлено напоминаний: {len(reminders)}")
    await callback.message.answer(CHOOSE_ACTION, reply_markup=get_main_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "blocked_delete_all")
async def blocked_delete_all(callback: CallbackQuery):
    deleted = await delete_paused_reminders(callback.message.chat.id)
    await callback.message.edit_text(f"🗑 Удалено напоминаний: {deleted}")
    await callback.message.answer(CHOOSE_ACTION, reply_markup=get_main_menu_keyboard())
    await callback.answer()


# ---------- Часовой пояс ----------

@router.callback_query(F.data == "tz_cancel")
async def tz_cancel(callback: CallbackQuery):
    await delete_message(callback.message)
    await callback.message.answer(f"🕗 Выбор часового пояса отменён.\n{CHOOSE_ACTION}",
                                  reply_markup=get_main_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "tz_geo")
async def tz_geo_request(callback: CallbackQuery):
    """Запрос геолокации через ReplyKeyboard (кнопку геолокации можно показать только новым сообщением)"""
    await callback.message.edit_text("📍 Отправьте вашу геолокацию, нажав на кнопку ниже:")
    await callback.message.answer("Нажмите кнопку для отправки геолокации:", reply_markup=get_timezone_geo_button())
    await callback.answer()


@router.message(lambda msg: msg.location is not None)
async def handle_location(message: Message):
    lat, lon = message.location.latitude, message.location.longitude
    tz_name = tf.timezone_at(lat=lat, lng=lon)

    if tz_name:
        await get_or_create_user(message.chat.id)
        await set_user_timezone(message.chat.id, tz_name)
        await message.answer(f"🕗 Ваш часовой пояс: {tz_name}\n{CHOOSE_ACTION}", reply_markup=get_main_menu_keyboard())
    else:
        await message.answer("❌ Не удалось определить часовой пояс. Попробуйте другой способ.",
                             reply_markup=ReplyKeyboardRemove())
        await message.answer("Выберите способ:", reply_markup=get_timezone_geo_or_manual())


@router.callback_query(F.data == "tz_manual")
async def tz_manual(callback: CallbackQuery):
    await callback.message.edit_text("🌍 Выберите часовой пояс (отсортировано по UTC):",
                                     reply_markup=get_timezone_page_keyboard(page=0))
    await callback.answer()


@router.callback_query(F.data.startswith("tz_page_"))
async def tz_page_navigate(callback: CallbackQuery):
    page = parse_id(callback.data.removeprefix("tz_page_"))
    if page is not None:
        await callback.message.edit_reply_markup(reply_markup=get_timezone_page_keyboard(page=page))
    await callback.answer()


@router.callback_query(F.data.startswith("tz_select_"))
async def tz_selected(callback: CallbackQuery):
    tz_name = callback.data.removeprefix("tz_select_")
    try:
        ZoneInfo(tz_name)
    except Exception:
        await callback.answer("❌ Неверный часовой пояс.", show_alert=True)
        return
    await get_or_create_user(callback.message.chat.id)
    await set_user_timezone(callback.message.chat.id, tz_name)
    await delete_message(callback.message)
    await callback.message.answer(f"🕗 Часовой пояс установлен: {tz_name}\n{CHOOSE_ACTION}",
                                  reply_markup=get_main_menu_keyboard())
    await callback.answer()


@router.message(Command("timezone"))
async def cmd_timezone(message: Message, state: FSMContext):
    await send_settings(message, state)


@router.message(F.text == BTN_SETTINGS)
async def settings_text(message: Message, state: FSMContext):
    await send_settings(message, state)


@router.callback_query(F.data == "settings")
async def settings_callback(callback: CallbackQuery, state: FSMContext):
    await send_settings(callback.message, state)
    await callback.answer()


# ---------- Главное меню и статистика ----------

@router.message(F.text == BTN_NEW)
async def new_reminder_text(message: Message, state: FSMContext):
    await start_new_reminder(message, state)


@router.message(Command("remind"))
async def cmd_remind(message: Message, state: FSMContext):
    await start_new_reminder(message, state)


@router.callback_query(F.data == "new_reminder")
async def new_reminder_callback(callback: CallbackQuery, state: FSMContext):
    await start_new_reminder(callback.message, state)
    await callback.answer()


@router.message(F.text == BTN_LIST)
async def my_reminders_text(message: Message):
    await send_reminders_list(message)


@router.message(Command("list"))
async def cmd_list(message: Message):
    await send_reminders_list(message)


@router.callback_query(F.data == "my_reminders")
async def my_reminders_callback(callback: CallbackQuery):
    await send_reminders_list(callback.message)
    await callback.answer()


@router.message(Command("admin_stats"))
async def admin_stats(message: Message):
    """Статистика — только для id из ADMIN_IDS (в .env, через запятую). Остальным бот не отвечает."""
    if message.from_user is None or message.from_user.id not in load_admin_ids():
        return
    stats = await get_admin_stats()
    await message.answer(
        "📊 Статистика бота\n"
        f"👥 Пользователей: {stats['users_total']}\n"
        f"🔔 Напоминаний: {stats['reminders_total']}\n"
        f"📈 Среднее напоминаний на пользователя: {stats['avg_reminders_per_user']:.2f}"
    )


# ---------- Создание напоминания ----------
# Текст или вложение → тип → параметры типа → время → «✅ Напоминание установлено».
# Как в прошлой версии: сообщения с кнопками внутри (типы, календарь, дни недели, режим и дни месяца)
# исчезают, как только выбор сделан, а вопросы с нижней клавиатурой остаются в чате.

async def start_new_reminder(message: Message, state: FSMContext):
    if not await get_user_timezone(message.chat.id):
        await message.answer(NO_TIMEZONE)
        return
    await state.set_data({})  # выбор из брошенного диалога не нужен
    await message.answer(TEXT_STEP, reply_markup=get_cancel_keyboard())
    await state.set_state(ReminderFlow.waiting_for_text)


async def save_content(message: Message, state: FSMContext, content: tuple):
    content_type, file_id, text = content
    await state.update_data(content_type=content_type, file_id=file_id, text=text,
                            is_forward=is_forwarded(message))


@router.message(ReminderFlow.waiting_for_text)
async def remind_content(message: Message, state: FSMContext):
    content = extract_content(message)
    if content is None:
        await message.answer(UNSUPPORTED_CONTENT)
        return
    await save_content(message, state, content)
    await message.answer(TYPE_STEP, reply_markup=get_repeat_type_keyboard())
    await state.set_state(ReminderFlow.waiting_for_repeat_type)


async def ask_calendar(message: Message, text: str):
    # Сегодняшняя дата — пользователя, а не сервера
    today = user_now(await get_user_timezone(message.chat.id)).date()
    await message.answer(text, parse_mode="HTML",
                         reply_markup=get_calendar(today.year, today.month, message.chat.id, today=today))


@router.callback_query(ReminderFlow.waiting_for_repeat_type, F.data.startswith("repeat_"))
async def repeat_type_selected(callback: CallbackQuery, state: FSMContext):
    repeat_type = callback.data.removeprefix("repeat_")
    if repeat_type not in REPEAT_TYPES:
        await callback.answer()
        return

    await state.update_data(repeat_type=repeat_type)
    message = callback.message
    await delete_message(message)

    if repeat_type in ('hourly', 'daily'):
        rule, unit = ("Каждые N часов", "часов") if repeat_type == 'hourly' else ("Каждые N дней", "дней")
        await message.answer(
            step_text("🔁", rule, f"Как часто напоминать?\n"
                                  f"Выберите количество {unit} из предложенных ниже или введите своё число."),
            parse_mode="HTML", reply_markup=get_interval_keyboard(repeat_type),
        )
        await state.set_state(ReminderFlow.waiting_for_interval)
    elif repeat_type == 'weekly':
        await message.answer(step_text("📆", "Каждую неделю", "Выберите дни недели для повторения."),
                             parse_mode="HTML", reply_markup=get_weekdays_keyboard())
        await state.set_state(ReminderFlow.waiting_for_weekdays)
    elif repeat_type == 'monthly':
        await message.answer(step_text("📆", "Каждый месяц", "Как напоминать каждый месяц?"),
                             parse_mode="HTML", reply_markup=get_month_mode_keyboard())
        await state.set_state(ReminderFlow.waiting_for_month_mode)
    else:  # once
        await ask_calendar(message, step_text("🗓", "Однократно", "Выберите дату для напоминания."))
        await state.set_state(ReminderFlow.waiting_for_date)
    await callback.answer()


@router.message(ReminderFlow.waiting_for_interval)
async def interval_input(message: Message, state: FSMContext):
    repeat_type = (await state.get_data()).get('repeat_type', 'daily')
    limit = NUMBER_LIMITS[repeat_type]
    n = parse_number(message.text, limit)
    if n is None:
        await message.answer(f"❌ Введите число от 1 до {limit}.", reply_markup=get_interval_keyboard(repeat_type))
        return

    await state.update_data(interval=n)
    if repeat_type == 'hourly':
        await message.answer(
            step_text("🔃", every_text('hourly', n),
                      "От какого момента начать отсчёт?\n"
                      "Нажмите «📍 С текущего момента», выберите время или введите вручную (например: 1015)."),
            parse_mode="HTML", reply_markup=get_time_keyboard(start_now=True),
        )
        await state.set_state(ReminderFlow.waiting_for_time)
    else:
        # Дальше календарь (кнопки в сообщении) — внизу оставляем только «❌ Отмена»
        await message.answer("❌ Для отмены используйте кнопку ниже.", reply_markup=get_cancel_keyboard())
        await ask_calendar(message, step_text("🔃", every_text('daily', n)))
        await state.set_state(ReminderFlow.waiting_for_date)


@router.callback_query(ReminderFlow.waiting_for_weekdays, F.data.startswith("weekday_"))
async def weekday_toggle(callback: CallbackQuery, state: FSMContext):
    selected = set((await state.get_data()).get('selected_weekdays', set()))

    if callback.data == "weekday_done":
        if not selected:
            await callback.answer(NO_DAYS, show_alert=True)
            return
        await delete_message(callback.message)
        await callback.message.answer(
            step_text("📆", f"Каждую неделю: {format_weekdays(selected)}", WEEKLY_TIME_STEP),
            parse_mode="HTML", reply_markup=get_time_keyboard(),
        )
        await state.set_state(ReminderFlow.waiting_for_time)
        await callback.answer()
        return

    weekday = callback.data.removeprefix("weekday_")
    if weekday not in WEEKDAY_ORDER:
        await callback.answer()
        return
    selected ^= {weekday}
    await state.update_data(selected_weekdays=selected)
    await callback.message.edit_reply_markup(reply_markup=get_weekdays_keyboard(selected))
    await callback.answer()


@router.callback_query(ReminderFlow.waiting_for_month_mode, F.data.startswith("monthmode_"))
async def month_mode_selected(callback: CallbackQuery, state: FSMContext):
    mode = callback.data.removeprefix("monthmode_")
    if mode not in ('days', 'last', 'before'):
        await callback.answer()
        return
    message = callback.message
    await delete_message(message)

    if mode == 'days':
        await message.answer(step_text("📆", "Каждый месяц", MONTH_DAYS_STEP),
                             parse_mode="HTML", reply_markup=get_month_days_keyboard())
        await state.set_state(ReminderFlow.waiting_for_month_days)
    elif mode == 'last':
        config = {'last': True}
        await state.update_data(month_config=config)
        await message.answer(step_text("📆", rule_text('monthly', config), TIME_STEP),
                             parse_mode="HTML", reply_markup=get_time_keyboard())
        await state.set_state(ReminderFlow.waiting_for_time)
    else:
        await message.answer(
            step_text("📆", "Каждый месяц, за N дней до конца месяца",
                      "Укажите, за сколько дней до конца месяца напоминать.\nВыберите число ниже или введите своё."),
            parse_mode="HTML", reply_markup=get_interval_keyboard('before_end'),
        )
        await state.set_state(ReminderFlow.waiting_for_month_offset)
    await callback.answer()


@router.callback_query(ReminderFlow.waiting_for_month_days, F.data.startswith("mday_"))
async def month_day_toggle(callback: CallbackQuery, state: FSMContext):
    selected = set((await state.get_data()).get('month_days', set()))

    if callback.data == "mday_done":
        if not selected:
            await callback.answer(NO_DAYS, show_alert=True)
            return
        config = {'days': sorted(selected)}
        await state.update_data(month_config=config)
        await delete_message(callback.message)
        await callback.message.answer(step_text("📆", rule_text('monthly', config), TIME_STEP),
                                      parse_mode="HTML", reply_markup=get_time_keyboard())
        await state.set_state(ReminderFlow.waiting_for_time)
        await callback.answer()
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
        await message.answer(f"❌ Введите число от 1 до {limit}.", reply_markup=get_interval_keyboard('before_end'))
        return
    config = {'before_end': n}
    await state.update_data(month_config=config)
    await message.answer(step_text("📆", f"Каждый месяц, {before_end_text(n)} до конца месяца", TIME_STEP),
                         parse_mode="HTML", reply_markup=get_time_keyboard())
    await state.set_state(ReminderFlow.waiting_for_time)


@router.callback_query(ReminderFlow.waiting_for_date, F.data.startswith("cal_"))
async def calendar_nav(callback: CallbackQuery):
    parts = callback.data.split("_")
    if len(parts) != 4 or not (parts[2].isdigit() and parts[3].isdigit()):
        await callback.answer()
        return
    _, chat_id, year, month = parts
    if chat_id != str(callback.message.chat.id):
        await callback.answer("Это не ваш календарь!", show_alert=True)
        return
    today = user_now(await get_user_timezone(callback.message.chat.id)).date()
    await callback.message.edit_reply_markup(reply_markup=get_calendar(int(year), int(month), chat_id, today=today))
    await callback.answer()


@router.callback_query(ReminderFlow.waiting_for_date, F.data.startswith("date_"))
async def date_selected(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    if len(parts) != 5 or not all(p.isdigit() for p in parts[2:]):
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
    data = await state.get_data()
    await delete_message(callback.message)
    if data.get('repeat_type') == 'daily':
        text = step_text("🔁", f"{every_text('daily', data.get('interval', 1))}, начиная с {format_date(selected_date)}",
                         TIME_STEP)
    else:
        text = step_text("🗓", f"Однократно, {format_date(selected_date)}", "Во сколько напоминать?")
    await callback.message.answer(text, parse_mode="HTML", reply_markup=get_time_keyboard())
    await state.set_state(ReminderFlow.waiting_for_time)
    await callback.answer()


# Кнопка шага создания нажата вне диалога (старое сообщение или бот перезапускался)
@router.callback_query(F.data.startswith(FLOW_BUTTON_PREFIXES))
async def stale_flow_button(callback: CallbackQuery):
    await callback.answer("⌛ Эта кнопка устарела. Начните заново: /remind", show_alert=True)


@router.message(ReminderFlow.waiting_for_time)
async def time_input(message: Message, state: FSMContext):
    """Время с клавиатуры (09:00), набранное вручную (1015, 930, 14:30) или «📍 С текущего момента»."""
    repeat_type = (await state.get_data()).get('repeat_type', 'once')
    if message.text == BTN_START_NOW and repeat_type == 'hourly':
        await finalize_reminder(message, state, start_now=True)
        return

    selected_time = parse_time(message.text)
    if selected_time is None:
        error = TIME_ERROR_DETAILED if repeat_type in ('once', 'weekly') else TIME_ERROR
        await message.answer(error, reply_markup=get_time_keyboard(start_now=repeat_type == 'hourly'))
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
            await message.answer(TIME_PASSED, reply_markup=get_time_keyboard())  # остаёмся на шаге времени
            return

    await create_reminder(
        user_id, run_at.astimezone(UTC), text,
        repeat_type=repeat_type,
        repeat_config=json.dumps(config) if config else None,
        content_type=data.get('content_type', 'text'),
        file_id=data.get('file_id'),
        is_forward=data.get('is_forward', False),
    )

    when = f"{format_date(run_at)} в {run_at:%H:%M}"
    lines = [
        "✅ <b>Напоминание установлено</b>",
        f"📝 {html.escape(display_text(text))}",
        html.escape(describe_repeat(repeat_type, config)),
    ]
    if repeat_type == 'monthly' and month_note(config):
        lines.append(month_note(config))
    lines.append(f"🕰 {when}" if repeat_type == 'once' else f"🕰 Следующее: {when}")
    await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=get_main_menu_keyboard())
    await state.clear()


# ---------- Кнопки у напоминаний ----------

# «❌ №N» под общим списком из ранней версии бота: отменить и убрать эту кнопку
@router.callback_query(F.data.startswith("lcancel_"))
async def list_cancel_reminder(callback: CallbackQuery):
    reminder_id = parse_id(callback.data.removeprefix("lcancel_"))
    if not await get_own_reminder(reminder_id, callback.message.chat.id):
        await callback.answer(NOT_FOUND, show_alert=True)
        return

    await update_reminder_status(reminder_id, 'canceled')
    markup = getattr(callback.message, 'reply_markup', None)
    if markup is not None:
        rows = [[b for b in row if b.callback_data != callback.data] for row in markup.inline_keyboard]
        rows = [row for row in rows if row]
        await callback.message.edit_reply_markup(
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None)
    await callback.answer("✅ Напоминание отменено")


@router.callback_query(F.data.startswith("cancel_reminder_"))
async def cancel_single_reminder(callback: CallbackQuery):
    reminder_id = parse_id(callback.data.removeprefix("cancel_reminder_"))
    if not await get_own_reminder(reminder_id, callback.message.chat.id):
        await callback.answer(NOT_FOUND, show_alert=True)
        return
    await update_reminder_status(reminder_id, 'canceled')
    await append_status(callback.message, "❌ Напоминание отменено.")
    await callback.answer("✅ Напоминание отменено")


@router.callback_query(F.data.startswith("cancel_series_"))
async def cancel_series_reminder(callback: CallbackQuery):
    reminder_id = parse_id(callback.data.removeprefix("cancel_series_"))
    if not await get_own_reminder(reminder_id, callback.message.chat.id):
        await callback.answer(NOT_FOUND, show_alert=True)
        return
    await update_reminder_status(reminder_id, 'canceled')
    await append_status(callback.message, "❌ Серия напоминаний отменена.")
    await callback.answer("✅ Серия отменена")


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

    # Новое однократное напоминание с тем же содержимым; основное (если это серия) работает дальше
    new_time = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    await create_reminder(reminder['user_id'], new_time, reminder['text'], repeat_type='once',
                          content_type=reminder.get('content_type') or 'text', file_id=reminder.get('file_id'),
                          is_forward=bool(reminder.get('is_forward')))

    # Само напоминание остаётся, под ним жирным «⏰ Напоминание отложено на 30 минут.», кнопки убираем
    note = f"Напоминание отложено на {minutes} {plural(minutes, 'минуту', 'минуты', 'минут')}."
    room = len(note) + 12  # «\n⏰ <b>…</b>» не должно вылезти за лимит Telegram
    message = callback.message
    if message.text is not None:
        text = html.escape(message.text[:4096 - room])
        await message.edit_text(f"{text}\n⏰ <b>{note}</b>", parse_mode="HTML", reply_markup=None)
    elif message.caption is not None:
        caption = html.escape(message.caption[:1024 - room])
        await message.edit_caption(caption=f"{caption}\n⏰ <b>{note}</b>", parse_mode="HTML", reply_markup=None)
    else:
        await message.edit_reply_markup(reply_markup=None)  # стикер или кружок из прошлой версии
    await callback.answer(f"✅ Отложено на {minutes} мин")


@router.callback_query(F.data.startswith("done_"))
async def done_reminder(callback: CallbackQuery):
    reminder_id = parse_id(callback.data.removeprefix("done_"))
    reminder = await get_own_reminder(reminder_id, callback.message.chat.id)
    if not reminder:
        await callback.answer(NOT_FOUND, show_alert=True)
        return

    # Однократное больше не нужно; у серии ничего не меняем — она продолжит работать по расписанию
    if reminder.get('repeat_type', 'once') == 'once':
        await update_reminder_status(reminder_id, 'sent')

    # Само напоминание остаётся как было: только 🔔 → ✅ и без кнопок
    message = callback.message
    if message.text is not None:
        await message.edit_text(mark_done(message.text), reply_markup=None)
    elif message.caption is not None:
        await message.edit_caption(caption=mark_done(message.caption), reply_markup=None)
    else:
        await message.edit_reply_markup(reply_markup=None)  # стикер или кружок из прошлой версии
    await callback.answer()


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
    await append_status(callback.message, "❌ Напоминание отменено.")
    await callback.answer()


@router.callback_query(F.data == "ignore")
async def ignore_callback(callback: CallbackQuery):
    await callback.answer()


# Сообщение вне диалога (текст, вложение, пересланное) сразу становится новым напоминанием.
# Регистрируется последним: кнопки меню, команды и шаги диалога обрабатываются раньше.
@router.message(StateFilter(None))
async def auto_create_reminder(message: Message, state: FSMContext):
    incoming = message.text or ""
    if incoming in MENU_TEXTS or incoming.startswith("/"):
        return
    content = extract_content(message)
    if content is None:
        return
    if not await get_user_timezone(message.chat.id):
        await message.answer(NO_TIMEZONE)
        return
    await state.set_data({})
    await save_content(message, state, content)
    await state.set_state(ReminderFlow.waiting_for_repeat_type)
    await message.answer(FORWARD_TYPE_STEP if is_forwarded(message) else TYPE_STEP,
                         reply_markup=get_repeat_type_keyboard())


dp.include_router(router)


def read_env(key: str) -> str | None:
    """Значение из переменной окружения, а если её нет — из файла .env рядом с ботом."""
    value = os.getenv(key, "").strip()
    if value:
        return value
    try:
        # utf-8-sig: Блокнот Windows может сохранить файл с BOM
        lines = ENV_FILE.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return None
    for line in lines:
        name, sep, raw = line.partition("=")
        if sep and name.strip() == key:
            return raw.strip().strip('"').strip("'") or None
    return None


def load_token() -> str | None:
    """Токен из переменной окружения BOT_TOKEN, а если её нет — из файла .env рядом с ботом."""
    return read_env("BOT_TOKEN")


def load_admin_ids() -> set[int]:
    """ADMIN_IDS=123,456 — кому отвечает /admin_stats."""
    raw = read_env("ADMIN_IDS") or ""
    return {int(part) for part in (p.strip() for p in raw.split(",")) if part.lstrip("-").isdigit()}


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
