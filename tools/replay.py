"""기록 재생 + 통신 이상 주입 도구.

FrameRecorder가 남긴 기록을 원래 시간 간격대로 다시 보낸다. 재생하면서
통신 두절·프레임 손상·중복 수신을 일부러 섞어, 수신 소프트웨어가 이상 상황을
어떻게 처리하는지 반복해서 시험할 수 있다.

    python -m tools.replay logs/recordings/rec-20260928-154000.jsonl
    python -m tools.replay rec.jsonl --speed 4                     # 4배속
    python -m tools.replay rec.jsonl --drop UNIT-02:20-40          # 20~40초 구간 UNIT-02 두절
    python -m tools.replay rec.jsonl --corrupt 0.05 --dup 0.02     # 5% 손상, 2% 중복
    python -m tools.replay rec.jsonl --via tcp --seed 7            # 모두 TCP로, 같은 난수로 재현

재생 계획(plan)은 순수 함수라 같은 기록·옵션·seed면 항상 같은 결과가 나온다.
"""
from __future__ import annotations

import argparse
import json
import random
import socket
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Record:
    t: float
    src: str
    line: str


@dataclass(frozen=True)
class DropWindow:
    unit: str
    start: float
    end: float

    @classmethod
    def parse(cls, text: str) -> "DropWindow":
        """'UNIT-02:20-40' -> UNIT-02를 20초부터 40초까지 두절 ('UNIT-02:20-'는 끝까지)"""
        try:
            unit, span = text.rsplit(":", 1)
            a, b = span.split("-", 1)
            return cls(unit, float(a), float(b) if b else float("inf"))
        except ValueError as e:
            raise argparse.ArgumentTypeError(f"형식은 UNIT:시작-끝 (예: UNIT-02:20-40): {text}") from e

    def hits(self, rec: Record) -> bool:
        return self.start <= rec.t < self.end and unit_of(rec.line) == self.unit


@dataclass(frozen=True)
class Send:
    at: float        # 재생 시작 후 보낼 시각(초, 배속 반영)
    via: str         # UDP / TCP
    line: str
    note: str = ""   # corrupt / dup (요약 출력용)


def load(path: str | Path) -> list[Record]:
    out = []
    with open(path, encoding="utf-8") as f:
        for n, raw in enumerate(f, 1):
            if not raw.strip():
                continue
            d = json.loads(raw)
            if d.get("type") == "header":
                if d.get("version") != 1:
                    raise ValueError(f"지원하지 않는 기록 형식 버전: {d.get('version')}")
                continue
            out.append(Record(float(d["t"]), d["src"], d["line"]))
    return sorted(out, key=lambda r: r.t)


def unit_of(line: str) -> str:
    parts = line.split(",")
    return parts[1] if len(parts) > 1 else ""


def corrupt(line: str) -> str:
    """체크섬 두 자리를 틀린 값으로 바꾼다"""
    if "*" not in line:
        return line + "*00"
    body, cs = line.rsplit("*", 1)
    return f"{body}*{'00' if cs.upper() != '00' else 'FF'}"


def plan(records: list[Record], *, speed: float = 1.0, drops: tuple[DropWindow, ...] = (),
         corrupt_rate: float = 0.0, dup_rate: float = 0.0, via: str | None = None,
         seed: int | None = None) -> list[Send]:
    if speed <= 0:
        raise ValueError("speed는 0보다 커야 함")
    rng = random.Random(seed)
    out: list[Send] = []
    t0 = records[0].t if records else 0.0
    for r in records:
        if any(d.hits(r) for d in drops):
            continue
        at = (r.t - t0) / speed
        ch = (via or r.src).upper()
        ch = "UDP" if ch == "SERIAL" else ch          # 시리얼 기록은 기본적으로 UDP로 보낸다
        line, note = r.line, ""
        if corrupt_rate and rng.random() < corrupt_rate:
            line, note = corrupt(line), "corrupt"
        out.append(Send(at, ch, line, note))
        if dup_rate and rng.random() < dup_rate:
            out.append(Send(at, ch, line, "dup"))
    return out


def run(sends: list[Send], host: str, udp_port: int, tcp_port: int) -> None:
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tcp = socket.create_connection((host, tcp_port)) if any(s.via == "TCP" for s in sends) else None
    start = time.monotonic()
    try:
        for s in sends:
            wait = s.at - (time.monotonic() - start)
            if wait > 0:
                time.sleep(wait)
            data = (s.line + "\n").encode("ascii", "replace")
            if s.via == "TCP":
                tcp.sendall(data)
            else:
                udp.sendto(data, (host, udp_port))
    finally:
        udp.close()
        if tcp:
            tcp.close()


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="수신 기록 재생 + 통신 이상 주입")
    p.add_argument("record")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--udp", type=int, default=5600)
    p.add_argument("--tcp", type=int, default=5601)
    p.add_argument("--speed", type=float, default=1.0, help="재생 배속")
    p.add_argument("--drop", type=DropWindow.parse, action="append", default=[], metavar="UNIT:시작-끝")
    p.add_argument("--corrupt", type=float, default=0.0, help="체크섬 손상 비율 (0~1)")
    p.add_argument("--dup", type=float, default=0.0, help="중복 전송 비율 (0~1)")
    p.add_argument("--via", choices=["udp", "tcp"], help="모든 프레임을 한 경로로 보냄")
    p.add_argument("--seed", type=int, help="난수 고정 (같은 이상 패턴 재현)")
    p.add_argument("--dry-run", action="store_true", help="보내지 않고 계획만 요약")
    a = p.parse_args(argv)

    recs = load(a.record)
    sends = plan(recs, speed=a.speed, drops=tuple(a.drop), corrupt_rate=a.corrupt,
                 dup_rate=a.dup, via=a.via, seed=a.seed)
    dur = sends[-1].at if sends else 0
    notes = {k: sum(1 for s in sends if s.note == k) for k in ("corrupt", "dup")}
    print(f"기록 {len(recs)}건 → 전송 {len(sends)}건 (두절로 제외 {len(recs) - len(sends) + notes['dup']}건, "
          f"손상 {notes['corrupt']}건, 중복 {notes['dup']}건), 약 {dur:.1f}초")
    if not a.dry_run:
        run(sends, a.host, a.udp, a.tcp)
        print("재생 완료")


if __name__ == "__main__":
    main()
