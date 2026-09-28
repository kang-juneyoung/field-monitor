import pytest

from fieldmon.protocol import FrameError, checksum, decode_pos, encode_pos


def test_roundtrip():
    r = decode_pos(encode_pos("UNIT-01", 37.391512, 126.645034, 12.5, 90))
    assert (r.unit, round(r.lat, 6), round(r.lon, 6), r.speed, r.heading) == ("UNIT-01", 37.391512, 126.645034, 12.5, 90)


def test_checksum_is_case_insensitive_and_ignores_crlf():
    line = encode_pos("A", 37.0, 126.0, 1, 1)
    body, cs = line.rsplit("*", 1)
    assert decode_pos(f"{body}*{cs.lower()}\r\n").unit == "A"


@pytest.mark.parametrize("bad", [
    "POS,U,1,2,3,4*00",           # '$' 없음
    "$POS,U,1,2,3,4",             # 체크섬 없음
    "$POS,U,1,2,3,4*00",          # 체크섬 불일치
    "$POS,한글,1,2,3,4*00",        # ASCII 아님
])
def test_malformed_frames_rejected(bad):
    with pytest.raises(FrameError):
        decode_pos(bad)


@pytest.mark.parametrize("body", [
    "POS,U,95.0,126.0,1.0,0",     # 위도 범위 밖
    "POS,U,37.0,126.0,-1.0,0",    # 음수 속도
    "POS,U,37.0,126.0,1.0,400",   # 방위 범위 밖
    "POS,,37.0,126.0,1.0,0",      # 장비 ID 없음
    "POS,U,37.0,abc,1.0,0",       # 숫자 아님
    "GGA,U,37.0,126.0,1.0,0",     # 다른 문장
])
def test_valid_checksum_but_bad_content_rejected(body):
    with pytest.raises(FrameError):
        decode_pos(f"${body}*{checksum(body)}")
