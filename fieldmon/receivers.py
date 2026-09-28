"""세 가지 수신 경로. 모두 받은 줄을 sink(line, source)로 넘긴다.

- UDP   : 무선 모뎀·데이터링크가 데이터그램으로 보내는 경우
- TCP   : 게이트웨이가 스트림으로 붙는 경우. 줄 단위로 끊어 읽어 프레임이 나뉘어 와도 안전
- 시리얼: RS-232C/USB-시리얼 직결. 블로킹 I/O라 별도 스레드에서 읽고 이벤트 루프로 넘긴다
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Callable

log = logging.getLogger("fieldmon.rx")
Sink = Callable[[str, str], object]
MAX_LINE = 512   # 한 줄이 이보다 길면 비정상 스트림으로 보고 연결을 끊는다


class _UdpProto(asyncio.DatagramProtocol):
    def __init__(self, sink: Sink) -> None:
        self.sink = sink

    def datagram_received(self, data: bytes, addr) -> None:
        for line in data.decode("ascii", "replace").splitlines():
            self.sink(line, "UDP")


async def start_udp(sink: Sink, port: int, host: str = "0.0.0.0"):
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(lambda: _UdpProto(sink), local_addr=(host, port))
    log.info("UDP 수신 대기 :%d", port)
    return transport


async def start_tcp(sink: Sink, port: int, host: str = "0.0.0.0"):
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        log.info("TCP 접속 %s", peer)
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                sink(line.decode("ascii", "replace"), "TCP")
        except (asyncio.LimitOverrunError, ValueError):
            log.warning("TCP %s: 줄 길이 초과, 연결 종료", peer)
        except ConnectionError as e:
            log.warning("TCP %s: %s", peer, e)
        finally:
            writer.close()
            log.info("TCP 종료 %s", peer)

    server = await asyncio.start_server(handle, host, port, limit=MAX_LINE)
    log.info("TCP 수신 대기 :%d", port)
    return server


def start_serial(sink: Sink, device: str, baud: int, loop: asyncio.AbstractEventLoop,
                 stop: threading.Event | None = None) -> threading.Thread:
    """시리얼 포트 수신 스레드. 케이블 분리 등으로 포트가 사라지면 3초 뒤 다시 연다."""
    import serial  # pyserial

    stop = stop or threading.Event()

    def run() -> None:
        while not stop.is_set():
            try:
                with serial.Serial(device, baud, timeout=1) as port:
                    log.info("시리얼 열림 %s @%d", device, baud)
                    while not stop.is_set():
                        raw = port.readline()
                        if raw:
                            loop.call_soon_threadsafe(sink, raw.decode("ascii", "replace"), "SERIAL")
            except Exception as e:  # noqa: BLE001  포트 오류는 모두 재시도 대상
                log.warning("시리얼 %s 오류: %s (3초 후 재시도)", device, e)
                stop.wait(3)

    th = threading.Thread(target=run, name="serial-reader", daemon=True)
    th.start()
    return th
