"""Тесты бота без Telegram: настоящие main/storage/keyboards/scheduler,
поддельные только сообщения, нажатия кнопок и сам Bot.

Запуск из папки бота:
    py -m pip install -r requirements.txt pytest pytest-asyncio
    py -m pytest
"""
import itertools
import os
import sys
from types import SimpleNamespace

import aiosqlite
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
BOT_DIR = os.path.dirname(HERE)
sys.path.insert(0, BOT_DIR)

from aiogram.fsm.context import FSMContext  # noqa: E402
from aiogram.fsm.storage.base import StorageKey  # noqa: E402
from aiogram.fsm.storage.memory import MemoryStorage  # noqa: E402

import storage  # noqa: E402

BOT_MAIN_PATH = os.path.join(BOT_DIR, "main.py")


_message_ids = itertools.count(1000)


class FakeMessage:
    def __init__(self, chat_id, text=None, location=None):
        self.message_id = next(_message_ids)
        self.chat = SimpleNamespace(id=chat_id)
        self.from_user = SimpleNamespace(id=chat_id)
        self.text = text
        self.caption = None
        self.location = location
        self.sent = []
        self.answered = []  # сообщения, которые бот отправил в ответ (с их message_id)

    async def answer(self, text, **kwargs):
        self.sent.append(("answer", text, kwargs))
        reply = FakeMessage(self.chat.id, text=text)
        reply.kwargs = kwargs
        self.answered.append(reply)
        return reply

    async def edit_text(self, text, **kwargs):
        self.sent.append(("edit_text", text, kwargs))

    async def edit_reply_markup(self, **kwargs):
        self.sent.append(("edit_reply_markup", None, kwargs))

    async def edit_caption(self, caption=None, **kwargs):
        self.sent.append(("edit_caption", caption, kwargs))

    def texts(self):
        return [t for _, t, _ in self.sent if t is not None]


class FakeCallback:
    def __init__(self, data, message):
        self.data = data
        self.message = message
        self.from_user = message.from_user
        self.answers = []

    async def answer(self, text=None, show_alert=False, **kwargs):
        self.answers.append((text, show_alert))


class FakeBot:
    """Записывает send_message; можно заставить его падать с ошибкой."""

    def __init__(self, error=None):
        self.sent = []
        self.media = []  # (метод, chat_id, file_id, kwargs) для send_photo, send_voice и т.п.
        self.deleted = []
        self.error = error

    def __getattr__(self, name):
        if name.startswith("send_"):
            async def sender(chat_id, file_id, **kwargs):
                if self.error is not None:
                    raise self.error
                self.media.append((name, chat_id, file_id, kwargs))
            return sender
        raise AttributeError(name)

    async def send_message(self, chat_id, text, **kwargs):
        if self.error is not None:
            raise self.error
        self.sent.append((chat_id, text, kwargs))

    async def delete_message(self, chat_id, message_id, **kwargs):
        self.deleted.append((chat_id, message_id))
        return True


@pytest.fixture
def main_module():
    import main
    return main


@pytest.fixture(autouse=True)
async def fresh_db(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(storage, "DB_PATH", str(tmp_path / "reminders.db"))
    await storage.init_db()


@pytest.fixture
def fsm():
    st = MemoryStorage()

    def make(chat_id):
        return FSMContext(storage=st, key=StorageKey(bot_id=1, chat_id=chat_id, user_id=chat_id))

    return make


@pytest.fixture
def bot():
    return FakeBot()


@pytest.fixture
def send(main_module, bot):
    async def _send(msg, state):
        return await main_module.router.message.trigger(
            msg, state=state, raw_state=await state.get_state(), bot=bot
        )
    return _send


@pytest.fixture
def press(main_module, bot):
    async def _press(cb, state):
        return await main_module.router.callback_query.trigger(
            cb, state=state, raw_state=await state.get_state(), bot=bot
        )
    return _press


async def make_user(chat_id, tz="UTC"):
    uid = await storage.get_or_create_user(chat_id)
    await storage.set_user_timezone(chat_id, tz)
    return uid


async def all_reminders():
    async with aiosqlite.connect(storage.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM reminders ORDER BY id") as c:
            return [dict(r) for r in await c.fetchall()]
