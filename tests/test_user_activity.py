import asyncio
from types import SimpleNamespace

from plugins.user_activity import register_user_activity


class FakeApp:
    def __init__(self):
        self.handlers = []

    def add_handler(self, handler, group=0):
        self.handlers.append((handler, group))


class FakeBotModule:
    def __init__(self):
        self.users = []

    def register_user(self, user):
        self.users.append(user)


def test_user_activity_registers_non_blocking_message_middleware():
    app = FakeApp()
    bot = FakeBotModule()
    register_user_activity(app, bot)
    assert len(app.handlers) == 1
    handler, group = app.handlers[0]
    assert group == -100
    user = SimpleNamespace(id=123)
    update = SimpleNamespace(effective_user=user)
    asyncio.run(handler.callback(update, SimpleNamespace()))
    assert bot.users == [user]
