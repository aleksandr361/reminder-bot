"""Диалог создания напоминания по ТЗ:
тип повторения → текст или вложение → параметры типа → время → «✅ Напоминание установлено».
Каждый шаг начинается с уже выбранного правила; «❌ Отмена» всегда в клавиатуре.
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
TIME_INSTRUCTION = "Во сколько напоминать?\nВыберите время ниже или введите вручную (например: 1015)."


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


def shows_cancel(messages):
    """Среди ответов бота есть нижняя клавиатура с «❌ Отмена»."""
    return any(isinstance(markup_of(m), ReplyKeyboardMarkup)
               and [CANCEL] in reply_rows(markup_of(m)) for m in messages)


def visible_keyboard(bot, messages):
    """Какая нижняя клавиатура сейчас видна в Telegram.
    Её задаёт последнее сообщение с клавиатурой; если его удалили — клавиатуры нет."""
    with_kb = [m for m in messages if isinstance(markup_of(m), (ReplyKeyboardMarkup, ReplyKeyboardRemove))]
    if not with_kb:
        return None
    latest = max(with_kb, key=lambda m: m.message_id)
    if latest.message_id in {mid for _, mid in bot.deleted} or isinstance(markup_of(latest), ReplyKeyboardRemove):
        return None
    return reply_rows(markup_of(latest))


def run_at_of_last():
    async def _get():
        return datetime.fromisoformat((await all_reminders())[-1]["run_at"]).replace(tzinfo=UTC)
    return _get()


async def open_menu(send, state):
    """«➕ Новое напоминание». Возвращает сообщение пользователя (ответы бота — в .answered)."""
    start = FakeMessage(1, text="➕ Новое напоминание")
    await send(start, state)
    return start


async def choose_type(press, state, menu_msg, repeat):
    await press(FakeCallback(f"repeat_{repeat}", menu_msg), state)
    return menu_msg.answered[-1]


async def say(send, state, text=None, **attachment):
    msg = FakeMessage(1, text=text)
    for key, value in attachment.items():
        setattr(msg, key, value)
    await send(msg, state)
    return msg


async def flow_to_content(send, press, state, repeat, text="купить хлеб"):
    """Меню → тип → текст. Возвращает сообщение пользователя с текстом."""
    start = await open_menu(send, state)
    await choose_type(press, state, start.answered[-1], repeat)
    return await say(send, state, text)


def today_utc():
    return datetime.now(UTC).date()


def tomorrow_utc():
    return today_utc() + timedelta(days=1)


def fmt(main_module, d):
    return main_module.format_date(d)


def step_text(msg):
    """Все ответы бота на действие одним текстом (правило и задание могут прийти двумя сообщениями)."""
    return "\n\n".join(m.text for m in msg.answered)


def deleted_ids(bot):
    return {mid for _, mid in bot.deleted}


# ---------- 1. Меню ----------

async def test_menu_has_five_types_and_cancel_keyboard(send, fsm):
    await make_user(1, tz="UTC")
    start = await open_menu(send, fsm(1))

    header, menu = start.answered  # шапка с «❌ Отмена» на клавиатуре и меню с кнопками
    assert header.text == "🆕 Новое напоминание"
    assert reply_rows(markup_of(header)) == [[CANCEL]]
    assert menu.text == "Выберите тип повторения:"
    assert inline_datas(markup_of(menu)) == [
        "repeat_once", "repeat_hourly", "repeat_daily", "repeat_weekly", "repeat_monthly"
    ]
    assert inline_texts(markup_of(menu)) == [
        "🗓 Однократно", "🔁 Каждые N часов", "🔁 Каждые N дней", "📆 Каждую неделю", "📆 Каждый месяц"
    ]
    assert all(len(row) == 1 for row in markup_of(menu).inline_keyboard)


async def test_after_type_bot_asks_content_with_rule(send, press, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    start = await open_menu(send, state)
    prompt = await choose_type(press, state, start.answered[-1], "once")

    assert prompt.text == "🗓 Однократно\n\nОтправьте текст или вложение для напоминания:"
    assert markup_of(prompt) is None  # нижняя клавиатура «❌ Отмена» остаётся от шапки
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_text.state


async def test_type_can_be_changed_while_asked_for_text(send, press, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    start = await open_menu(send, state)
    await choose_type(press, state, start.answered[-1], "once")
    prompt = await choose_type(press, state, start.answered[-1], "weekly")

    assert prompt.text.startswith("📆 Каждую неделю")
    assert (await state.get_data())["repeat_type"] == "weekly"


# ---------- Отмена ----------

@pytest.mark.parametrize("steps", [
    [],                       # ждём текст
    ["купить хлеб"],          # календарь
    ["купить хлеб", "date"],  # время
])
async def test_cancel_works_on_any_step(send, press, fsm, steps):
    await make_user(1, tz="UTC")
    state = fsm(1)
    start = await open_menu(send, state)
    await choose_type(press, state, start.answered[-1], "once")
    last = None
    for step in steps:
        if step == "date":
            d = tomorrow_utc()
            await press(FakeCallback(f"date_1_{d.year}_{d.month}_{d.day}", last.answered[-1]), state)
        else:
            last = await say(send, state, step)

    cancel = await say(send, state, CANCEL)
    assert await state.get_state() is None
    assert "отменено" in cancel.answered[-1].text.lower()
    assert ["➕ Новое напоминание"] in reply_rows(markup_of(cancel.answered[-1]))
    assert await all_reminders() == []


async def test_cancel_outside_flow_just_shows_menu(send, fsm):
    await make_user(1, tz="UTC")
    state = fsm(1)
    cancel = await say(send, state, CANCEL)
    assert await state.get_state() is None
    assert ["➕ Новое напоминание"] in reply_rows(markup_of(cancel.answered[-1]))


# ---------- 3. Каждые N часов ----------

async def test_hours_number_step(send, press, fsm):
    await make_user(1, tz="UTC")
    content = await flow_to_content(send, press, fsm(1), "hourly")
    prompt = content.answered[-1]

    assert prompt.text == ("🔁 Каждые N часов\n\nКак часто напоминать?\n"
                           "Выберите количество часов из предложенных ниже или введите своё число.")
    assert reply_rows(markup_of(prompt)) == [["1", "2", "3", "4"], ["6", "8", "12", "24"], [CANCEL]]


async def test_hours_start_step(send, press, fsm):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await flow_to_content(send, press, state, "hourly")
    n = await say(send, state, "3")

    texts = "\n\n".join(m.text for m in n.answered)
    assert texts == ("🔁 Каждые 3 часа\n\nОт какого момента начать отсчёт?\n"
                     "Можно начать с текущего момента, выбрать время ниже или ввести время вручную (например: 1015).")
    assert len(n.answered) == 1
    assert reply_rows(markup_of(n.answered[-1])) == [
        ["📍 С текущего момента"],
        ["08:00", "09:00", "10:00", "11:00"], ["12:00", "13:00", "14:00", "15:00"],
        ["16:00", "17:00", "18:00", "19:00"], ["20:00", "21:00", "22:00", "23:00"], [CANCEL],
    ]


async def test_hours_start_now(send, press, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await flow_to_content(send, press, state, "hourly", text="пить воду")
    await say(send, state, "3")
    before = datetime.now(UTC).replace(second=0, microsecond=0)
    now_msg = await say(send, state, "📍 С текущего момента")

    run_at = await run_at_of_last()
    assert before + timedelta(hours=3) <= run_at <= before + timedelta(hours=3, minutes=1)
    rem = (await all_reminders())[-1]
    assert json.loads(rem["repeat_config"]) == {"every": 3}
    final = now_msg.answered[-1].text
    local = run_at
    assert final == ("✅ Напоминание установлено\n\nпить воду\n🔁 Каждые 3 часа\n"
                     f"⏰ Следующее: {fmt(main_module, local.date())} в {local:%H:%M}")
    assert await state.get_state() is None


@pytest.mark.parametrize("offset_minutes", [120, -240, -30])
async def test_hours_start_from_time_counts_from_it(send, press, fsm, offset_minutes):
    """Отсчёт от выбранного времени сегодня: T, T+N, T+2N… — ближайшее в будущем."""
    await make_user(1, tz="UTC")
    state = fsm(1)
    await flow_to_content(send, press, state, "hourly")
    await say(send, state, "3")
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    t = (now + timedelta(minutes=offset_minutes)).time()
    await say(send, state, t.strftime("%H%M"))

    expected = datetime.combine(now.date(), t, tzinfo=UTC)
    while expected <= datetime.now(UTC):
        expected += timedelta(hours=3)
    assert await run_at_of_last() == expected


@pytest.mark.parametrize("typed", ["0", "-2", "abc", "2.5", "1000", ""])
async def test_wrong_hours_number_is_rejected(send, press, fsm, main_module, typed):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await flow_to_content(send, press, state, "hourly")
    reply = await say(send, state, typed)
    assert "число" in reply.answered[-1].text.lower()
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_interval.state


# ---------- 4. Каждые N дней ----------

async def test_days_number_step(send, press, fsm):
    await make_user(1, tz="UTC")
    content = await flow_to_content(send, press, fsm(1), "daily")
    prompt = content.answered[-1]
    assert prompt.text == ("🔁 Каждые N дней\n\nКак часто напоминать?\n"
                           "Выберите количество дней из предложенных ниже или введите своё число.")
    assert reply_rows(markup_of(prompt)) == [["1", "2", "3", "7"], ["10", "14", "21", "30"], [CANCEL]]


async def test_days_full_flow(send, press, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await flow_to_content(send, press, state, "daily", text="Отправить отчёт")
    n = await say(send, state, "7")

    assert n.answered[-1].text == "🔁 Каждые 7 дней\n\nВыберите дату начала:"
    assert any(d.startswith("date_") for d in inline_datas(markup_of(n.answered[-1])))
    assert shows_cancel(n.answered)

    day = tomorrow_utc()
    await press(FakeCallback(f"date_1_{day.year}_{day.month}_{day.day}", n.answered[-1]), state)
    time_prompt = n.answered[-1].answered[-1]
    rule = f"🔁 Каждые 7 дней, начиная с {fmt(main_module, day)}"
    assert time_prompt.text == f"{rule}\n\n{TIME_INSTRUCTION}"

    done = await say(send, state, "1000")
    assert done.answered[-1].text == (f"✅ Напоминание установлено\n\nОтправить отчёт\n{rule}\n"
                                      f"⏰ Следующее: {fmt(main_module, day)} в 10:00")
    rem = (await all_reminders())[-1]
    assert json.loads(rem["repeat_config"]) == {"every": 7, "start": day.isoformat()}


async def test_days_start_today_with_passed_time_moves_by_n(send, press, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await flow_to_content(send, press, state, "daily")
    n = await say(send, state, "7")
    day = today_utc()
    await press(FakeCallback(f"date_1_{day.year}_{day.month}_{day.day}", n.answered[-1]), state)
    done = await say(send, state, "0000")  # сегодня 00:00 уже прошло

    assert await run_at_of_last() == datetime.combine(day + timedelta(days=7), time(0, 0), tzinfo=UTC)
    assert f"⏰ Следующее: {fmt(main_module, day + timedelta(days=7))} в 00:00" in done.answered[-1].text


# ---------- 5. Каждый месяц ----------

async def to_month_mode(send, press, state, text="Оплатить аренду"):
    content = await flow_to_content(send, press, state, "monthly", text=text)
    return content.answered[-1]


async def test_month_mode_step(send, press, fsm):
    await make_user(1, tz="UTC")
    state = fsm(1)
    content = await flow_to_content(send, press, state, "monthly")
    mode_msg = content.answered[-1]
    assert step_text(content) == "📆 Каждый месяц\n\nКак напоминать каждый месяц?"
    assert len(content.answered) == 1
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
    await press(FakeCallback("monthmode_days", mode_msg), state)
    days_msg = mode_msg.answered[-1]

    assert step_text(mode_msg) == (
        "📆 Каждый месяц\n\nВыберите дни месяца.\nМожно выбрать несколько дней.\n"
        "Когда закончите — нажмите «Подтвердить выбор».\n\n"
        "ℹ️ Если выбранный день отсутствует в месяце (например, 29 или 30), "
        "напоминание придёт в последний день этого месяца."
    )
    datas = inline_datas(markup_of(days_msg))
    assert datas == [f"mday_{d}" for d in range(1, 32)] + ["mday_done"]
    assert inline_texts(markup_of(days_msg))[-1] == "✔ Подтвердить выбор"

    for data in ["mday_29", "mday_5", "mday_done"]:
        await press(FakeCallback(data, days_msg), state)
    time_prompt = days_msg.answered[-1]
    assert time_prompt.text == f"📆 Каждый месяц, 5 и 29 числа\n\n{TIME_INSTRUCTION}"

    done = await say(send, state, "0900")
    expected = next_month_day_run([5, 29], time(9, 0), datetime.now(UTC))
    assert await run_at_of_last() == expected
    assert done.answered[-1].text == (
        "✅ Напоминание установлено\n\nОплатить аренду\n📆 Каждый месяц, 5 и 29 числа\n"
        "ℹ️ В месяцах без 29 числа напоминание придёт в последний день\n"
        f"⏰ Следующее: {fmt(main_module, expected.date())} в 09:00"
    )
    assert json.loads((await all_reminders())[-1]["repeat_config"]) == {"days": [5, 29]}


async def test_month_days_toggle_marks_selected(send, press, fsm):
    await make_user(1, tz="UTC")
    state = fsm(1)
    mode_msg = await to_month_mode(send, press, state)
    await press(FakeCallback("monthmode_days", mode_msg), state)
    days_msg = mode_msg.answered[-1]
    await press(FakeCallback("mday_7", days_msg), state)
    _, _, kwargs = days_msg.sent[-1]
    texts = inline_texts(kwargs["reply_markup"])
    assert "7" not in texts and any("7" in t and t != "7" for t in texts)


async def test_month_days_require_selection(send, press, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    mode_msg = await to_month_mode(send, press, state)
    await press(FakeCallback("monthmode_days", mode_msg), state)
    cb = FakeCallback("mday_done", mode_msg.answered[-1])
    await press(cb, state)
    assert cb.answers[-1][1] is True
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_month_days.state


async def test_month_last_day_flow(send, press, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    mode_msg = await to_month_mode(send, press, state, text="Оплатить коммуналку")
    await press(FakeCallback("monthmode_last", mode_msg), state)
    time_prompt = mode_msg.answered[-1]
    assert time_prompt.text == f"📆 Каждый месяц, последний день\n\n{TIME_INSTRUCTION}"

    done = await say(send, state, "1900")
    expected = next_month_day_run([31], time(19, 0), datetime.now(UTC))
    assert await run_at_of_last() == expected
    assert done.answered[-1].text == (
        "✅ Напоминание установлено\n\nОплатить коммуналку\n📆 Каждый месяц, последний день\n"
        f"⏰ Следующее: {fmt(main_module, expected.date())} в 19:00"
    )
    assert json.loads((await all_reminders())[-1]["repeat_config"]) == {"last": True}


async def test_month_before_end_flow(send, press, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    mode_msg = await to_month_mode(send, press, state, text="Подготовить отчёт")
    await press(FakeCallback("monthmode_before", mode_msg), state)
    ask_n = mode_msg.answered[-1]
    assert ask_n.text == ("📆 Каждый месяц\n\nЗа сколько дней до конца месяца напоминать?\n"
                          "Выберите число ниже или введите своё.")
    assert reply_rows(markup_of(ask_n)) == [["1", "2", "3", "5"], ["7", "10", "14"], [CANCEL]]

    n = await say(send, state, "3")
    assert n.answered[-1].text == f"📆 Каждый месяц, за 3 дня до конца месяца\n\n{TIME_INSTRUCTION}"

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
        "✅ Напоминание установлено\n\nПодготовить отчёт\n📆 Каждый месяц, за 3 дня до конца месяца\n"
        f"⏰ Следующее: {fmt(main_module, expected.date())} в 18:00"
    )
    assert json.loads((await all_reminders())[-1]["repeat_config"]) == {"before_end": 3}


@pytest.mark.parametrize("typed", ["0", "28", "abc"])
async def test_month_before_end_wrong_number(send, press, fsm, main_module, typed):
    await make_user(1, tz="UTC")
    state = fsm(1)
    mode_msg = await to_month_mode(send, press, state)
    await press(FakeCallback("monthmode_before", mode_msg), state)
    reply = await say(send, state, typed)
    assert "число" in reply.answered[-1].text.lower()
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_month_offset.state


# ---------- 6. Однократно и каждую неделю ----------

async def test_once_flow(send, press, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    content = await flow_to_content(send, press, state, "once", text="к ортопеду с Андреем")
    calendar_msg = content.answered[-1]
    assert step_text(content) == "🗓 Однократно\n\nВыберите дату:"
    assert any(d.startswith("date_") for d in inline_datas(markup_of(calendar_msg)))

    day = tomorrow_utc()
    await press(FakeCallback(f"date_1_{day.year}_{day.month}_{day.day}", calendar_msg), state)
    time_prompt = calendar_msg.answered[-1]
    assert time_prompt.text == f"🗓 Однократно, {fmt(main_module, day)}\n\n{TIME_INSTRUCTION}"
    assert [CANCEL] in reply_rows(markup_of(time_prompt))

    done = await say(send, state, "2000")
    assert done.answered[-1].text == ("✅ Напоминание установлено\n\nк ортопеду с Андреем\n🗓 Однократно\n"
                                      f"⏰ {fmt(main_module, day)} в 20:00")
    assert ["➕ Новое напоминание"] in reply_rows(markup_of(done.answered[-1]))
    assert (await all_reminders())[-1]["run_at"] == f"{day.isoformat()}T20:00:00"
    assert await state.get_state() is None


async def test_past_date_is_not_accepted(send, press, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    content = await flow_to_content(send, press, state, "once")
    yesterday = today_utc() - timedelta(days=1)
    cb = FakeCallback(f"date_1_{yesterday.year}_{yesterday.month}_{yesterday.day}", content.answered[-1])
    await press(cb, state)
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_date.state
    assert cb.answers[-1][1] is True


async def test_past_time_today_asks_again(send, press, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    content = await flow_to_content(send, press, state, "once")
    day = today_utc()
    await press(FakeCallback(f"date_1_{day.year}_{day.month}_{day.day}", content.answered[-1]), state)
    reply = await say(send, state, "0000")
    assert "прошло" in reply.answered[-1].text
    assert await all_reminders() == []
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_time.state


async def test_weekly_flow(send, press, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    content = await flow_to_content(send, press, state, "weekly", text="спортзал")
    days_msg = content.answered[-1]
    assert step_text(content) == ("📆 Каждую неделю\n\nВыберите дни недели.\nМожно выбрать несколько дней.\n"
                                  "Когда закончите — нажмите «Подтвердить выбор».")
    assert inline_texts(markup_of(days_msg))[-1] == "✔ Подтвердить выбор"

    for data in ["weekday_wed", "weekday_mon", "weekday_done"]:
        await press(FakeCallback(data, days_msg), state)
    time_prompt = days_msg.answered[-1]
    assert time_prompt.text == f"📆 Каждую неделю: Пн, Ср\n\n{TIME_INSTRUCTION}"

    done = await say(send, state, "2000")
    run_at = await run_at_of_last()
    assert done.answered[-1].text == ("✅ Напоминание установлено\n\nспортзал\n📆 Каждую неделю: Пн, Ср\n"
                                      f"⏰ Следующее: {fmt(main_module, run_at.date())} в 20:00")
    assert run_at.weekday() in (0, 2)
    assert json.loads((await all_reminders())[-1]["repeat_config"]) == {"weekdays": ["mon", "wed"]}


# ---------- 7. Текст вне диалога запускает создание ----------

async def test_text_outside_flow_starts_reminder(send, press, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    msg = await say(send, state, "Купить хлеб")

    assert [m.text for m in msg.answered] == ["🆕 Новое напоминание", "Купить хлеб\n\nВыберите тип повторения:"]
    assert shows_cancel(msg.answered)
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_repeat_type.state

    # Текст уже есть — после выбора типа сразу следующий шаг
    prompt = await choose_type(press, state, msg.answered[-1], "hourly")
    assert prompt.text.startswith("🔁 Каждые N часов\n\nКак часто напоминать?")
    await say(send, state, "2")
    await say(send, state, "📍 С текущего момента")
    rem = (await all_reminders())[-1]
    assert (rem["text"], rem["repeat_type"]) == ("Купить хлеб", "hourly")


async def test_commands_and_menu_buttons_do_not_start_reminder(send, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await say(send, state, "/unknown")
    assert await state.get_state() is None
    menu = await say(send, state, "📋 Мои напоминания")
    assert await state.get_state() is None
    assert menu.answered and "напоминаний" in menu.answered[-1].text


async def test_text_inside_flow_belongs_to_current_step(send, press, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await flow_to_content(send, press, state, "daily")
    await say(send, state, "Купить молоко")  # не число — ошибка шага, а не новое напоминание
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_interval.state


async def test_typing_on_button_step_gives_hint(send, press, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await flow_to_content(send, press, state, "once")
    reply = await say(send, state, "завтра")
    assert "кнопк" in reply.answered[-1].text.lower()
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_date.state


async def test_text_outside_flow_without_timezone_asks_for_it(send, fsm):
    await storage.get_or_create_user(1)
    state = fsm(1)
    msg = await say(send, state, "Купить хлеб")
    assert await state.get_state() is None
    assert "часовой пояс" in msg.answered[-1].text.lower()


# ---------- Вложения ----------

async def test_photo_with_caption_becomes_reminder(send, press, fsm):
    await make_user(1, tz="UTC")
    state = fsm(1)
    start = await open_menu(send, state)
    await choose_type(press, state, start.answered[-1], "once")
    photo = [SimpleNamespace(file_id="small"), SimpleNamespace(file_id="big")]
    content = await say(send, state, photo=photo, caption="рецепт")
    day = tomorrow_utc()
    await press(FakeCallback(f"date_1_{day.year}_{day.month}_{day.day}", content.answered[-1]), state)
    done = await say(send, state, "1200")

    assert "🖼 Фото: рецепт" in done.answered[-1].text
    rem = (await all_reminders())[-1]
    assert (rem["content_type"], rem["file_id"], rem["text"]) == ("photo", "big", "рецепт")


@pytest.mark.parametrize("kind,label", [
    ("voice", "🎤 Голосовое"), ("document", "📎 Файл"), ("video", "🎬 Видео"),
    ("audio", "🎵 Аудио"), ("video_note", "📹 Кружок"), ("sticker", "🙂 Стикер"), ("animation", "🎞 GIF"),
])
async def test_other_attachments_become_reminders(send, press, fsm, kind, label):
    await make_user(1, tz="UTC")
    state = fsm(1)
    start = await open_menu(send, state)
    await choose_type(press, state, start.answered[-1], "hourly")
    await say(send, state, **{kind: SimpleNamespace(file_id=f"id-{kind}")})
    await say(send, state, "1")
    done = await say(send, state, "📍 С текущего момента")

    assert label in done.answered[-1].text
    rem = (await all_reminders())[-1]
    assert (rem["content_type"], rem["file_id"], rem["text"]) == (kind, f"id-{kind}", "")


async def test_gif_is_saved_as_animation_not_document(send, press, fsm):
    await make_user(1, tz="UTC")
    state = fsm(1)
    start = await open_menu(send, state)
    await choose_type(press, state, start.answered[-1], "hourly")
    await say(send, state, animation=SimpleNamespace(file_id="gif"), document=SimpleNamespace(file_id="gif-doc"))
    await say(send, state, "1")
    await say(send, state, "📍 С текущего момента")
    assert (await all_reminders())[-1]["content_type"] == "animation"


async def test_unsupported_content_is_rejected(send, press, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    start = await open_menu(send, state)
    await choose_type(press, state, start.answered[-1], "once")
    reply = await say(send, state, poll=SimpleNamespace(question="?"))
    assert "текст" in reply.answered[-1].text.lower()
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_text.state


async def test_restarting_flow_resets_choices(send, press, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    content = await flow_to_content(send, press, state, "weekly")
    await press(FakeCallback("weekday_mon", content.answered[-1]), state)

    await open_menu(send, state)
    data = await state.get_data()
    assert not {"selected_weekdays", "text", "repeat_type"} & set(data)
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_repeat_type.state


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
    assert "1015" in msg.answered[-1].text


# ---------- Часовой пояс ----------

async def test_manual_timezone_is_saved(send, press, fsm):
    await storage.get_or_create_user(1)
    state = fsm(1)
    await press(FakeCallback("tz_manual", FakeMessage(1, text="👋 Привет!")), state)
    await send(FakeMessage(1, text="Mars/Olympus"), state)
    assert await storage.get_user_timezone(1) is None
    await send(FakeMessage(1, text="Europe/Moscow"), state)
    assert await storage.get_user_timezone(1) == "Europe/Moscow"


async def test_geo_timezone_is_saved(send, press, fsm):
    await storage.get_or_create_user(1)
    state = fsm(1)
    choice = FakeMessage(1, text="👋 Привет!")
    await press(FakeCallback("tz_geo", choice), state)
    buttons = [b for row in markup_of(choice.answered[-1]).keyboard for b in row]
    assert any(b.request_location for b in buttons)
    await send(FakeMessage(1, location=SimpleNamespace(latitude=55.75, longitude=37.62)), state)
    assert await storage.get_user_timezone(1) == "Europe/Moscow"


# ---------- После выбора вопросы и кнопки исчезают ----------

def bot_messages(*user_or_bot_msgs):
    """Все сообщения бота, порождённые этими сообщениями (рекурсивно: ответы на ответы)."""
    out, stack = [], list(user_or_bot_msgs)
    while stack:
        m = stack.pop()
        for reply in m.answered:
            out.append(reply)
            stack.append(reply)
    return out


async def run_flow(send, press, state, repeat):
    """Пройти весь сценарий. Возвращает (сообщения пользователя, подтверждение)."""
    start = await open_menu(send, state)
    await choose_type(press, state, start.answered[-1], repeat)
    content = await say(send, state, "купить хлеб")
    users = [start, content]
    step = content.answered[-1]
    day = tomorrow_utc()
    if repeat == "once":
        await press(FakeCallback(f"date_1_{day.year}_{day.month}_{day.day}", step), state)
    elif repeat == "hourly":
        n = await say(send, state, "3")
        now_msg = await say(send, state, "📍 С текущего момента")
        users += [n, now_msg]
        return users, now_msg.answered[-1]
    elif repeat == "daily":
        n = await say(send, state, "2")
        users.append(n)
        await press(FakeCallback(f"date_1_{day.year}_{day.month}_{day.day}", n.answered[-1]), state)
    elif repeat == "weekly":
        for data in ["weekday_mon", "weekday_done"]:
            await press(FakeCallback(data, step), state)
    else:  # monthly
        await press(FakeCallback("monthmode_days", step), state)
        days_msg = step.answered[-1]
        for data in ["mday_10", "mday_done"]:
            await press(FakeCallback(data, days_msg), state)
    t = await say(send, state, "1000")
    users.append(t)
    return users, t.answered[-1]


@pytest.mark.parametrize("repeat", ["once", "hourly", "daily", "weekly", "monthly"])
async def test_only_confirmation_stays_after_flow(send, press, fsm, bot, repeat):
    await make_user(1, tz="UTC")
    state = fsm(1)
    users, confirmation = await run_flow(send, press, state, repeat)

    assert "✅ Напоминание установлено" in confirmation.text
    questions = [m for m in bot_messages(*users) if m is not confirmation]
    assert questions
    assert {m.message_id for m in questions} <= deleted_ids(bot)
    assert confirmation.message_id not in deleted_ids(bot)
    assert not {m.message_id for m in users} & deleted_ids(bot)  # сообщения пользователя не трогаем


async def test_previous_question_disappears_right_after_choice(send, press, fsm, bot):
    await make_user(1, tz="UTC")
    state = fsm(1)
    start = await open_menu(send, state)
    header, menu = start.answered
    await choose_type(press, state, menu, "once")
    assert menu.message_id in deleted_ids(bot)  # меню типов исчезло
    assert header.message_id not in deleted_ids(bot)  # шапка держит клавиатуру «❌ Отмена»

    content = await say(send, state, "x")
    text_prompt = start.answered[-1].answered[-1]
    assert text_prompt.message_id in deleted_ids(bot)  # «Отправьте текст…» исчезло
    assert not {m.message_id for m in content.answered} & deleted_ids(bot)  # текущий шаг на месте


async def test_cancel_removes_questions(send, press, fsm, bot):
    await make_user(1, tz="UTC")
    state = fsm(1)
    start = await open_menu(send, state)
    await choose_type(press, state, start.answered[-1], "once")
    content = await say(send, state, "x")
    cancel = await say(send, state, CANCEL)

    questions = bot_messages(start, content)
    assert {m.message_id for m in questions} <= deleted_ids(bot)
    assert cancel.answered[-1].message_id not in deleted_ids(bot)


async def test_hints_and_errors_disappear_too(send, press, fsm, bot):
    await make_user(1, tz="UTC")
    state = fsm(1)
    content = await flow_to_content(send, press, state, "once")
    hint = await say(send, state, "завтра")          # текст вместо кнопки
    day = tomorrow_utc()
    await press(FakeCallback(f"date_1_{day.year}_{day.month}_{day.day}", content.answered[-1]), state)
    error = await say(send, state, "abc")            # неверное время
    await say(send, state, "1000")

    assert hint.answered[-1].message_id in deleted_ids(bot)
    assert error.answered[-1].message_id in deleted_ids(bot)


async def test_restart_removes_questions_of_abandoned_flow(send, press, fsm, bot):
    await make_user(1, tz="UTC")
    state = fsm(1)
    content = await flow_to_content(send, press, state, "weekly")
    old_questions = list(content.answered)
    again = await open_menu(send, state)
    assert {m.message_id for m in old_questions} <= deleted_ids(bot)
    assert not {m.message_id for m in again.answered} & deleted_ids(bot)


async def test_timezone_questions_disappear(send, press, fsm, bot):
    await storage.get_or_create_user(1)
    state = fsm(1)
    choice = FakeMessage(1, text="👋 Привет! Укажите часовой пояс")
    await press(FakeCallback("tz_manual", choice), state)
    bad = await say(send, state, "Mars/Olympus")
    good = await say(send, state, "Europe/Moscow")
    assert {choice.message_id, bad.answered[-1].message_id} <= deleted_ids(bot)
    assert good.answered[-1].message_id not in deleted_ids(bot)


async def test_geo_questions_disappear(send, press, fsm, bot):
    await storage.get_or_create_user(1)
    state = fsm(1)
    choice = FakeMessage(1, text="👋 Привет! Укажите часовой пояс")
    await press(FakeCallback("tz_geo", choice), state)
    geo_prompt = choice.answered[-1]
    assert choice.message_id in deleted_ids(bot)  # кнопки выбора способа исчезли
    done = await say(send, state, location=SimpleNamespace(latitude=55.75, longitude=37.62))
    assert geo_prompt.message_id in deleted_ids(bot)
    assert done.answered[-1].message_id not in deleted_ids(bot)


async def test_failed_delete_does_not_break_flow(send, press, fsm, bot):
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import DeleteMessages

    async def cannot_delete(chat_id, message_ids, **kwargs):
        raise TelegramBadRequest(method=DeleteMessages(chat_id=chat_id, message_ids=message_ids),
                                 message="Bad Request: message can't be deleted")

    bot.delete_messages = cannot_delete
    await make_user(1, tz="UTC")
    _, confirmation = await run_flow(send, press, fsm(1), "once")
    assert "✅ Напоминание установлено" in confirmation.text



# ---------- «❌ Отмена» всегда на клавиатуре, сообщений — минимум ----------

async def walk(send, press, state, repeat, after_each):
    """Пройти сценарий, вызывая after_each(сообщения пользователя) после каждого действия."""
    users = []
    start = await open_menu(send, state)
    users.append(start)
    await after_each(users)
    await choose_type(press, state, start.answered[-1], repeat)
    await after_each(users)
    content = await say(send, state, "купить хлеб")
    users.append(content)
    await after_each(users)
    step = content.answered[-1]
    day = tomorrow_utc()
    date_data = f"date_1_{day.year}_{day.month}_{day.day}"

    async def tap(data, msg):
        await press(FakeCallback(data, msg), state)
        await after_each(users)

    if repeat == "once":
        await tap(date_data, step)
    elif repeat in ("hourly", "daily"):
        n = await say(send, state, "3")
        users.append(n)
        await after_each(users)
        if repeat == "daily":
            await tap(date_data, n.answered[-1])
    elif repeat == "weekly":
        await tap("weekday_mon", step)
        await tap("weekday_done", step)
    else:
        await tap("monthmode_days", step)
        days_msg = step.answered[-1]
        await tap("mday_10", days_msg)
        await tap("mday_done", days_msg)
    final = await say(send, state, "1000")
    users.append(final)
    return users, final.answered[-1]


@pytest.mark.parametrize("repeat", ["once", "hourly", "daily", "weekly", "monthly"])
async def test_cancel_is_always_on_keyboard(send, press, fsm, bot, repeat):
    await make_user(1, tz="UTC")
    state = fsm(1)

    async def check(users):
        keyboard = visible_keyboard(bot, bot_messages(*users))
        assert keyboard and keyboard[-1] == [CANCEL], keyboard

    users, confirmation = await walk(send, press, state, repeat, check)
    assert "✅ Напоминание установлено" in confirmation.text
    assert ["➕ Новое напоминание"] in visible_keyboard(bot, bot_messages(*users))


@pytest.mark.parametrize("repeat", ["once", "hourly", "daily", "weekly", "monthly"])
async def test_no_cancel_buttons_inside_messages(send, press, fsm, bot, repeat):
    await make_user(1, tz="UTC")

    async def nothing(users):
        pass

    users, _ = await walk(send, press, fsm(1), repeat, nothing)
    for m in bot_messages(*users):
        if isinstance(markup_of(m), InlineKeyboardMarkup):
            assert "cancel_flow" not in inline_datas(markup_of(m))


@pytest.mark.parametrize("repeat,expected", [
    # меню (шапка + меню), тип, текст, …, итог
    ("once", [2, 1, 1, 1, 1]),
    ("hourly", [2, 1, 1, 1, 1]),
    ("daily", [2, 1, 1, 2, 1, 1]),   # после числа дней — снова шапка с «❌ Отмена» и календарь
    ("weekly", [2, 1, 1, 0, 1, 1]),  # отметка дня меняет то же сообщение
    ("monthly", [2, 1, 1, 1, 0, 1, 1]),
])
async def test_messages_per_step(send, press, fsm, repeat, expected):
    await make_user(1, tz="UTC")
    counts, seen = [], [0]

    async def count(users):
        total = len(bot_messages(*users))
        counts.append(total - seen[0])
        seen[0] = total

    users, _ = await walk(send, press, fsm(1), repeat, count)
    counts.append(len(bot_messages(*users)) - seen[0])
    assert counts == expected


async def test_cancel_button_from_previous_version_still_works(send, press, fsm, bot):
    await make_user(1, tz="UTC")
    state = fsm(1)
    content = await flow_to_content(send, press, state, "once")
    calendar_msg = content.answered[-1]
    cb = FakeCallback("cancel_flow", calendar_msg)
    await press(cb, state)

    assert await state.get_state() is None
    assert cb.answers
    done = calendar_msg.answered[-1]
    assert "отменено" in done.text.lower()
    assert ["➕ Новое напоминание"] in reply_rows(markup_of(done))
    assert calendar_msg.message_id in deleted_ids(bot)
    assert await all_reminders() == []


async def test_start_now_button_only_for_hours(send, press, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    content = await flow_to_content(send, press, state, "once")
    day = tomorrow_utc()
    await press(FakeCallback(f"date_1_{day.year}_{day.month}_{day.day}", content.answered[-1]), state)
    reply = await say(send, state, "📍 С текущего момента")
    assert await all_reminders() == []
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_time.state
    assert "1015" in reply.answered[-1].text
