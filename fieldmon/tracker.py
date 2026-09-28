"""장비별 최신 위치·궤적·통신 상태 관리.

- feed()  : 수신한 한 줄을 검증해 반영. 오류 프레임은 버리고 경로별로 센다.
- sweep() : 일정 시간 수신이 없는 장비를 '통신 두절'로 바꾼다 (1초마다 호출).
- 이벤트 로그에는 상태 변화(최초 수신·두절·복구)만 남기고,
  프레임 오류는 경로별 카운터 + 최근 오류 목록으로 따로 관리한다.
  (오류가 많을 때 두절·복구 같은 중요한 이벤트가 밀려나지 않게 하기 위함)
"""
from __future__ import annotations

import time
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Callable

from fieldmon.protocol import FrameError, decode_pos

TRACK_LEN = 120


@dataclass
class Unit:
    unit: str
    lat: float = 0.0
    lon: float = 0.0
    speed: float = 0.0
    heading: float = 0.0
    source: str = ""
    last_seen: float = 0.0
    online: bool = True
    frames: int = 0
    track: deque = field(default_factory=lambda: deque(maxlen=TRACK_LEN))

    def to_dict(self) -> dict:
        return {"unit": self.unit, "lat": self.lat, "lon": self.lon, "speed": self.speed,
                "heading": self.heading, "source": self.source, "last_seen": self.last_seen,
                "online": self.online, "frames": self.frames, "track": list(self.track)}


class FieldTracker:
    def __init__(self, lost_after: float = 5.0, clock: Callable[[], float] = time.time) -> None:
        self.units: dict[str, Unit] = {}
        self.lost_after = lost_after
        self.now = clock
        self.version = 0                     # 화면 갱신 여부 판단용 (바뀔 때마다 증가)
        self.events: deque = deque(maxlen=50)
        self.errors: deque = deque(maxlen=20)
        self.bad_by_source: Counter = Counter()

    def _event(self, msg: str, level: str = "info") -> None:
        self.events.appendleft({"ts": self.now(), "level": level, "msg": msg})

    def feed(self, line: str, source: str) -> bool:
        if not line.strip():
            return False
        try:
            r = decode_pos(line)
        except FrameError as e:
            self.bad_by_source[source] += 1
            self.errors.appendleft({"ts": self.now(), "source": source, "msg": str(e)})
            self.version += 1
            return False
        u = self.units.get(r.unit)
        if u is None:
            u = self.units[r.unit] = Unit(r.unit)
            self._event(f"{r.unit} 최초 수신 ({source})")
        elif not u.online:
            self._event(f"{r.unit} 통신 복구 ({source}, {self.now() - u.last_seen:.0f}초 만)", "ok")
        elif u.source != source:
            self._event(f"{r.unit} 수신 경로 변경 {u.source} → {source}")
        u.lat, u.lon, u.speed, u.heading = r.lat, r.lon, r.speed, r.heading
        u.source, u.last_seen, u.online = source, self.now(), True
        u.frames += 1
        u.track.append([r.lat, r.lon])
        self.version += 1
        return True

    def sweep(self) -> bool:
        changed = False
        for u in self.units.values():
            if u.online and self.now() - u.last_seen > self.lost_after:
                u.online = False
                self._event(f"{u.unit} 통신 두절 ({self.lost_after:.0f}초 무수신)", "bad")
                changed = True
        if changed:
            self.version += 1
        return changed

    def snapshot(self) -> dict:
        return {"units": [u.to_dict() for u in sorted(self.units.values(), key=lambda x: x.unit)],
                "bad_frames": sum(self.bad_by_source.values()),
                "bad_by_source": dict(self.bad_by_source),
                "events": list(self.events), "errors": list(self.errors), "ts": self.now()}
