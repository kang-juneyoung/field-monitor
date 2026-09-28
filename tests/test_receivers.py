import asyncio
import socket

from fieldmon.protocol import encode_pos
from fieldmon.receivers import MAX_LINE, start_tcp, start_udp
from fieldmon.tracker import FieldTracker


def _free_port(kind):
    s = socket.socket(socket.AF_INET, kind)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_udp_and_tcp_with_split_frames():
    async def run():
        t = FieldTracker()
        up, tp = _free_port(socket.SOCK_DGRAM), _free_port(socket.SOCK_STREAM)
        udp = await start_udp(t.feed, up, "127.0.0.1")
        tcp = await start_tcp(t.feed, tp, "127.0.0.1")
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # 데이터그램 하나에 두 줄이 같이 오는 경우
        s.sendto((encode_pos("A", 37.0, 126.0, 1, 1) + "\n" + encode_pos("C", 37.2, 126.2, 1, 1)).encode(),
                 ("127.0.0.1", up))
        r, w = await asyncio.open_connection("127.0.0.1", tp)
        line = encode_pos("B", 37.1, 126.1, 1, 1) + "\n"
        w.write(line[:10].encode())            # 프레임이 두 번에 나뉘어 도착하는 경우
        await w.drain()
        await asyncio.sleep(0.05)
        w.write(line[10:].encode())
        await w.drain()
        await asyncio.sleep(0.2)
        w.close()
        s.close()
        udp.close()
        tcp.close()
        return {u["unit"]: u["source"] for u in t.snapshot()["units"]}

    assert asyncio.run(run()) == {"A": "UDP", "B": "TCP", "C": "UDP"}


def test_tcp_overlong_line_closes_connection():
    async def run():
        t = FieldTracker()
        tp = _free_port(socket.SOCK_STREAM)
        tcp = await start_tcp(t.feed, tp, "127.0.0.1")
        r, w = await asyncio.open_connection("127.0.0.1", tp)
        w.write(b"X" * (MAX_LINE * 4))         # 줄바꿈 없이 계속 들어오는 비정상 스트림
        await w.drain()
        data = await asyncio.wait_for(r.read(), 2)   # 서버가 끊으면 EOF
        tcp.close()
        return data

    assert asyncio.run(run()) == b""
