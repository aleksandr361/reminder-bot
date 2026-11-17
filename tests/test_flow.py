"""Диалог создания напоминания — как в прошлой рабочей версии бота (папка remember):
текст или вложение → тип повторения → параметры типа → время → «✅ Напоминание установлено».

Шаг — «<значок> <b>правило</b>» и с новой строки, что сделать. Сообщения с кнопками внутри (типы, календарь,
дни недели, режим и дни месяца) исчезают после выбора; вопросы с нижней клавиатурой остаются в чате.
Окончания слов — правильные («Каждые 3 часа», «за 3 дня»), как в ТЗ.
"""
import calendar
import json
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

import pytest
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove

import storage
from conftest import FakeCallback, FakeMessage, all_reminders, make_user

UTC = timezone.utc
CANCEL = "❌ Отмена"
MAIN_MENU = [["➕ Новое напоминание"], ["📋 Мои напоминания"], ["⚙️ Настройки"]]
TEXT_QUESTION = "📝 Отправьте текст или вложение для напоминания:"
TYPE_QUESTION = "🔄 Выберите тип повторения:"
TIME_HINT = "Во сколько напоминать?\nВыберите время ниже или введите вручную (например: 1015)."
CANCELED = "🛑 Создание напоминания отменено.\nВыберите действие:"
HOUR_ROWS = [["08:00", "09:00", "10:00", "11:00"], ["12:00", "13:00", "14:00", "15:00"],
             ["16:00", "17:00", "18:00", "19:00"], ["20:00", "21:00", "22:00", "23:00"]]
TIME_ROWS = HOUR_ROWS + [[CANCEL]]
TIME_ERROR = "❌ Неверный формат времени.\nВведите в формате ЧЧММ (например, 1015) или ЧЧ:ММ (например, 10:15)."
TIME_ERROR_DETAILED = ("❌ Неверный формат времени.\nПожалуйста, выберите время из кнопок ниже или введите в формате:\n"
                       "• ЧЧММ (например, `1015`)\n• ЧЧ:ММ (например, `10:15`)")


# ---------- Помощники ----------

def inline_datas(markup):
    assert isinstance(markup, InlineKeyboardMarkup), markup
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def inline_texts(markup):
    return [b.text for row in markup.inline_keyboard for b in row]


def reply_rows(markup):
    assert isinstance(markup, ReplyKeyboardMarkup), markup
    return [[b.text for b in row] for row in markup.keyboard]


def markup_of(msg):
    return msg.kwargs.get("reply_markup")


def bot_messages(*msgs):
    """Все сообщения бота, порождённые этими сообщениями (рекурсивно: ответы на ответы)."""
    out, stack = [], list(msgs)
    while stack:
        m = stack.pop()
        for reply in m.answered:
            out.append(reply)
            stack.append(reply)
    return out


def visible_keyboard(messages):
    """Какая нижняя клавиатура сейчас видна в Telegram: её задаёт последнее сообщение бота с клавиатурой
    (если его удалить, на Android клавиатура пропадает)."""
    with_kb = [m for m in messages if isinstance(markup_of(m), (ReplyKeyboardMarkup, ReplyKeyboardRemove))]
    if not with_kb:
        return None
    latest = max(with_kb, key=lambda m: m.message_id)
    if latest.is_deleted or isinstance(markup_of(latest), ReplyKeyboardRemove):
        return None
    return reply_rows(markup_of(latest))


async def say(send, state, text=None, **attachment):
    msg = FakeMessage(1, text=text)
    for key, value in attachment.items():
        setattr(msg, key, value)
    await send(msg, state)
    return msg


async def tap(press, state, msg, data):
    cb = FakeCallback(data, msg)
    await press(cb, state)
    return cb


async def open_menu(send, state):
    """«➕ Новое напоминание». Возвращает сообщение пользователя (ответы бота — в .answered)."""
    return await say(send, state, "➕ Новое напоминание")


async def to_type_menu(send, state, text="купить хлеб", **attachment):
    """«➕» → текст (или вложение). Возвращает сообщение «🔄 Выберите тип повторения:»."""
    await open_menu(send, state)
    content = await say(send, state, None if attachment else text, **attachment)
    return content.answered[-1]


async def choose_type(send, press, state, repeat, text="купить хлеб", **attachment):
    """…→ тип. Возвращает сообщение с выбором типа (оно удалено; следующий шаг — в его .answered)."""
    menu = await to_type_menu(send, state, text, **attachment)
    await tap(press, state, menu, f"repeat_{repeat}")
    return menu


def date_data(d):
    return f"date_1_{d.year}_{d.month}_{d.day}"


def today_utc():
    return datetime.now(UTC).date()


def tomorrow_utc():
    return today_utc() + timedelta(days=1)


def fmt(main_module, d):
    return main_module.format_date(d)


async def run_at_of_last():
    return datetime.fromisoformat((await all_reminders())[-1]["run_at"]).replace(tzinfo=UTC)


# ---------- Начало создания ----------

