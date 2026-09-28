from fieldmon.protocol import encode_pos
from fieldmon.tracker import FieldTracker


def make():
    now = [0.0]
    return FieldTracker(lost_after=5, clock=lambda: now[0]), now


def test_comm_loss_and_recovery():
    t, now = make()
    assert t.feed(encode_pos("U1", 37.0, 126.0, 0, 0), "UDP")
    now[0] = 6
    assert t.sweep()
    assert t.snapshot()["units"][0]["online"] is False
    assert not t.sweep()                      # 이미 두절이면 중복 이벤트 없음
    now[0] = 8
    t.feed(encode_pos("U1", 37.0001, 126.0, 0, 0), "TCP")
    u = t.snapshot()["units"][0]
    assert u["online"] and u["source"] == "TCP" and len(u["track"]) == 2
    msgs = [e["msg"] for e in t.snapshot()["events"]]
    assert any("통신 복구" in m and "8초 만" in m for m in msgs)
    assert any("통신 두절" in m for m in msgs)


def test_bad_frames_counted_per_source_and_kept_out_of_event_log():
    t, _ = make()
    t.feed(encode_pos("U1", 37.0, 126.0, 0, 0), "UDP")
    for _ in range(30):
        t.feed("$POS,U1,1,2,3,4*FF", "SERIAL")   # 체크섬 불일치
    t.feed("garbage", "TCP")
    s = t.snapshot()
    assert s["bad_by_source"] == {"SERIAL": 30, "TCP": 1}
    assert s["bad_frames"] == 31
    assert len(s["errors"]) == 20                         # 최근 20건만 보관
    assert [e["msg"] for e in s["events"]] == ["U1 최초 수신 (UDP)"]   # 오류가 이벤트를 밀어내지 않음


def test_blank_lines_ignored():
    t, _ = make()
    assert not t.feed("\r\n", "SERIAL")
    assert t.snapshot()["bad_frames"] == 0
