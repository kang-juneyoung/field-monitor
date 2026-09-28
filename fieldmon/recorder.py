"""수신 프레임 기록기.

받은 줄을 오류 프레임까지 포함해 그대로 JSON Lines로 남긴다.
기록은 tools/replay.py로 같은 시간 간격 그대로 다시 보낼 수 있어,
현장에서 겪은 통신 이상을 사무실에서 재현하는 시험 자료가 된다.

    {"type": "header", "version": 1, "started": "2026-09-28T15:40:00+09:00"}
    {"t": 0.000, "src": "UDP", "line": "$POS,UNIT-01,...*5A"}
    {"t": 0.512, "src": "TCP", "line": "$POS,UNIT-02,...*3C"}

t는 기록 시작부터의 경과 초(단조 시계)라 시스템 시각이 바뀌어도 간격이 흔들리지 않는다.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, TextIO

FORMAT_VERSION = 1


class FrameRecorder:
    def __init__(self, path: str | Path, clock: Callable[[], float] = time.monotonic) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.t0 = clock()
        self.count = 0
        self._f: TextIO = open(self.path, "w", encoding="utf-8", buffering=1)  # 줄 단위 flush
        self._f.write(json.dumps({"type": "header", "version": FORMAT_VERSION,
                                  "started": datetime.now().astimezone().isoformat(timespec="seconds")}) + "\n")

    def write(self, line: str, source: str) -> None:
        line = line.rstrip("\r\n")
        if not line:
            return
        rec = {"t": round(self.clock() - self.t0, 3), "src": source, "line": line}
        self._f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.count += 1

    def close(self) -> None:
        self._f.close()

    @staticmethod
    def default_path(directory: str | Path) -> Path:
        return Path(directory) / datetime.now().strftime("rec-%Y%m%d-%H%M%S.jsonl")