async def test_new_reminder_asks_text_with_cancel_keyboard(send, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    start = await open_menu(send, state)

    [question] = start.answered
    assert question.text == TEXT_QUESTION
    assert reply_rows(markup_of(question)) == [[CANCEL]]
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_text.state


async def test_remind_command_and_new_button_in_message_start_the_same(press, fsm, main_module):
    await make_user(1, tz="UTC")
    by_command = FakeMessage(1, text="/remind")
    await main_module.cmd_remind(by_command, fsm(1))
    by_button = FakeMessage(2, text="📋")
    await make_user(2, tz="UTC")
    await press(FakeCallback("new_reminder", by_button), fsm(2))
    assert by_command.answered[0].text == by_button.answered[0].text == TEXT_QUESTION


async def test_new_reminder_without_timezone_asks_for_it(send, fsm):
    await storage.get_or_create_user(1)
    state = fsm(1)
    start = await open_menu(send, state)
    assert start.answered[-1].text == "❌ Сначала установите часовой пояс: /timezone"
    assert await state.get_state() is None


async def test_after_text_bot_offers_five_types(send, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await open_menu(send, state)
    content = await say(send, state, "купить хлеб")

    [menu] = content.answered
    assert menu.text == TYPE_QUESTION
    assert inline_datas(markup_of(menu)) == [
        "repeat_once", "repeat_hourly", "repeat_daily", "repeat_weekly", "repeat_monthly"
    ]
    assert inline_texts(markup_of(menu)) == [
        "🗓 Однократно", "🔁 Каждые N часов", "🔁 Каждые N дней", "📆 Каждую неделю", "📆 Каждый месяц"
    ]
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_repeat_type.state


async def test_unsupported_content_is_rejected(send, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await open_menu(send, state)
    reply = await say(send, state, poll=SimpleNamespace(question="?"))
    assert reply.answered[-1].text == ("❌ Поддерживаются текст, фото, видео, аудио, голосовые, документы "
                                       "и другие вложения.")
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_text.state


async def test_type_menu_disappears_after_choice(send, press, fsm):
    await make_user(1, tz="UTC")
    menu = await choose_type(send, press, fsm(1), "weekly")
    assert menu.is_deleted


async def test_new_reminder_resets_abandoned_choices(send, press, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    menu = await choose_type(send, press, state, "weekly")
    await tap(press, state, menu.answered[-1], "weekday_mon")
    await open_menu(send, state)
    data = await state.get_data()
    assert not {"selected_weekdays", "text", "repeat_type"} & set(data)
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_text.state


# ---------- Сообщение вне диалога сразу становится напоминанием ----------

async def test_text_outside_flow_goes_straight_to_types(send, press, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    msg = await say(send, state, "Купить хлеб")

    # кнопки главного меню внизу сменяются на «❌ Отмена», затем — выбор типа
    carrier, menu = msg.answered
    assert carrier.text == "Создание напоминания"
    assert reply_rows(markup_of(carrier)) == [[CANCEL]]
    assert menu.text == TYPE_QUESTION
    assert isinstance(markup_of(menu), InlineKeyboardMarkup)
    assert visible_keyboard(msg.answered) == [[CANCEL]]
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_repeat_type.state

    await tap(press, state, menu, "repeat_hourly")
    await say(send, state, "2")
    await say(send, state, "📍 С текущего момента")
    rem = (await all_reminders())[-1]
    assert (rem["text"], rem["repeat_type"]) == ("Купить хлеб", "hourly")


async def test_attachment_outside_flow_starts_reminder_too(send, press, fsm):
    await make_user(1, tz="UTC")
    state = fsm(1)
    photo = [SimpleNamespace(file_id="small"), SimpleNamespace(file_id="big")]
    msg = await say(send, state, photo=photo, caption="рецепт")
    assert msg.answered[-1].text == TYPE_QUESTION
    await tap(press, state, msg.answered[-1], "repeat_hourly")
    await say(send, state, "1")
    await say(send, state, "📍 С текущего момента")
    rem = (await all_reminders())[-1]
    assert (rem["content_type"], rem["file_id"], rem["text"]) == ("photo", "big", "рецепт")


async def test_forwarded_message_outside_flow(send, press, fsm):
    await make_user(1, tz="UTC")
    state = fsm(1)
    msg = await say(send, state, "из чата", forward_origin=SimpleNamespace(type="user"))
    assert msg.answered[-1].text == "↪️ Пересланное сообщение принято. Выберите тип повторения:"
    await tap(press, state, msg.answered[-1], "repeat_hourly")
    await say(send, state, "1")
    await say(send, state, "📍 С текущего момента")
    assert (await all_reminders())[-1]["is_forward"] == 1

    listed = await say(send, state, "📋 Мои напоминания")
    assert listed.answered[-1].text.endswith("\n↪️ Из пересланного сообщения")


async def test_commands_and_menu_buttons_do_not_start_reminder(send, fsm):
    await make_user(1, tz="UTC")
    state = fsm(1)
    unknown = await say(send, state, "/unknown")
    assert unknown.answered == [] and await state.get_state() is None
    menu = await say(send, state, "📋 Мои напоминания")
    assert await state.get_state() is None
    assert menu.answered and "напоминаний" in menu.answered[-1].text


async def test_text_outside_flow_without_timezone_asks_for_it(send, fsm):
    await storage.get_or_create_user(1)
    state = fsm(1)
    msg = await say(send, state, "Купить хлеб")
    assert await state.get_state() is None
    assert msg.answered[-1].text == "❌ Сначала установите часовой пояс: /timezone"


async def test_text_inside_flow_belongs_to_current_step(send, press, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await choose_type(send, press, state, "daily")
    await say(send, state, "Купить молоко")  # не число — ошибка шага, а не новое напоминание
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_interval.state


async def test_typing_on_button_step_is_ignored(send, press, fsm, main_module):
    """Как в прошлой версии: на шаге с кнопками в сообщении печатный текст бот не трогает."""
    await make_user(1, tz="UTC")
    state = fsm(1)
    await choose_type(send, press, state, "once")
    reply = await say(send, state, "завтра")
    assert reply.answered == []
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_date.state


# ---------- 🗓 Однократно ----------

async def test_once_flow(send, press, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    menu = await choose_type(send, press, state, "once", text="к ортопеду с Андреем")
    calendar_msg = menu.answered[-1]
    assert calendar_msg.text == "🗓 <b>Однократно</b>\nВыберите дату для напоминания."
    assert calendar_msg.kwargs.get("parse_mode") == "HTML"
    today = today_utc()
    assert f"{today.day}*" in inline_texts(markup_of(calendar_msg))
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_date.state

    day = tomorrow_utc()
    await tap(press, state, calendar_msg, date_data(day))
    assert calendar_msg.is_deleted
    time_q = calendar_msg.answered[-1]
    assert time_q.text == f"🗓 <b>Однократно, {fmt(main_module, day)}</b>\nВо сколько напоминать?"
    assert reply_rows(markup_of(time_q)) == TIME_ROWS

    done = await say(send, state, "2000")
    confirmation = done.answered[-1]
    assert confirmation.text == ("✅ <b>Напоминание установлено</b>\n📝 к ортопеду с Андреем\n🗓 Однократно\n"
                                 f"🕰 {fmt(main_module, day)} в 20:00")
    assert confirmation.kwargs.get("parse_mode") == "HTML"
    assert reply_rows(markup_of(confirmation)) == MAIN_MENU
    assert (await all_reminders())[-1]["run_at"] == f"{day.isoformat()}T20:00:00"
    assert await state.get_state() is None
    assert not time_q.is_deleted  # вопросы с нижней клавиатурой остаются в чате


async def test_past_date_is_not_accepted(send, press, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    menu = await choose_type(send, press, state, "once")
    yesterday = today_utc() - timedelta(days=1)
    cb = await tap(press, state, menu.answered[-1], date_data(yesterday))
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_date.state
    assert cb.answers[-1][1] is True


async def test_past_time_today_asks_again(send, press, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    menu = await choose_type(send, press, state, "once")
    await tap(press, state, menu.answered[-1], date_data(today_utc()))
    reply = await say(send, state, "0000")
    assert reply.answered[-1].text == "❌ Время уже прошло. Введите другое:"
    assert reply_rows(markup_of(reply.answered[-1])) == TIME_ROWS
    assert await all_reminders() == []
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_time.state


@pytest.mark.parametrize("repeat", ["once", "weekly"])
async def test_wrong_time_detailed_error_for_once_and_weekly(send, press, fsm, main_module, repeat):
    await make_user(1, tz="UTC")
    state = fsm(1)
    menu = await choose_type(send, press, state, repeat)
    step = menu.answered[-1]
    if repeat == "once":
        await tap(press, state, step, date_data(tomorrow_utc()))
    else:
        await tap(press, state, step, "weekday_mon")
        await tap(press, state, step, "weekday_done")
    reply = await say(send, state, "abc")
    assert reply.answered[-1].text == TIME_ERROR_DETAILED
    assert reply.answered[-1].kwargs.get("parse_mode") is None  # `1015` — как есть
    assert reply_rows(markup_of(reply.answered[-1])) == TIME_ROWS
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_time.state


async def test_start_now_button_only_for_hours(send, press, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    menu = await choose_type(send, press, state, "once")
    await tap(press, state, menu.answered[-1], date_data(tomorrow_utc()))
    reply = await say(send, state, "📍 С текущего момента")
    assert await all_reminders() == []
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_time.state
    assert reply.answered[-1].text == TIME_ERROR_DETAILED


# ---------- 🔁 Каждые N часов ----------

async def test_hours_number_step(send, press, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    menu = await choose_type(send, press, state, "hourly")
    prompt = menu.answered[-1]
    assert prompt.text == ("🔁 <b>Каждые N часов</b>\nКак часто напоминать?\n"
                           "Выберите количество часов из предложенных ниже или введите своё число.")
    assert reply_rows(markup_of(prompt)) == [["1", "2", "3", "4"], ["6", "8", "12", "24"], [CANCEL]]
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_interval.state


async def test_hours_start_step(send, press, fsm):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await choose_type(send, press, state, "hourly")
    n = await say(send, state, "3")
    [start_q] = n.answered
    assert start_q.text == ("🔃 <b>Каждые 3 часа</b>\nОт какого момента начать отсчёт?\n"
                            "Нажмите «📍 С текущего момента», выберите время или введите вручную (например: 1015).")
    assert reply_rows(markup_of(start_q)) == [["📍 С текущего момента"]] + TIME_ROWS


@pytest.mark.parametrize("n,rule", [(1, "Каждый час"), (5, "Каждые 5 часов"), (21, "Каждые 21 час")])
async def test_hours_rule_has_correct_endings(send, press, fsm, n, rule):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await choose_type(send, press, state, "hourly")
    reply = await say(send, state, str(n))
    assert reply.answered[-1].text.startswith(f"🔃 <b>{rule}</b>\n")


async def test_hours_start_now(send, press, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await choose_type(send, press, state, "hourly", text="пить воду")
    await say(send, state, "3")
    before = datetime.now(UTC).replace(second=0, microsecond=0)
    now_msg = await say(send, state, "📍 С текущего момента")

    run_at = await run_at_of_last()
    assert before + timedelta(hours=3) <= run_at <= before + timedelta(hours=3, minutes=1)
    assert json.loads((await all_reminders())[-1]["repeat_config"]) == {"every": 3}
    assert now_msg.answered[-1].text == ("✅ <b>Напоминание установлено</b>\n📝 пить воду\n🔃 Каждые 3 часа\n"
                                         f"🕰 Следующее: {fmt(main_module, run_at.date())} в {run_at:%H:%M}")
    assert await state.get_state() is None


@pytest.mark.parametrize("offset_minutes", [120, -240, -30])
async def test_hours_start_from_time_counts_from_it(send, press, fsm, offset_minutes):
    """Отсчёт от выбранного времени сегодня: T, T+N, T+2N… — ближайшее в будущем."""
    await make_user(1, tz="UTC")
    state = fsm(1)
    await choose_type(send, press, state, "hourly")
    await say(send, state, "3")
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    t = (now + timedelta(minutes=offset_minutes)).time()
    await say(send, state, t.strftime("%H%M"))

    expected = datetime.combine(now.date(), t, tzinfo=UTC)
    while expected <= datetime.now(UTC):
        expected += timedelta(hours=3)
    assert await run_at_of_last() == expected


@pytest.mark.parametrize("typed", ["0", "-2", "abc", "2.5", "169", ""])
async def test_wrong_hours_number_is_rejected(send, press, fsm, main_module, typed):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await choose_type(send, press, state, "hourly")
    reply = await say(send, state, typed)
    assert reply.answered[-1].text == "❌ Введите число от 1 до 168."
    assert reply_rows(markup_of(reply.answered[-1]))[-1] == [CANCEL]
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_interval.state


async def test_hours_wrong_time_short_error_keeps_start_now(send, press, fsm):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await choose_type(send, press, state, "hourly")
    await say(send, state, "3")
    reply = await say(send, state, "abc")
    assert reply.answered[-1].text == TIME_ERROR
    assert reply_rows(markup_of(reply.answered[-1]))[0] == ["📍 С текущего момента"]


# ---------- 🔁 Каждые N дней ----------

async def test_days_number_step(send, press, fsm):
    await make_user(1, tz="UTC")
    menu = await choose_type(send, press, fsm(1), "daily")
    prompt = menu.answered[-1]
    assert prompt.text == ("🔁 <b>Каждые N дней</b>\nКак часто напоминать?\n"
                           "Выберите количество дней из предложенных ниже или введите своё число.")
    assert reply_rows(markup_of(prompt)) == [["1", "2", "3", "7"], ["10", "14", "21", "30"], [CANCEL]]


async def test_days_full_flow(send, press, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await choose_type(send, press, state, "daily", text="Отправить отчёт")
    n = await say(send, state, "7")

    # как в прошлой версии: внизу — только «❌ Отмена», затем календарь
    cancel_note, calendar_msg = n.answered
    assert cancel_note.text == "❌ Для отмены используйте кнопку ниже."
    assert reply_rows(markup_of(cancel_note)) == [[CANCEL]]
    assert calendar_msg.text == "🔃 <b>Каждые 7 дней</b>"
    assert any(d.startswith("date_") for d in inline_datas(markup_of(calendar_msg)))

    day = tomorrow_utc()
    await tap(press, state, calendar_msg, date_data(day))
    assert calendar_msg.is_deleted
    time_q = calendar_msg.answered[-1]
    assert time_q.text == f"🔁 <b>Каждые 7 дней, начиная с {fmt(main_module, day)}</b>\n{TIME_HINT}"
    assert reply_rows(markup_of(time_q)) == TIME_ROWS

    done = await say(send, state, "1000")
    assert done.answered[-1].text == ("✅ <b>Напоминание установлено</b>\n📝 Отправить отчёт\n🔃 Каждые 7 дней\n"
                                      f"🕰 Следующее: {fmt(main_module, day)} в 10:00")
    rem = (await all_reminders())[-1]
    assert json.loads(rem["repeat_config"]) == {"every": 7, "start": day.isoformat()}


async def test_days_n1_is_every_day(send, press, fsm):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await choose_type(send, press, state, "daily")
    n = await say(send, state, "1")
    assert n.answered[-1].text == "🔃 <b>Каждый день</b>"


async def test_days_start_today_with_passed_time_moves_by_n(send, press, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await choose_type(send, press, state, "daily")
    n = await say(send, state, "7")
    day = today_utc()
    await tap(press, state, n.answered[-1], date_data(day))
    done = await say(send, state, "0000")  # сегодня 00:00 уже прошло

    assert await run_at_of_last() == datetime.combine(day + timedelta(days=7), time(0, 0), tzinfo=UTC)
    assert f"🕰 Следующее: {fmt(main_module, day + timedelta(days=7))} в 00:00" in done.answered[-1].text


@pytest.mark.parametrize("typed", ["0", "366", "abc"])
async def test_wrong_days_number_is_rejected(send, press, fsm, typed):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await choose_type(send, press, state, "daily")
    reply = await say(send, state, typed)
    assert reply.answered[-1].text == "❌ Введите число от 1 до 365."


async def test_days_wrong_time_short_error(send, press, fsm):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await choose_type(send, press, state, "daily")
    n = await say(send, state, "2")
    await tap(press, state, n.answered[-1], date_data(tomorrow_utc()))
    reply = await say(send, state, "25:00")
    assert reply.answered[-1].text == TIME_ERROR
    assert reply_rows(markup_of(reply.answered[-1])) == TIME_ROWS


# ---------- 📆 Каждую неделю ----------

async def test_weekly_flow(send, press, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    menu = await choose_type(send, press, state, "weekly", text="спортзал")
    days_msg = menu.answered[-1]
    assert days_msg.text == "📆 <b>Каждую неделю</b>\nВыберите дни недели для повторения."
    assert inline_texts(markup_of(days_msg))[-1] == "✔ Подтвердить выбор"

    for data in ["weekday_wed", "weekday_mon"]:
        await tap(press, state, days_msg, data)
    _, _, kwargs = days_msg.sent[-1]
    assert "✅ Пн" in inline_texts(kwargs["reply_markup"]) and "✅ Ср" in inline_texts(kwargs["reply_markup"])
    await tap(press, state, days_msg, "weekday_done")
    assert days_msg.is_deleted
    time_q = days_msg.answered[-1]
    assert time_q.text == ("📆 <b>Каждую неделю: Пн, Ср</b>\n"
                           "Укажите время напоминания из предложенного или введите своё время (пример: 1015).")
    assert reply_rows(markup_of(time_q)) == TIME_ROWS

    done = await say(send, state, "2000")
    run_at = await run_at_of_last()
    assert done.answered[-1].text == ("✅ <b>Напоминание установлено</b>\n📝 спортзал\n📆 Каждую неделю: Пн, Ср\n"
                                      f"🕰 Следующее: {fmt(main_module, run_at.date())} в 20:00")
    assert run_at.weekday() in (0, 2)
    assert json.loads((await all_reminders())[-1]["repeat_config"]) == {"weekdays": ["mon", "wed"]}


async def test_weekly_requires_a_day(send, press, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    menu = await choose_type(send, press, state, "weekly")
    cb = await tap(press, state, menu.answered[-1], "weekday_done")
    assert cb.answers[-1] == ("❌ Выберите хотя бы один день!", True)
    assert not menu.answered[-1].is_deleted
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_weekdays.state


# ---------- 📆 Каждый месяц ----------

async def to_month_mode(send, press, state, text="Оплатить аренду"):
    menu = await choose_type(send, press, state, "monthly", text=text)
    return menu.answered[-1]


async def test_month_mode_step(send, press, fsm):
    await make_user(1, tz="UTC")
    mode_msg = await to_month_mode(send, press, fsm(1))
    assert mode_msg.text == "📆 <b>Каждый месяц</b>\nКак напоминать каждый месяц?"
    assert inline_datas(markup_of(mode_msg)) == ["monthmode_days", "monthmode_last", "monthmode_before"]
    assert inline_texts(markup_of(mode_msg)) == [
        "📅 В указанные дни месяца", "📆 В последний день месяца", "⏳ За N дней до конца месяца"
    ]


def next_month_day_run(days, at, now):
    """Ожидаемое ближайшее срабатывание (UTC) для дней месяца."""
    y, m = now.year, now.month
    for _ in range(3):
        last = calendar.monthrange(y, m)[1]
        for d in sorted({min(x, last) for x in days}):
            cand = datetime.combine(date(y, m, d), at, tzinfo=UTC)
            if cand > now:
                return cand
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


async def test_month_days_flow(send, press, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    mode_msg = await to_month_mode(send, press, state)
    await tap(press, state, mode_msg, "monthmode_days")
    assert mode_msg.is_deleted
    days_msg = mode_msg.answered[-1]
    assert days_msg.text == (
        "📆 <b>Каждый месяц</b>\nВыберите дни месяца.\nМожно выбрать несколько дней.\n"
        "Когда закончите — нажмите «Подтвердить выбор».\n"
        "ℹ️ Если выбранный день отсутствует в месяце (например, 29 или 30), "
        "напоминание придёт в последний день этого месяца."
    )
    assert inline_datas(markup_of(days_msg)) == [f"mday_{d}" for d in range(1, 32)] + ["mday_done"]

    for data in ["mday_29", "mday_5", "mday_done"]:
        await tap(press, state, days_msg, data)
    assert days_msg.is_deleted
    time_q = days_msg.answered[-1]
    assert time_q.text == f"📆 <b>Каждый месяц, 5 и 29 числа</b>\n{TIME_HINT}"

    done = await say(send, state, "0900")
    expected = next_month_day_run([5, 29], time(9, 0), datetime.now(UTC))
    assert await run_at_of_last() == expected
    assert done.answered[-1].text == (
        "✅ <b>Напоминание установлено</b>\n📝 Оплатить аренду\n📆 Каждый месяц, 5 и 29 числа\n"
        "ℹ️ В месяцах без указанного числа напоминание придёт в последний день\n"
        f"🕰 Следующее: {fmt(main_module, expected.date())} в 09:00"
    )
    assert json.loads((await all_reminders())[-1]["repeat_config"]) == {"days": [5, 29]}


async def test_month_days_without_long_days_has_no_note(send, press, fsm):
    await make_user(1, tz="UTC")
    state = fsm(1)
    mode_msg = await to_month_mode(send, press, state)
    await tap(press, state, mode_msg, "monthmode_days")
    days_msg = mode_msg.answered[-1]
    for data in ["mday_1", "mday_15", "mday_done"]:
        await tap(press, state, days_msg, data)
    assert days_msg.answered[-1].text.startswith("📆 <b>Каждый месяц, 1 и 15 числа</b>\n")
    done = await say(send, state, "0900")
    assert "ℹ️" not in done.answered[-1].text


async def test_month_days_toggle_marks_selected(send, press, fsm):
    await make_user(1, tz="UTC")
    state = fsm(1)
    mode_msg = await to_month_mode(send, press, state)
    await tap(press, state, mode_msg, "monthmode_days")
    days_msg = mode_msg.answered[-1]
    await tap(press, state, days_msg, "mday_7")
    _, _, kwargs = days_msg.sent[-1]
    assert "✅ 7" in inline_texts(kwargs["reply_markup"])


async def test_month_days_require_selection(send, press, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    mode_msg = await to_month_mode(send, press, state)
    await tap(press, state, mode_msg, "monthmode_days")
    cb = await tap(press, state, mode_msg.answered[-1], "mday_done")
    assert cb.answers[-1] == ("❌ Выберите хотя бы один день!", True)
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_month_days.state


async def test_month_last_day_flow(send, press, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    mode_msg = await to_month_mode(send, press, state, text="Оплатить коммуналку")
    await tap(press, state, mode_msg, "monthmode_last")
    assert mode_msg.is_deleted
    time_q = mode_msg.answered[-1]
    assert time_q.text == f"📆 <b>Каждый месяц, последний день</b>\n{TIME_HINT}"
    assert reply_rows(markup_of(time_q)) == TIME_ROWS

    done = await say(send, state, "1900")
    expected = next_month_day_run([31], time(19, 0), datetime.now(UTC))
    assert await run_at_of_last() == expected
    assert done.answered[-1].text == (
        "✅ <b>Напоминание установлено</b>\n📝 Оплатить коммуналку\n📆 Каждый месяц, последний день\n"
        f"🕰 Следующее: {fmt(main_module, expected.date())} в 19:00"
    )
    assert json.loads((await all_reminders())[-1]["repeat_config"]) == {"last": True}


async def test_month_before_end_flow(send, press, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    mode_msg = await to_month_mode(send, press, state, text="Подготовить отчёт")
    await tap(press, state, mode_msg, "monthmode_before")
    ask_n = mode_msg.answered[-1]
    assert ask_n.text == ("📆 <b>Каждый месяц, за N дней до конца месяца</b>\n"
                          "Укажите, за сколько дней до конца месяца напоминать.\nВыберите число ниже или введите своё.")
    assert reply_rows(markup_of(ask_n)) == [["1", "2", "3", "5"], ["7", "10", "14"], [CANCEL]]

    n = await say(send, state, "3")
    assert n.answered[-1].text == f"📆 <b>Каждый месяц, за 3 дня до конца месяца</b>\n{TIME_HINT}"

    done = await say(send, state, "1800")
    now = datetime.now(UTC)
    y, m = now.year, now.month
    expected = None
    for _ in range(2):
        cand = datetime.combine(date(y, m, calendar.monthrange(y, m)[1] - 3), time(18, 0), tzinfo=UTC)
        if cand > now:
            expected = cand
            break
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    assert await run_at_of_last() == expected
    assert done.answered[-1].text == (
        "✅ <b>Напоминание установлено</b>\n📝 Подготовить отчёт\n📆 Каждый месяц, за 3 дня до конца месяца\n"
        f"🕰 Следующее: {fmt(main_module, expected.date())} в 18:00"
    )
    assert json.loads((await all_reminders())[-1]["repeat_config"]) == {"before_end": 3}


@pytest.mark.parametrize("typed", ["0", "29", "abc"])
async def test_month_before_end_wrong_number(send, press, fsm, main_module, typed):
    await make_user(1, tz="UTC")
    state = fsm(1)
    mode_msg = await to_month_mode(send, press, state)
    await tap(press, state, mode_msg, "monthmode_before")
    reply = await say(send, state, typed)
    assert reply.answered[-1].text == "❌ Введите число от 1 до 28."
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_month_offset.state


async def test_month_before_end_accepts_28(send, press, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    mode_msg = await to_month_mode(send, press, state)
    await tap(press, state, mode_msg, "monthmode_before")
    reply = await say(send, state, "28")
    assert reply.answered[-1].text.startswith("📆 <b>Каждый месяц, за 28 дней до конца месяца</b>")
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_time.state


async def test_month_wrong_time_short_error(send, press, fsm):
    await make_user(1, tz="UTC")
    state = fsm(1)
    mode_msg = await to_month_mode(send, press, state)
    await tap(press, state, mode_msg, "monthmode_last")
    reply = await say(send, state, "9999")
    assert reply.answered[-1].text == TIME_ERROR


# ---------- Отмена ----------

STOPS = ["text", "type", "interval", "date", "weekdays", "month_days", "time"]


async def go_to(send, press, state, stop):
    """Дойти до шага stop."""
    if stop == "text":
        await open_menu(send, state)
    elif stop == "type":
        await to_type_menu(send, state)
    elif stop == "interval":
        await choose_type(send, press, state, "daily")
    elif stop == "date":
        await choose_type(send, press, state, "once")
    elif stop == "weekdays":
        await choose_type(send, press, state, "weekly")
    elif stop == "month_days":
        mode_msg = await to_month_mode(send, press, state)
        await tap(press, state, mode_msg, "monthmode_days")
    else:
        menu = await choose_type(send, press, state, "once")
        await tap(press, state, menu.answered[-1], date_data(tomorrow_utc()))


@pytest.mark.parametrize("stop", STOPS)
async def test_cancel_works_on_any_step(send, press, fsm, stop):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await go_to(send, press, state, stop)
    cancel = await say(send, state, CANCEL)
    assert await state.get_state() is None
    assert cancel.answered[-1].text == CANCELED
    assert reply_rows(markup_of(cancel.answered[-1])) == MAIN_MENU
    assert await all_reminders() == []


async def test_cancel_outside_flow_returns_menu(send, fsm):
    """Вне диалога (бот перезапускался и забыл его) «❌ Отмена» тоже возвращает главное меню."""
    await make_user(1, tz="UTC")
    state = fsm(1)
    cancel = await say(send, state, CANCEL)
    assert await state.get_state() is None
    assert reply_rows(markup_of(cancel.answered[-1])) == MAIN_MENU


async def test_inline_cancel_button_from_old_versions(send, press, fsm):
    await make_user(1, tz="UTC")
    state = fsm(1)
    menu = await choose_type(send, press, state, "once")
    cb = await tap(press, state, menu.answered[-1], "cancel_flow")
    assert await state.get_state() is None
    assert cb.answers
    assert menu.answered[-1].answered[-1].text == CANCELED


@pytest.mark.parametrize("started_by", ["button", "text", "photo", "forward"])
@pytest.mark.parametrize("repeat", ["once", "hourly", "daily", "weekly", "monthly"])
async def test_cancel_on_keyboard_during_whole_creation(send, press, fsm, main_module, repeat, started_by):
    """С «➕» или сразу текстом / вложением / пересланным — на каждом шаге внизу «❌ Отмена»
    вместо кнопок главного меню; с итогом внизу снова главное меню."""
    await make_user(1, tz="UTC")
    state = fsm(1)
    start_msg = FakeMessage(1, text="/start")
    await main_module.cmd_start(start_msg, state)
    users = [start_msg]

    def check():
        keyboard = visible_keyboard(bot_messages(*users))
        assert keyboard and [CANCEL] in keyboard, keyboard

    if started_by == "button":
        users.append(await open_menu(send, state))
        check()
        users.append(await say(send, state, "купить хлеб"))
    elif started_by == "text":
        users.append(await say(send, state, "купить хлеб"))
    elif started_by == "photo":
        users.append(await say(send, state, photo=[SimpleNamespace(file_id="p")], caption="хлеб"))
    else:
        users.append(await say(send, state, "купить хлеб", forward_origin=SimpleNamespace(type="user")))
    check()
    menu = users[-1].answered[-1]
    await tap(press, state, menu, f"repeat_{repeat}")
    check()
    step = menu.answered[-1]
    if repeat == "once":
        await tap(press, state, step, date_data(tomorrow_utc()))
    elif repeat in ("hourly", "daily"):
        users.append(await say(send, state, "3"))
        check()
        if repeat == "daily":
            await tap(press, state, users[-1].answered[-1], date_data(tomorrow_utc()))
    elif repeat == "weekly":
        await tap(press, state, step, "weekday_mon")
        await tap(press, state, step, "weekday_done")
    else:
        await tap(press, state, step, "monthmode_last")
    check()
    users.append(await say(send, state, "1000"))
    assert "Напоминание установлено" in users[-1].answered[-1].text
    assert visible_keyboard(bot_messages(*users)) == MAIN_MENU


async def test_only_messages_with_buttons_inside_disappear(send, press, fsm):
    """Как в прошлой версии: удаляются типы, календарь, дни недели, режим и дни месяца; остальное остаётся."""
    await make_user(1, tz="UTC")
    state = fsm(1)
    start = await open_menu(send, state)
    content = await say(send, state, "x")
    menu = content.answered[-1]
    await tap(press, state, menu, "repeat_daily")
    numbers_q = menu.answered[-1]
    n = await say(send, state, "2")
    cancel_note, calendar_msg = n.answered
    await tap(press, state, calendar_msg, date_data(tomorrow_utc()))
    time_q = calendar_msg.answered[-1]
    await say(send, state, "1000")

    assert menu.is_deleted and calendar_msg.is_deleted
    assert not any(m.is_deleted for m in (start.answered[0], numbers_q, cancel_note, time_q))
    assert not start.is_deleted and not content.is_deleted  # сообщения пользователя не трогаем


# ---------- Вложения ----------

async def test_photo_with_caption_becomes_reminder(send, press, fsm):
    await make_user(1, tz="UTC")
    state = fsm(1)
    photo = [SimpleNamespace(file_id="small"), SimpleNamespace(file_id="big")]
    menu = await choose_type(send, press, state, "once", photo=photo, caption="рецепт")
    await tap(press, state, menu.answered[-1], date_data(tomorrow_utc()))
    done = await say(send, state, "1200")

    assert "\n📝 рецепт\n" in done.answered[-1].text  # как в прошлой версии — просто подпись
    rem = (await all_reminders())[-1]
    assert (rem["content_type"], rem["file_id"], rem["text"]) == ("photo", "big", "рецепт")


@pytest.mark.parametrize("kind", ["voice", "document", "video", "audio", "video_note", "sticker", "animation"])
async def test_other_attachments_become_reminders(send, press, fsm, kind):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await choose_type(send, press, state, "hourly", **{kind: SimpleNamespace(file_id=f"id-{kind}")})
    await say(send, state, "1")
    done = await say(send, state, "📍 С текущего момента")

    assert "\n📝 📎 Вложение\n" in done.answered[-1].text
    rem = (await all_reminders())[-1]
    assert (rem["content_type"], rem["file_id"], rem["text"]) == (kind, f"id-{kind}", "")


async def test_gif_is_saved_as_animation_not_document(send, press, fsm):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await choose_type(send, press, state, "hourly",
                      animation=SimpleNamespace(file_id="gif"), document=SimpleNamespace(file_id="gif-doc"))
    await say(send, state, "1")
    await say(send, state, "📍 С текущего момента")
    assert (await all_reminders())[-1]["content_type"] == "animation"


async def test_confirmation_is_bold_html_and_escapes_text(send, press, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    menu = await choose_type(send, press, state, "once", text="<купить> & хлеб")
    day = tomorrow_utc()
    await tap(press, state, menu.answered[-1], date_data(day))
    done = (await say(send, state, "2100")).answered[-1]

    assert done.kwargs.get("parse_mode") == "HTML"
    assert done.text == ("✅ <b>Напоминание установлено</b>\n📝 &lt;купить&gt; &amp; хлеб\n🗓 Однократно\n"
                         f"🕰 {fmt(main_module, day)} в 21:00")


# ---------- Ввод времени ----------

async def start_time_step(state, main_module, **data):
    await state.update_data(text="x", **data)
    await state.set_state(main_module.ReminderFlow.waiting_for_time)


@pytest.mark.parametrize("typed,expected", [
    ("1430", "14:30"), ("14:30", "14:30"), ("14.30", "14:30"), ("14 30", "14:30"),
    ("0930", "09:30"), ("930", "09:30"), ("9:05", "09:05"), ("00:00", "00:00"),
    ("2359", "23:59"), ("  1430 ", "14:30"), ("08:00", "08:00"),
])
async def test_time_can_be_typed(send, fsm, main_module, typed, expected):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await start_time_step(state, main_module, repeat_type="daily", interval=1)
    await send(FakeMessage(1, text=typed), state)
    assert (await run_at_of_last()).strftime("%H:%M") == expected


@pytest.mark.parametrize("typed", ["2400", "1260", "14", "abc", "14:3", "143000", "25:00", "-1430"])
async def test_wrong_time_is_rejected(send, fsm, main_module, typed):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await start_time_step(state, main_module, repeat_type="daily", interval=1)
    msg = await say(send, state, typed)
    assert await all_reminders() == []
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_time.state
    assert msg.answered[-1].text == TIME_ERROR


# ---------- /start и приостановленные напоминания ----------

async def test_start_with_timezone(fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await state.set_state(main_module.ReminderFlow.waiting_for_text)
    msg = FakeMessage(1, text="/start")
    await main_module.cmd_start(msg, state)
    assert msg.answered[-1].text == "✅ Часовой пояс уже установлен.\nВыберите действие:"
    assert reply_rows(markup_of(msg.answered[-1])) == MAIN_MENU
    assert await state.get_state() is None


async def test_start_without_timezone(fsm, main_module):
    msg = FakeMessage(1, text="/start")
    await main_module.cmd_start(msg, fsm(1))
    assert msg.answered[-1].text == "👋 Привет! Чтобы напоминания приходили вовремя, укажите ваш часовой пояс."
    assert inline_datas(markup_of(msg.answered[-1])) == ["tz_geo", "tz_manual"]


@pytest.mark.parametrize("count,phrase", [
    (1, "У вас 1 приостановленное напоминание. Что сделать?"),
    (2, "У вас 2 приостановленных напоминания. Что сделать?"),
    (5, "У вас 5 приостановленных напоминаний. Что сделать?"),
])
async def test_start_after_unblocking_offers_paused(fsm, main_module, count, phrase):
    uid = await make_user(1, tz="UTC")
    for i in range(count):
        await storage.create_reminder(uid, datetime.now(UTC) + timedelta(days=1), f"дело {i}")
    await storage.block_user_and_pause(uid)

    msg = FakeMessage(1, text="/start")
    await main_module.cmd_start(msg, fsm(1))
    assert not await storage.is_user_blocked(1)
    assert msg.answered[-1].text == phrase
    assert inline_datas(markup_of(msg.answered[-1])) == ["blocked_resume_all", "blocked_delete_all"]


async def test_resume_all_moves_missed_reminders_to_future(press, fsm):
    uid = await make_user(1, tz="UTC")
    now = datetime.now(UTC).replace(microsecond=0)
    past_once = await storage.create_reminder(uid, now - timedelta(days=2), "разовое")
    past_daily = await storage.create_reminder(uid, now - timedelta(days=2, hours=1), "каждый день",
                                               repeat_type="daily", repeat_config=json.dumps({"every": 1}))
    future = await storage.create_reminder(uid, now + timedelta(days=3), "потом")
    await storage.block_user_and_pause(uid)

    choice = FakeMessage(1, text="У вас 3 приостановленных напоминания. Что сделать?")
    cb = await tap(press, fsm(1), choice, "blocked_resume_all")

    assert choice.sent[0][:2] == ("edit_text", "✅ Возобновлено напоминаний: 3")
    assert choice.answered[-1].text == "Выберите действие:"
    assert reply_rows(markup_of(choice.answered[-1])) == MAIN_MENU
    assert cb.answers
    rems = {r["id"]: r for r in await all_reminders()}
    assert all(r["status"] == "pending" for r in rems.values())

    def run_at(rid):
        return datetime.fromisoformat(rems[rid]["run_at"]).replace(tzinfo=UTC)

    assert now < run_at(past_once) <= now + timedelta(minutes=2)
    assert now < run_at(past_daily) <= now + timedelta(days=1)
    assert run_at(past_daily).time() == (now - timedelta(hours=1)).time()  # то же время дня
    assert run_at(future) == now + timedelta(days=3)


async def test_delete_all_paused(press, fsm):
    uid = await make_user(1, tz="UTC")
    for i in range(2):
        await storage.create_reminder(uid, datetime.now(UTC) + timedelta(days=1), f"дело {i}")
    await storage.block_user_and_pause(uid)
    choice = FakeMessage(1, text="…")
    await tap(press, fsm(1), choice, "blocked_delete_all")
    assert choice.sent[0][:2] == ("edit_text", "🗑 Удалено напоминаний: 2")
    assert await all_reminders() == []


# ---------- Часовой пояс и «⚙️ Настройки» ----------

async def test_settings_show_timezone_and_change_options(send, press, fsm, main_module):
    await make_user(1, tz="Europe/Moscow")
    state = fsm(1)
    await open_menu(send, state)  # посреди создания — «⚙️ Настройки» его прекращают
    msg = await say(send, state, "⚙️ Настройки")
    assert msg.answered[-1].text == "🕗 Текущий часовой пояс: Europe/Moscow\nХотите изменить его?"
    assert inline_datas(markup_of(msg.answered[-1])) == ["tz_geo", "tz_manual"]
    assert await state.get_state() is None

    by_command = FakeMessage(1, text="/timezone")
    await main_module.cmd_timezone(by_command, state)
    assert by_command.answered[-1].text == msg.answered[-1].text


async def test_settings_without_timezone(send, fsm):
    await storage.get_or_create_user(1)
    msg = await say(send, fsm(1), "⚙️ Настройки")
    assert msg.answered[-1].text == "🕗 Часовой пояс не установлен.\nВыберите способ:"


async def test_manual_timezone_is_chosen_from_list(press, fsm):
    await storage.get_or_create_user(1)
    state = fsm(1)
    choice = FakeMessage(1, text="👋 Привет!")
    await tap(press, state, choice, "tz_manual")
    kind, text, kwargs = choice.sent[-1]
    assert (kind, text) == ("edit_text", "🌍 Выберите часовой пояс (отсортировано по UTC):")
    assert "tz_cancel" in inline_datas(kwargs["reply_markup"])

    await tap(press, state, choice, "tz_page_1")
    assert choice.sent[-1][0] == "edit_reply_markup"

    await tap(press, state, choice, "tz_select_Europe/Moscow")
    assert await storage.get_user_timezone(1) == "Europe/Moscow"
    assert choice.is_deleted
    assert choice.answered[-1].text == "🕗 Часовой пояс установлен: Europe/Moscow\nВыберите действие:"
    assert reply_rows(markup_of(choice.answered[-1])) == MAIN_MENU


async def test_unknown_timezone_is_refused(press, fsm):
    await storage.get_or_create_user(1)
    cb = await tap(press, fsm(1), FakeMessage(1), "tz_select_Mars/Olympus")
    assert cb.answers[-1] == ("❌ Неверный часовой пояс.", True)
    assert await storage.get_user_timezone(1) is None


async def test_timezone_choice_can_be_canceled(press, fsm):
    choice = FakeMessage(1)
    await tap(press, fsm(1), choice, "tz_cancel")
    assert choice.is_deleted
    assert choice.answered[-1].text == "🕗 Выбор часового пояса отменён.\nВыберите действие:"
    assert reply_rows(markup_of(choice.answered[-1])) == MAIN_MENU


async def test_geo_timezone_is_saved(send, press, fsm):
    await storage.get_or_create_user(1)
    state = fsm(1)
    choice = FakeMessage(1, text="👋 Привет!")
    await tap(press, state, choice, "tz_geo")
    assert choice.sent[0][:2] == ("edit_text", "📍 Отправьте вашу геолокацию, нажав на кнопку ниже:")
    geo_prompt = choice.answered[-1]
    assert geo_prompt.text == "Нажмите кнопку для отправки геолокации:"
    assert any(b.request_location for row in markup_of(geo_prompt).keyboard for b in row)

    done = await say(send, state, location=SimpleNamespace(latitude=55.75, longitude=37.62))
    assert await storage.get_user_timezone(1) == "Europe/Moscow"
    assert done.answered[-1].text == "🕗 Ваш часовой пояс: Europe/Moscow\nВыберите действие:"
    assert reply_rows(markup_of(done.answered[-1])) == MAIN_MENU


async def test_geo_in_ocean_offers_other_way(send, fsm, main_module, monkeypatch):
    monkeypatch.setattr(main_module.tf, "timezone_at", lambda lat, lng: None)
    msg = await say(send, fsm(1), location=SimpleNamespace(latitude=0.0, longitude=-160.0))
    error, choice = msg.answered
    assert error.text == "❌ Не удалось определить часовой пояс. Попробуйте другой способ."
    assert isinstance(markup_of(error), ReplyKeyboardRemove)
    assert choice.text == "Выберите способ:"
    assert inline_datas(markup_of(choice)) == ["tz_geo", "tz_manual"]


# ---------- /admin_stats ----------

async def test_admin_stats_only_for_admins(fsm, main_module, monkeypatch):
    uid = await make_user(1, tz="UTC")
    await storage.create_reminder(uid, datetime.now(UTC) + timedelta(days=1), "x")
    await make_user(2, tz="UTC")
    monkeypatch.setenv("ADMIN_IDS", "1, 99")

    admin = FakeMessage(1, text="/admin_stats")
    await main_module.admin_stats(admin)
    assert admin.answered[-1].text == ("📊 Статистика бота\n👥 Пользователей: 2\n🔔 Напоминаний: 1\n"
                                       "📈 Среднее напоминаний на пользователя: 0.50")
    stranger = FakeMessage(2, text="/admin_stats")
    await main_module.admin_stats(stranger)
    assert stranger.answered == []


# ---------- «Мои напоминания» как в прошлой версии ----------

@pytest.mark.parametrize("repeat_type,config,first_line,rule", [
    ("once", None, "📆 <b>Напоминание</b>", "🔄 Однократно"),
    ("hourly", {"every": 3}, "🔄🕰 <b>Напоминание</b>", "🔄 Каждые 3 часа"),
    ("daily", {"every": 7, "start": "2026-12-01"}, "🔄📆 <b>Напоминание</b>", "🔄 Каждые 7 дней"),
    ("weekly", {"weekdays": ["mon", "wed"]}, "🔄📅 <b>Напоминание</b>", "🔄 Каждую неделю\n📅 Дни: Пн, Ср"),
    ("monthly", {"days": [5, 29]}, "📆 <b>Напоминание</b>", "🔄 Каждый месяц, 5 и 29 числа"),
    ("monthly", {"last": True}, "📆 <b>Напоминание</b>", "🔄 Каждый месяц, последний день"),
    ("monthly", {"before_end": 3}, "📆 <b>Напоминание</b>", "🔄 Каждый месяц, за 3 дня до конца"),
])
async def test_list_item_format(send, fsm, repeat_type, config, first_line, rule):
    uid = await make_user(1, tz="UTC")
    at = (datetime.now(UTC) + timedelta(days=2)).replace(second=0, microsecond=0)
    await storage.create_reminder(uid, at, "дело", repeat_type=repeat_type,
                                  repeat_config=json.dumps(config) if config else None)
    msg = await say(send, fsm(1), "📋 Мои напоминания")
    assert msg.answered[-1].text == f"{first_line}\n📝 дело\n🕐 {at:%d.%m.%Y %H:%M}\n{rule}"


async def test_list_item_with_attachment(send, fsm):
    uid = await make_user(1, tz="UTC")
    at = (datetime.now(UTC) + timedelta(days=2)).replace(second=0, microsecond=0)
    await storage.create_reminder(uid, at, "", content_type="photo", file_id="p")
    msg = await say(send, fsm(1), "📋 Мои напоминания")
    assert msg.answered[-1].text == (f"📆 <b>Напоминание</b>\n📝 📎 Вложение\n🕐 {at:%d.%m.%Y %H:%M}\n"
                                     "🔄 Однократно\n📎 Вложения: photo (1)")


async def test_empty_list(send, fsm):
    await make_user(1, tz="UTC")
    msg = await say(send, fsm(1), "📋 Мои напоминания")
    assert msg.answered[-1].text == "📭 Нет активных напоминаний.\nСоздайте новое!"
