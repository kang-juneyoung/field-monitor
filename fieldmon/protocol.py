"""현장 장비 위치 프로토콜.

    $POS,<id>,<lat>,<lon>,<speed_kmh>,<heading_deg>*<XOR 체크섬 2자리 hex>
    예) $POS,UNIT-01,37.391500,126.645000,12.5,90*5A

NMEA 0183 문장 구조를 따른 ASCII 한 줄 프로토콜. 시리얼·UDP·TCP 모두 같은 형식이며
줄바꿈(\\n)으로 프레임을 구분한다. 체크섬은 '$'와 '*' 사이 바이트의 XOR.
"""
from __future__ import annotations

from dataclasses import dataclass


class FrameError(ValueError):
    """형식·체크섬·범위 오류로 버려야 하는 프레임"""


@dataclass(frozen=True)
class PosReport:
    unit: str
    lat: float
    lon: float
    speed: float
    heading: float


def checksum(body: str) -> str:
    cs = 0
    for ch in body.encode("ascii"):
        cs ^= ch
    return f"{cs:02X}"


def encode_pos(unit: str, lat: float, lon: float, speed: float, heading: float) -> str:
    body = f"POS,{unit},{lat:.6f},{lon:.6f},{speed:.1f},{heading:.0f}"
    return f"${body}*{checksum(body)}"


def decode_pos(line: str) -> PosReport:
    line = line.strip()
    if not line.startswith("$") or "*" not in line:
        raise FrameError("'$'로 시작하고 '*'를 포함해야 함")
    body, cs = line[1:].rsplit("*", 1)
    try:
        want = checksum(body)
    except UnicodeEncodeError as e:
        raise FrameError("ASCII가 아닌 문자") from e
    if want != cs.upper():
        raise FrameError(f"체크섬 불일치 (수신 {cs}, 계산 {want})")
    fields = body.split(",")
    if fields[0] != "POS" or len(fields) != 6:
        raise FrameError("POS 문장이 아님")
    if not fields[1]:
        raise FrameError("장비 ID 없음")
    try:
        lat, lon, spd, hdg = (float(x) for x in fields[2:])
    except ValueError as e:
        raise FrameError(f"숫자 형식 오류: {e}") from e
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise FrameError("좌표 범위 밖")
    if spd < 0 or not (0 <= hdg <= 360):
        raise FrameError("속도·방위 범위 밖")
    return PosReport(fields[1], lat, lon, spd, hdg)
