"""Диалог создания напоминания.

Тип повторения → текст или вложение → параметры типа → время → «✅ Напоминание установлено».
Каждый шаг — одно сообщение без кнопок внутри: варианты и «❌ Отмена» — на нижней клавиатуре.
«❌ Отмена» появляется только на время создания. Вопросы бота и нажатия кнопок исчезают,
в чате остаются текст напоминания и итог.
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
CONFIRM = "✔ Подтвердить выбор"
START_NOW = "📍 С текущего момента"
ONCE, HOURS, DAYS, WEEK, MONTH = "🗓 Однократно", "🔁 Каждые N часов", "🔁 Каждые N дней", "📆 Каждую неделю", "📆 Каждый месяц"
MODE_DAYS, MODE_LAST, MODE_BEFORE = "📅 В указанные дни месяца", "📆 В последний день месяца", "⏳ За N дней до конца месяца"
TIME_INSTRUCTION = "Во сколько напоминать?\nВыберите время ниже или введите вручную (например: 1015)."
MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July", "August",
             "September", "October", "November", "December"]


# ---------- Помощники ----------

def markup_of(msg):
    return msg.kwargs.get("reply_markup")


def reply_rows(markup):
    assert isinstance(markup, ReplyKeyboardMarkup), markup
    return [[b.text for b in row] for row in markup.keyboard]


def deleted_ids(bot):
    return {mid for _, mid in bot.deleted}


def bot_messages(*msgs):
    """Все сообщения бота, порождённые этими сообщениями (ответы на ответы — тоже)."""
    out, stack = [], list(msgs)
    while stack:
        m = stack.pop()
        for answer in m.answered:
            out.append(answer)
            stack.append(answer)
    return out


def visible_keyboard(bot, messages):
    """Какая нижняя клавиатура сейчас видна в Telegram.
    Её задаёт последнее сообщение с клавиатурой; если его удалили — клавиатуры нет."""
    with_kb = [m for m in messages if isinstance(markup_of(m), (ReplyKeyboardMarkup, ReplyKeyboardRemove))]
    if not with_kb:
        return None
    latest = max(with_kb, key=lambda m: m.message_id)
    if latest.message_id in deleted_ids(bot) or isinstance(markup_of(latest), ReplyKeyboardRemove):
        return None
    return reply_rows(markup_of(latest))


async def say(send, state, text=None, **attachment):
    msg = FakeMessage(1, text=text)
    for key, value in attachment.items():
        setattr(msg, key, value)
    await send(msg, state)
    return msg


def reply(msg):
    """Последний ответ бота на сообщение пользователя."""
    return msg.answered[-1]


async def flow_to_content(send, state, label, text="купить хлеб"):
    """«➕ Новое напоминание» → тип → текст. Возвращает сообщение с текстом."""
    await say(send, state, "➕ Новое напоминание")
    await say(send, state, label)
    return await say(send, state, text)


def tomorrow_utc():
    return datetime.now(UTC).date() + timedelta(days=1)


def typed(d):
    return d.strftime("%d.%m.%Y")


def fmt(main_module, d):
    return main_module.format_date(d)


# ---------- Меню ----------

async def test_menu_is_one_message_with_types_on_keyboard(send, fsm):
    await make_user(1, tz="UTC")
    start = await say(send, fsm(1), "➕ Новое напоминание")
    [menu] = start.answered
    assert menu.text == "Выберите тип повторения:"
    assert reply_rows(markup_of(menu)) == [[ONCE], [HOURS], [DAYS], [WEEK], [MONTH], [CANCEL]]


async def test_main_menu_has_no_cancel(fsm, main_module):
    await make_user(1, tz="UTC")
    start = FakeMessage(1, text="/start")
    await main_module.cmd_start(start, fsm(1))
    assert CANCEL not in [t for row in reply_rows(markup_of(reply(start))) for t in row]


async def test_after_type_bot_asks_content(send, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await say(send, state, "➕ Новое напоминание")
    prompt = reply(await say(send, state, ONCE))
    assert prompt.text == "🗓 Однократно\n\nОтправьте текст или вложение для напоминания:"
    assert reply_rows(markup_of(prompt)) == [[CANCEL]]
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_text.state


async def test_unknown_type_gives_hint(send, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await say(send, state, "➕ Новое напоминание")
    hint = reply(await say(send, state, "что-то"))
    assert "клавиатур" in hint.text
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_repeat_type.state


# ---------- Отмена ----------

@pytest.mark.parametrize("steps", [[], ["купить хлеб"], ["купить хлеб", "date"]])
async def test_cancel_works_on_any_step(send, fsm, steps):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await say(send, state, "➕ Новое напоминание")
    await say(send, state, ONCE)
    for step in steps:
        await say(send, state, typed(tomorrow_utc()) if step == "date" else step)

    done = reply(await say(send, state, CANCEL))
    assert await state.get_state() is None
    assert "отменено" in done.text.lower()
    assert reply_rows(markup_of(done)) == [["➕ Новое напоминание"], ["📋 Мои напоминания", "⚙️ Настройки"]]
    assert await all_reminders() == []


async def test_cancel_outside_flow_is_silent(send, fsm, bot):
    await make_user(1, tz="UTC")
    state = fsm(1)
    cancel = await say(send, state, CANCEL)
    assert await state.get_state() is None
    assert cancel.answered == []
    assert cancel.message_id in deleted_ids(bot)


async def test_old_inline_cancel_button_still_cancels(send, press, fsm):
    await make_user(1, tz="UTC")
    state = fsm(1)
    content = await flow_to_content(send, state, ONCE)
    old_msg = reply(content)
    await press(FakeCallback("cancel_flow", old_msg), state)
    assert await state.get_state() is None
    assert "отменено" in reply(old_msg).text.lower()


# ---------- Каждые N часов ----------

async def test_hours_number_step(send, fsm):
    await make_user(1, tz="UTC")
    prompt = reply(await flow_to_content(send, fsm(1), HOURS))
    assert prompt.text == ("🔁 Каждые N часов\n\nКак часто напоминать?\n"
                           "Выберите количество часов из предложенных ниже или введите своё число.")
    assert reply_rows(markup_of(prompt)) == [["1", "2", "3", "4"], ["6", "8", "12", "24"], [CANCEL]]


async def test_hours_start_step(send, fsm):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await flow_to_content(send, state, HOURS)
    n = await say(send, state, "3")
    [step] = n.answered
    assert step.text == ("🔁 Каждые 3 часа\n\nОт какого момента начать отсчёт?\n"
                         "Можно начать с текущего момента, выбрать время ниже или ввести время вручную (например: 1015).")
    assert reply_rows(markup_of(step)) == [
        [START_NOW],
        ["08:00", "09:00", "10:00", "11:00"], ["12:00", "13:00", "14:00", "15:00"],
        ["16:00", "17:00", "18:00", "19:00"], ["20:00", "21:00", "22:00", "23:00"], [CANCEL],
    ]


async def test_hours_start_now(send, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await flow_to_content(send, state, HOURS, text="пить воду")
    await say(send, state, "3")
    before = datetime.now(UTC).replace(second=0, microsecond=0)
    done = reply(await say(send, state, START_NOW))

    rem = (await all_reminders())[-1]
    run_at = datetime.fromisoformat(rem["run_at"]).replace(tzinfo=UTC)
    assert before + timedelta(hours=3) <= run_at <= before + timedelta(hours=3, minutes=1)
    assert json.loads(rem["repeat_config"]) == {"every": 3}
    assert done.text == ("✅ <b>Напоминание установлено</b>\n📝 пить воду\n🔁 Каждые 3 часа\n"
                         f"🕰 Следующее: {fmt(main_module, run_at.date())} в {run_at:%H:%M}")


@pytest.mark.parametrize("offset_minutes", [120, -240, -30])
async def test_hours_start_from_time_counts_from_it(send, fsm, offset_minutes):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await flow_to_content(send, state, HOURS)
    await say(send, state, "3")
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    t = (now + timedelta(minutes=offset_minutes)).time()
    await say(send, state, t.strftime("%H%M"))

    expected = datetime.combine(now.date(), t, tzinfo=UTC)
    while expected <= datetime.now(UTC):
        expected += timedelta(hours=3)
    assert datetime.fromisoformat((await all_reminders())[-1]["run_at"]).replace(tzinfo=UTC) == expected


@pytest.mark.parametrize("typed_n", ["0", "-2", "abc", "2.5", "1000", ""])
async def test_wrong_hours_number_is_rejected(send, fsm, main_module, typed_n):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await flow_to_content(send, state, HOURS)
    assert "число" in reply(await say(send, state, typed_n)).text.lower()
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_interval.state


# ---------- Каждые N дней и календарь ----------

async def test_days_number_step(send, fsm):
    await make_user(1, tz="UTC")
    prompt = reply(await flow_to_content(send, fsm(1), DAYS))
    assert prompt.text == ("🔁 Каждые N дней\n\nКак часто напоминать?\n"
                           "Выберите количество дней из предложенных ниже или введите своё число.")
    assert reply_rows(markup_of(prompt)) == [["1", "2", "3", "7"], ["10", "14", "21", "30"], [CANCEL]]


async def test_days_full_flow(send, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await flow_to_content(send, state, DAYS, text="Отправить отчёт")
    calendar_step = reply(await say(send, state, "7"))
    today = datetime.now(UTC).date()
    assert calendar_step.text.startswith("🔁 Каждые 7 дней\n\nВыберите дату начала:\n")
    assert f"{MONTHS_EN[today.month - 1]} {today.year}" in calendar_step.text
    assert reply_rows(markup_of(calendar_step))[-1] == [CANCEL]

    day = tomorrow_utc()
    time_step = reply(await say(send, state, typed(day)))
    rule = f"🔁 Каждые 7 дней, начиная с {fmt(main_module, day)}"
    assert time_step.text == f"{rule}\n\n{TIME_INSTRUCTION}"

    done = reply(await say(send, state, "1000"))
    assert done.text == (f"✅ <b>Напоминание установлено</b>\n📝 Отправить отчёт\n{rule}\n"
                         f"🕰 Следующее: {fmt(main_module, day)} в 10:00")
    assert json.loads((await all_reminders())[-1]["repeat_config"]) == {"every": 7, "start": day.isoformat()}


async def test_calendar_shows_days_from_today_on_keyboard(send, fsm):
    await make_user(1, tz="UTC")
    calendar_step = reply(await flow_to_content(send, fsm(1), ONCE))
    keyboard = reply_rows(markup_of(calendar_step))
    today = datetime.now(UTC).date()
    days = [t for row in keyboard[:-2] for t in row]
    last = calendar.monthrange(today.year, today.month)[1]
    assert days == [f"•{today.day}•"] + [str(d) for d in range(today.day + 1, last + 1)]
    next_month = date(today.year + (today.month == 12), today.month % 12 + 1, 1)
    assert keyboard[-2] == [f"{MONTHS_EN[next_month.month - 1]} ▶"]
    assert keyboard[-1] == [CANCEL]


async def test_calendar_tap_today_and_tap_day_in_next_month(send, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    calendar_step = reply(await flow_to_content(send, state, ONCE))
    today = datetime.now(UTC).date()

    # листаем на следующий месяц и выбираем 1-е число
    forward = reply(await say(send, state, reply_rows(markup_of(calendar_step))[-2][-1]))
    next_month = date(today.year + (today.month == 12), today.month % 12 + 1, 1)
    assert f"{MONTHS_EN[next_month.month - 1]} {next_month.year}" in forward.text
    assert reply_rows(markup_of(forward))[-2][0] == f"◀ {MONTHS_EN[today.month - 1]}"
    time_step = reply(await say(send, state, "1"))
    assert time_step.text.startswith(f"🗓 Однократно, {fmt(main_module, next_month)}")

    # и в другом напоминании — «сегодня» с отметкой
    await flow_to_content(send, state, ONCE)
    time_step = reply(await say(send, state, f"•{today.day}•"))
    assert time_step.text.startswith(f"🗓 Однократно, {fmt(main_module, today)}")


@pytest.mark.parametrize("text", ["вчера", "32", "31.02", "abc"])
async def test_bad_date_gives_hint(send, fsm, main_module, text):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await flow_to_content(send, state, ONCE)
    hint = reply(await say(send, state, text))
    assert "дат" in hint.text.lower()
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_date.state


async def test_past_date_is_not_accepted(send, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await flow_to_content(send, state, ONCE)
    yesterday = datetime.now(UTC).date() - timedelta(days=1)
    hint = reply(await say(send, state, typed(yesterday)))
    assert "прошла" in hint.text
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_date.state


async def test_date_without_year_means_nearest_future(send, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await flow_to_content(send, state, ONCE)
    yesterday = datetime.now(UTC).date() - timedelta(days=1)
    time_step = reply(await say(send, state, yesterday.strftime("%d.%m")))  # прошло в этом году — значит в следующем
    expected = yesterday.replace(year=yesterday.year + 1)
    assert time_step.text.startswith(f"🗓 Однократно, {fmt(main_module, expected)}")


# ---------- Однократно ----------

async def test_once_flow(send, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    calendar_step = reply(await flow_to_content(send, state, ONCE, text="к ортопеду с Андреем"))
    assert calendar_step.text.startswith("🗓 Однократно\n\nВыберите дату:\n")

    day = tomorrow_utc()
    time_step = reply(await say(send, state, typed(day)))
    assert time_step.text == f"🗓 Однократно, {fmt(main_module, day)}\n\n{TIME_INSTRUCTION}"
    assert reply_rows(markup_of(time_step))[-1] == [CANCEL]

    done = reply(await say(send, state, "2000"))
    assert done.text == ("✅ <b>Напоминание установлено</b>\n📝 к ортопеду с Андреем\n🗓 Однократно\n"
                         f"🕰 {fmt(main_module, day)} в 20:00")
    assert reply_rows(markup_of(done)) == [["➕ Новое напоминание"], ["📋 Мои напоминания", "⚙️ Настройки"]]
    assert (await all_reminders())[-1]["run_at"] == f"{day.isoformat()}T20:00:00"
    assert await state.get_state() is None


async def test_past_time_today_asks_again(send, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await flow_to_content(send, state, ONCE)
    await say(send, state, typed(datetime.now(UTC).date()))
    hint = reply(await say(send, state, "0000"))
    assert "прошло" in hint.text
    assert await all_reminders() == []
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_time.state


# ---------- Каждую неделю ----------

async def test_weekly_flow(send, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    days_step = reply(await flow_to_content(send, state, WEEK, text="спортзал"))
    instruction = ("📆 Каждую неделю\n\nВыберите дни недели.\nМожно выбрать несколько дней.\n"
                   "Когда закончите — нажмите «Подтвердить выбор».")
    assert days_step.text == instruction
    assert reply_rows(markup_of(days_step)) == [["Пн", "Вт", "Ср", "Чт"], ["Пт", "Сб", "Вс"], [CONFIRM], [CANCEL]]

    await say(send, state, "Ср")
    marked = reply(await say(send, state, "Пн"))
    assert marked.text == f"{instruction}\n\nВыбрано: Пн, Ср"
    assert reply_rows(markup_of(marked))[0] == ["✅ Пн", "Вт", "✅ Ср", "Чт"]
    unmarked = reply(await say(send, state, "✅ Ср"))  # нажатие на отмеченный день снимает отметку
    assert unmarked.text == f"{instruction}\n\nВыбрано: Пн"
    await say(send, state, "Ср")

    time_step = reply(await say(send, state, CONFIRM))
    assert time_step.text == f"📆 Каждую неделю: Пн, Ср\n\n{TIME_INSTRUCTION}"
    done = reply(await say(send, state, "2000"))
    run_at = datetime.fromisoformat((await all_reminders())[-1]["run_at"]).replace(tzinfo=UTC)
    assert done.text == ("✅ <b>Напоминание установлено</b>\n📝 спортзал\n📆 Каждую неделю: Пн, Ср\n"
                         f"🕰 Следующее: {fmt(main_module, run_at.date())} в 20:00")
    assert run_at.weekday() in (0, 2)
    assert json.loads((await all_reminders())[-1]["repeat_config"]) == {"weekdays": ["mon", "wed"]}


async def test_weekly_confirm_without_days(send, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await flow_to_content(send, state, WEEK)
    assert "хотя бы один" in reply(await say(send, state, CONFIRM)).text
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_weekdays.state


# ---------- Каждый месяц ----------

async def test_month_mode_step(send, fsm):
    await make_user(1, tz="UTC")
    mode_step = reply(await flow_to_content(send, fsm(1), MONTH))
    assert mode_step.text == "📆 Каждый месяц\n\nКак напоминать каждый месяц?"
    assert reply_rows(markup_of(mode_step)) == [[MODE_DAYS], [MODE_LAST], [MODE_BEFORE], [CANCEL]]


def next_month_day_run(days, at, now):
    y, m = now.year, now.month
    for _ in range(3):
        last = calendar.monthrange(y, m)[1]
        for d in sorted({min(x, last) for x in days}):
            cand = datetime.combine(date(y, m, d), at, tzinfo=UTC)
            if cand > now:
                return cand
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


async def test_month_days_flow(send, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await flow_to_content(send, state, MONTH, text="Оплатить аренду")
    days_step = reply(await say(send, state, MODE_DAYS))
    instruction = ("📆 Каждый месяц\n\nВыберите дни месяца.\nМожно выбрать несколько дней.\n"
                   "Когда закончите — нажмите «Подтвердить выбор».\n\n"
                   "ℹ️ Если выбранный день отсутствует в месяце (например, 29 или 30), "
                   "напоминание придёт в последний день этого месяца.")
    assert days_step.text == instruction
    keyboard = reply_rows(markup_of(days_step))
    assert [t for row in keyboard[:-2] for t in row] == [str(d) for d in range(1, 32)]
    assert keyboard[-2:] == [[CONFIRM], [CANCEL]]

    await say(send, state, "29")
    marked = reply(await say(send, state, "5"))
    assert marked.text == f"{instruction}\n\nВыбрано: 5 и 29"
    assert "✅5" in [t for row in reply_rows(markup_of(marked)) for t in row]

    time_step = reply(await say(send, state, CONFIRM))
    assert time_step.text == f"📆 Каждый месяц, 5 и 29 числа\n\n{TIME_INSTRUCTION}"
    done = reply(await say(send, state, "0900"))
    expected = next_month_day_run([5, 29], time(9, 0), datetime.now(UTC))
    assert datetime.fromisoformat((await all_reminders())[-1]["run_at"]).replace(tzinfo=UTC) == expected
    assert done.text == (
        "✅ <b>Напоминание установлено</b>\n📝 Оплатить аренду\n📆 Каждый месяц, 5 и 29 числа\n"
        "ℹ️ В месяцах без 29 числа напоминание придёт в последний день\n"
        f"🕰 Следующее: {fmt(main_module, expected.date())} в 09:00"
    )
    assert json.loads((await all_reminders())[-1]["repeat_config"]) == {"days": [5, 29]}


async def test_month_days_require_selection(send, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await flow_to_content(send, state, MONTH)
    await say(send, state, MODE_DAYS)
    assert "хотя бы один" in reply(await say(send, state, CONFIRM)).text
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_month_days.state


async def test_month_last_day_flow(send, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await flow_to_content(send, state, MONTH, text="Оплатить коммуналку")
    time_step = reply(await say(send, state, MODE_LAST))
    assert time_step.text == f"📆 Каждый месяц, последний день\n\n{TIME_INSTRUCTION}"
    done = reply(await say(send, state, "1900"))
    expected = next_month_day_run([31], time(19, 0), datetime.now(UTC))
    assert done.text == ("✅ <b>Напоминание установлено</b>\n📝 Оплатить коммуналку\n📆 Каждый месяц, последний день\n"
                         f"🕰 Следующее: {fmt(main_module, expected.date())} в 19:00")
    assert json.loads((await all_reminders())[-1]["repeat_config"]) == {"last": True}


async def test_month_before_end_flow(send, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await flow_to_content(send, state, MONTH, text="Подготовить отчёт")
    ask_n = reply(await say(send, state, MODE_BEFORE))
    assert ask_n.text == ("📆 Каждый месяц\n\nЗа сколько дней до конца месяца напоминать?\n"
                          "Выберите число ниже или введите своё.")
    assert reply_rows(markup_of(ask_n)) == [["1", "2", "3", "5"], ["7", "10", "14"], [CANCEL]]

    time_step = reply(await say(send, state, "3"))
    assert time_step.text == f"📆 Каждый месяц, за 3 дня до конца месяца\n\n{TIME_INSTRUCTION}"
    done = reply(await say(send, state, "1800"))
    now = datetime.now(UTC)
    y, m = now.year, now.month
    expected = None
    for _ in range(2):
        cand = datetime.combine(date(y, m, calendar.monthrange(y, m)[1] - 3), time(18, 0), tzinfo=UTC)
        if cand > now:
            expected = cand
            break
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    assert done.text == ("✅ <b>Напоминание установлено</b>\n📝 Подготовить отчёт\n📆 Каждый месяц, за 3 дня до конца месяца\n"
                         f"🕰 Следующее: {fmt(main_module, expected.date())} в 18:00")
    assert json.loads((await all_reminders())[-1]["repeat_config"]) == {"before_end": 3}


@pytest.mark.parametrize("typed_n", ["0", "28", "abc"])
async def test_month_before_end_wrong_number(send, fsm, main_module, typed_n):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await flow_to_content(send, state, MONTH)
    await say(send, state, MODE_BEFORE)
    assert "число" in reply(await say(send, state, typed_n)).text.lower()
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_month_offset.state


async def test_unknown_month_mode_gives_hint(send, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await flow_to_content(send, state, MONTH)
    assert "клавиатур" in reply(await say(send, state, "как-нибудь")).text
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_month_mode.state


# ---------- Текст вне диалога запускает создание ----------

async def test_text_outside_flow_starts_reminder(send, fsm):
    await make_user(1, tz="UTC")
    state = fsm(1)
    msg = await say(send, state, "Купить хлеб")
    [menu] = msg.answered
    assert menu.text == "Купить хлеб\n\nВыберите тип повторения:"
    assert reply_rows(markup_of(menu))[-1] == [CANCEL]

    numbers = reply(await say(send, state, HOURS))  # текст уже есть — сразу следующий шаг
    assert numbers.text.startswith("🔁 Каждые N часов\n\nКак часто напоминать?")
    await say(send, state, "2")
    await say(send, state, START_NOW)
    rem = (await all_reminders())[-1]
    assert (rem["text"], rem["repeat_type"]) == ("Купить хлеб", "hourly")


async def test_commands_and_menu_buttons_do_not_start_reminder(send, fsm):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await say(send, state, "/unknown")
    assert await state.get_state() is None
    menu = await say(send, state, "📋 Мои напоминания")
    assert await state.get_state() is None
    assert "напоминаний" in reply(menu).text


async def test_text_inside_flow_belongs_to_current_step(send, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await flow_to_content(send, state, DAYS)
    await say(send, state, "Купить молоко")
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_interval.state


async def test_text_outside_flow_without_timezone_asks_for_it(send, fsm):
    await storage.get_or_create_user(1)
    state = fsm(1)
    msg = await say(send, state, "Купить хлеб")
    assert await state.get_state() is None
    assert "часовой пояс" in reply(msg).text.lower()


# ---------- Вложения ----------

async def test_photo_with_caption_becomes_reminder(send, fsm):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await say(send, state, "➕ Новое напоминание")
    await say(send, state, ONCE)
    await say(send, state, photo=[SimpleNamespace(file_id="small"), SimpleNamespace(file_id="big")], caption="рецепт")
    await say(send, state, typed(tomorrow_utc()))
    done = reply(await say(send, state, "1200"))
    assert "🖼 Фото: рецепт" in done.text
    rem = (await all_reminders())[-1]
    assert (rem["content_type"], rem["file_id"], rem["text"]) == ("photo", "big", "рецепт")


@pytest.mark.parametrize("kind,label", [
    ("voice", "🎤 Голосовое"), ("document", "📎 Файл"), ("video", "🎬 Видео"),
    ("audio", "🎵 Аудио"), ("video_note", "📹 Кружок"), ("sticker", "🙂 Стикер"), ("animation", "🎞 GIF"),
])
async def test_other_attachments_become_reminders(send, fsm, kind, label):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await say(send, state, "➕ Новое напоминание")
    await say(send, state, HOURS)
    await say(send, state, **{kind: SimpleNamespace(file_id=f"id-{kind}")})
    await say(send, state, "1")
    done = reply(await say(send, state, START_NOW))
    assert label in done.text
    rem = (await all_reminders())[-1]
    assert (rem["content_type"], rem["file_id"], rem["text"]) == (kind, f"id-{kind}", "")


async def test_gif_is_saved_as_animation_not_document(send, fsm):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await say(send, state, "➕ Новое напоминание")
    await say(send, state, HOURS)
    await say(send, state, animation=SimpleNamespace(file_id="gif"), document=SimpleNamespace(file_id="gif-doc"))
    await say(send, state, "1")
    await say(send, state, START_NOW)
    assert (await all_reminders())[-1]["content_type"] == "animation"


async def test_unsupported_content_is_rejected(send, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await say(send, state, "➕ Новое напоминание")
    await say(send, state, ONCE)
    hint = reply(await say(send, state, poll=SimpleNamespace(question="?")))
    assert "текст" in hint.text.lower()
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_text.state


async def test_restarting_flow_resets_choices(send, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await flow_to_content(send, state, WEEK)
    await say(send, state, "Пн")
    await say(send, state, "➕ Новое напоминание")
    data = await state.get_data()
    assert not {"selected_weekdays", "text", "repeat_type"} & set(data)
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_repeat_type.state


# ---------- Ввод времени ----------

async def start_time_step(state, main_module, **data):
    await state.update_data(text="x", **data)
    await state.set_state(main_module.ReminderFlow.waiting_for_time)


@pytest.mark.parametrize("typed_t,expected", [
    ("1430", "14:30"), ("14:30", "14:30"), ("14.30", "14:30"), ("14 30", "14:30"),
    ("0930", "09:30"), ("930", "09:30"), ("9:05", "09:05"), ("00:00", "00:00"),
    ("2359", "23:59"), ("  1430 ", "14:30"), ("08:00", "08:00"),
])
async def test_time_can_be_typed(send, fsm, main_module, typed_t, expected):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await start_time_step(state, main_module, repeat_type="daily", interval=1)
    await say(send, state, typed_t)
    assert datetime.fromisoformat((await all_reminders())[-1]["run_at"]).strftime("%H:%M") == expected


@pytest.mark.parametrize("typed_t", ["2400", "1260", "14", "abc", "14:3", "143000", "25:00", "-1430"])
async def test_wrong_time_is_rejected(send, fsm, main_module, typed_t):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await start_time_step(state, main_module, repeat_type="daily", interval=1)
    msg = await say(send, state, typed_t)
    assert await all_reminders() == []
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_time.state
    assert "1015" in reply(msg).text


async def test_start_now_only_for_hours(send, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await flow_to_content(send, state, ONCE)
    await say(send, state, typed(tomorrow_utc()))
    hint = reply(await say(send, state, START_NOW))
    assert await all_reminders() == []
    assert await state.get_state() == main_module.ReminderFlow.waiting_for_time.state
    assert "1015" in hint.text


# ---------- Часовой пояс ----------

async def test_manual_timezone_is_saved(send, press, fsm):
    await storage.get_or_create_user(1)
    state = fsm(1)
    await press(FakeCallback("tz_manual", FakeMessage(1, text="👋 Привет!")), state)
    await say(send, state, "Mars/Olympus")
    assert await storage.get_user_timezone(1) is None
    await say(send, state, "Europe/Moscow")
    assert await storage.get_user_timezone(1) == "Europe/Moscow"


async def test_geo_timezone_is_saved(send, press, fsm):
    await storage.get_or_create_user(1)
    state = fsm(1)
    choice = FakeMessage(1, text="👋 Привет!")
    await press(FakeCallback("tz_geo", choice), state)
    assert any(b.request_location for row in markup_of(reply(choice)).keyboard for b in row)
    await say(send, state, location=SimpleNamespace(latitude=55.75, longitude=37.62))
    assert await storage.get_user_timezone(1) == "Europe/Moscow"


async def test_timezone_questions_disappear(send, press, fsm, bot):
    await storage.get_or_create_user(1)
    state = fsm(1)
    choice = FakeMessage(1, text="👋 Привет! Укажите часовой пояс")
    await press(FakeCallback("tz_manual", choice), state)
    bad = await say(send, state, "Mars/Olympus")
    good = await say(send, state, "Europe/Moscow")
    assert {choice.message_id, reply(bad).message_id} <= deleted_ids(bot)
    assert reply(good).message_id not in deleted_ids(bot)


# ---------- Весь сценарий: клавиатура, исчезновение, количество сообщений ----------

FLOW_STEPS = {
    "once": [ONCE, "купить хлеб", "DATE"],
    "hourly": [HOURS, "купить хлеб", "3"],
    "daily": [DAYS, "купить хлеб", "3", "DATE"],
    "weekly": [WEEK, "купить хлеб", "Пн", CONFIRM],
    "monthly": [MONTH, "купить хлеб", MODE_DAYS, "10", CONFIRM],
}


async def walk(send, state, repeat, after_each):
    """Пройти сценарий, вызывая after_each(сообщения пользователя) после каждого действия."""
    users = []
    for text in ["➕ Новое напоминание"] + FLOW_STEPS[repeat]:
        users.append(await say(send, state, typed(tomorrow_utc()) if text == "DATE" else text))
        await after_each(users)
    final = await say(send, state, "1000")
    users.append(final)
    return users, reply(final)


@pytest.mark.parametrize("repeat", list(FLOW_STEPS))
async def test_cancel_on_keyboard_only_while_creating(send, fsm, bot, main_module, repeat):
    await make_user(1, tz="UTC")
    state = fsm(1)
    start = FakeMessage(1, text="/start")
    await main_module.cmd_start(start, state)
    assert [CANCEL] not in visible_keyboard(bot, bot_messages(start))  # до создания отмены нет

    async def check(users):
        keyboard = visible_keyboard(bot, bot_messages(start, *users))
        assert keyboard and keyboard[-1] == [CANCEL], keyboard
        for m in bot_messages(*users):
            assert not isinstance(markup_of(m), InlineKeyboardMarkup), m.text  # кнопок в сообщениях нет

    users, confirmation = await walk(send, state, repeat, check)
    assert "Напоминание установлено" in confirmation.text
    assert [CANCEL] not in visible_keyboard(bot, bot_messages(start, *users))  # после — снова без отмены


@pytest.mark.parametrize("repeat", list(FLOW_STEPS))
async def test_only_text_and_confirmation_stay(send, fsm, bot, repeat):
    await make_user(1, tz="UTC")

    async def nothing(users):
        pass

    users, confirmation = await walk(send, fsm(1), repeat, nothing)
    content = next(m for m in users if m.text == "купить хлеб")
    questions = [m for m in bot_messages(*users) if m is not confirmation]
    assert {m.message_id for m in questions} <= deleted_ids(bot)          # вопросы бота исчезли
    taps = [m for m in users if m is not content]
    assert {m.message_id for m in taps} <= deleted_ids(bot)               # нажатия кнопок исчезли
    assert content.message_id not in deleted_ids(bot)                     # текст напоминания остался
    assert confirmation.message_id not in deleted_ids(bot)                # и итог


@pytest.mark.parametrize("repeat", list(FLOW_STEPS))
async def test_every_action_gets_one_message(send, fsm, repeat):
    await make_user(1, tz="UTC")

    async def check(users):
        assert len(users[-1].answered) == 1, [m.text for m in users[-1].answered]

    users, _ = await walk(send, fsm(1), repeat, check)
    assert len(users[-1].answered) == 1


async def test_hints_and_wrong_answers_disappear_too(send, fsm, bot):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await flow_to_content(send, state, ONCE)
    wrong = await say(send, state, "abc")
    await say(send, state, typed(tomorrow_utc()))
    assert {wrong.message_id, reply(wrong).message_id} <= deleted_ids(bot)


async def test_restart_removes_questions_of_abandoned_flow(send, fsm, bot):
    await make_user(1, tz="UTC")
    state = fsm(1)
    content = await flow_to_content(send, state, WEEK)
    old_question = reply(content)
    again = await say(send, state, "➕ Новое напоминание")
    assert old_question.message_id in deleted_ids(bot)
    assert reply(again).message_id not in deleted_ids(bot)


async def test_failed_delete_does_not_break_flow(send, fsm, bot):
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import DeleteMessages

    async def cannot_delete(chat_id, message_ids, **kwargs):
        raise TelegramBadRequest(method=DeleteMessages(chat_id=chat_id, message_ids=message_ids),
                                 message="Bad Request: message can't be deleted")

    bot.delete_messages = cannot_delete
    await make_user(1, tz="UTC")

    async def nothing(users):
        pass

    _, confirmation = await walk(send, fsm(1), "once", nothing)
    assert "Напоминание установлено" in confirmation.text


async def test_confirmation_is_bold_html_and_escapes_text(send, fsm, main_module):
    await make_user(1, tz="UTC")
    state = fsm(1)
    await flow_to_content(send, state, ONCE, text="<купить> & хлеб")
    day = tomorrow_utc()
    await say(send, state, typed(day))
    done = reply(await say(send, state, "2100"))
    assert done.kwargs.get("parse_mode") == "HTML"
    assert done.text == ("✅ <b>Напоминание установлено</b>\n📝 &lt;купить&gt; &amp; хлеб\n🗓 Однократно\n"
                         f"🕰 {fmt(main_module, day)} в 21:00")
