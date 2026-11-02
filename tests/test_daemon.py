import asyncio

import pytest

from bodycontrol.daemon import Session, drain_delay_s
from bodycontrol.deep import ENABLE_SEQUENCE
from bodycontrol.frame import build_history_ack

TOKEN = bytes(range(8))


class FakeClient:
    """Records every write. After the SEND_HISTORICAL_DATA write (the one after the flags) it runs `on_send`."""

    def __init__(self, on_send):
        self.writes: list[bytes] = []
        self.on_send = on_send

    async def write_gatt_char(self, uuid, data, response=False):
        self.writes.append(bytes(data))
        if len(self.writes) == len(ENABLE_SEQUENCE) + 1:
            self.on_send()


class FakeStore:
    def __init__(self):
        self.commits = 0

    def commit(self):
        self.commits += 1


def _drain(idle_s, max_s, complete_at=None, token_at=0.02):
    async def go():
        holder = {}

        def on_send():
            s = holder["s"]
            if complete_at is not None:
                s.loop.call_later(complete_at, s.drain_done.set)
            s.loop.call_later(token_at, s.acks.put_nowait, TOKEN)

        s = holder["s"] = Session(FakeClient(on_send), FakeStore(), 1)
        return s, await s.drain(idle_s=idle_s, max_s=max_s)

    return asyncio.run(go())


def test_drain_complete_when_strap_reports_history_complete():
    s, outcome = _drain(idle_s=5.0, max_s=5.0, complete_at=0.01)
    assert outcome == "complete"
    assert s.n["chunks"] == 1
    assert s.client.writes[-1] == build_history_ack(TOKEN, seq=s.seq)
    assert s.store.commits >= 2  # once before the ack, once at the end


def test_drain_capped_when_time_runs_out_with_data_still_flowing():
    s, outcome = _drain(idle_s=5.0, max_s=0.01)
    assert outcome == "capped"
    assert s.n["chunks"] == 1


def test_drain_idle_wins_over_capped():
    _, outcome = _drain(idle_s=0.01, max_s=0.01)
    assert outcome == "idle"


def test_capped_drain_is_followed_at_once_others_wait_the_interval():
    assert drain_delay_s("capped", 60.0) == 0.0
    assert drain_delay_s("complete", 60.0) == 3600.0
    assert drain_delay_s("idle", 60.0) == 3600.0


class DeadClient:
    """A link that accepted the connection and then never answers."""

    async def write_gatt_char(self, uuid, data, response=False):
        await asyncio.Event().wait()

    async def read_gatt_char(self, uuid):
        await asyncio.Event().wait()


def test_write_on_a_dead_link_times_out_instead_of_hanging():
    async def go():
        s = Session(DeadClient(), FakeStore(), 1)
        s.gatt_timeout_s = 0.05
        await s.send(0x16, b"\x00")

    with pytest.raises(TimeoutError):
        asyncio.run(go())


def test_battery_on_a_dead_link_returns_none_after_the_timeout():
    async def go():
        s = Session(DeadClient(), FakeStore(), 1)
        s.gatt_timeout_s = 0.05
        return await s.battery()

    assert asyncio.run(go()) is None
