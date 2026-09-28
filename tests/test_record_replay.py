import asyncio
import socket

import pytest

from fieldmon.protocol import decode_pos, encode_pos, FrameError
from fieldmon.receivers import start_tcp, start_udp
from fieldmon.recorder import FrameRecorder
from fieldmon.tracker import FieldTracker
from tools import replay
from tools.replay import DropWindow, Record, load, plan


def _records():
    out = []
    for i in range(10):                         # 1초 간격, 장비 2대
        out.append(Record(float(i), "UDP", encode_pos("U1", 37.0, 126.0 + i / 1000, 1, 0)))
        out.append(Record(float(i) + 0.5, "SERIAL", encode_pos("U2", 37.1, 126.0, 1, 0)))
    return out


def test_recorder_roundtrip(tmp_path):
    now = [100.0]
    rec = FrameRecorder(tmp_path / "r.jsonl", clock=lambda: now[0])
    rec.write(encode_pos("U1", 37, 126, 0, 0) + "\r\n", "UDP")
    now[0] = 101.25
    rec.write("$BROKEN*00", "SERIAL")            # 오류 프레임도 그대로 기록
    rec.write("\n", "TCP")                        # 빈 줄은 기록하지 않음
    rec.close()
    recs = load(tmp_path / "r.jsonl")
    assert [(r.t, r.src) for r in recs] == [(0.0, "UDP"), (1.25, "SERIAL")]
    assert recs[1].line == "$BROKEN*00"


def test_plan_keeps_timing_and_speed():
    sends = plan(_records(), speed=2.0)
    assert len(sends) == 20
    assert sends[1].at == 0.25 and sends[-1].at == 4.75
    assert {s.via for s in sends} == {"UDP"}     # 시리얼 기록은 UDP로 보냄


def test_plan_drop_window():
    sends = plan(_records(), drops=(DropWindow.parse("U2:3-6"),))
    u2 = [s.at for s in sends if ",U2," in s.line]
    assert u2 == [0.5, 1.5, 2.5, 6.5, 7.5, 8.5, 9.5]
    assert len(plan(_records(), drops=(DropWindow.parse("U1:5-"),))) == 15


def test_plan_corrupt_and_dup_are_reproducible():
    a = plan(_records(), corrupt_rate=0.3, dup_rate=0.3, seed=7)
    b = plan(_records(), corrupt_rate=0.3, dup_rate=0.3, seed=7)
    assert a == b
    corrupted = [s for s in a if s.note == "corrupt"]
    assert corrupted and all(_bad(s.line) for s in corrupted)
    assert any(s.note == "dup" for s in a)


def _bad(line):
    try:
        decode_pos(line)
        return False
    except FrameError:
        return True


def test_bad_drop_spec():
    with pytest.raises(Exception):
        DropWindow.parse("U2-20-40")


def test_replay_end_to_end_into_tracker(tmp_path):
    """기록 → 재생(두절 주입) → 수신기 → 트래커: 주입한 두절이 그대로 감지되는지"""
    def free(kind):
        s = socket.socket(socket.AF_INET, kind)
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        return p

    recs = [Record(i * 0.1, "UDP", encode_pos("U1", 37.0, 126.0, 1, 0)) for i in range(10)]
    recs += [Record(i * 0.1 + 0.05, "TCP", encode_pos("U2", 37.1, 126.0, 1, 0)) for i in range(10)]
    sends = plan(sorted(recs, key=lambda r: r.t), drops=(DropWindow("U2", 0.3, 9.9),), corrupt_rate=0.0)

    async def run():
        now = [0.0]
        t = FieldTracker(lost_after=0.3)
        up, tp = free(socket.SOCK_DGRAM), free(socket.SOCK_STREAM)
        udp = await start_udp(t.feed, up, "127.0.0.1")
        tcp = await start_tcp(t.feed, tp, "127.0.0.1")
        await asyncio.to_thread(replay.run, sends, "127.0.0.1", up, tp)
        await asyncio.sleep(0.4)
        t.sweep()
        udp.close()
        tcp.close()
        return {u["unit"]: (u["online"], u["frames"]) for u in t.snapshot()["units"]}

    res = asyncio.run(run())
    assert res["U2"] == (False, 3)     # 0.3초 이후 끊김 → 3건만 받고 두절
    assert res["U1"][1] == 10
