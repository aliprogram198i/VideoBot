import asyncio
import time

from downloader.krx18_resolver import (
    KRX18_MAX_PLAYER_CLICKS,
    KRX18_PLAYER_CLICK_TIMEOUT_MS,
    _probe_player_controls,
)


class _FakeControl:
    def __init__(self, label):
        self.label = label
        self.clicks = 0

    async def evaluate(self, script):
        return "button"

    async def inner_text(self, timeout=None):
        return self.label

    async def get_attribute(self, name):
        return ""

    async def click(self, timeout=None, force=False):
        assert timeout == KRX18_PLAYER_CLICK_TIMEOUT_MS
        assert force is True
        self.clicks += 1


class _FakeLocator:
    def __init__(self, controls):
        self.controls = controls

    async def count(self):
        return len(self.controls)

    def nth(self, index):
        return self.controls[index]


class _FakePage:
    def __init__(self, controls):
        self.locator_obj = _FakeLocator(controls)

    def locator(self, selector):
        assert selector == "button,a,[role='button'],[onclick],video,iframe"
        return self.locator_obj

    async def wait_for_timeout(self, milliseconds):
        assert milliseconds <= 700


def test_player_probe_is_bounded_and_clicks_at_most_three_controls():
    controls = [_FakeControl(f"Play {index}") for index in range(6)]
    page = _FakePage(controls)
    clicked = asyncio.run(_probe_player_controls(page, time.monotonic() + 5))

    assert clicked == KRX18_MAX_PLAYER_CLICKS == 3
    assert sum(control.clicks for control in controls) == 3
