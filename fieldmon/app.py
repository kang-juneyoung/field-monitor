"""현장 장비 위치 관제 서버 (FastAPI).

    uvicorn fieldmon.app:app --port 8080     →  http://localhost:8080

수신(UDP·TCP·시리얼) → 기록(JSONL) + 상태 반영 → 웹소켓으로 지도 화면에 전달.
지도 타일은 MBTiles 파일에서 직접 꺼내 주므로 인터넷이 필요 없다.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from fieldmon import config
from fieldmon.receivers import start_serial, start_tcp, start_udp
from fieldmon.recorder import FrameRecorder
from fieldmon.tracker import FieldTracker

log = logging.getLogger("fieldmon")
STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
    loop = asyncio.get_running_loop()
    tracker = FieldTracker(config.LOST_AFTER)
    recorder = FrameRecorder(FrameRecorder.default_path(config.RECORD_DIR)) if config.RECORD_DIR else None
    if recorder:
        log.info("수신 기록: %s", recorder.path)

    def sink(line: str, source: str) -> None:
        if recorder:
            recorder.write(line, source)
        tracker.feed(line, source)

    clients: set[WebSocket] = set()
    closers = [await start_udp(sink, config.UDP_PORT), await start_tcp(sink, config.TCP_PORT)]
    stop_serial = threading.Event()
    if config.SERIAL:
        start_serial(sink, config.SERIAL, config.BAUD, loop, stop_serial)

    app.state.tracker, app.state.recorder, app.state.clients = tracker, recorder, clients
    tasks = [asyncio.create_task(_sweeper(tracker)), asyncio.create_task(_pusher(tracker, clients))]
    yield
    for t in tasks:
        t.cancel()
    stop_serial.set()
    for c in closers:
        c.close()
    if recorder:
        recorder.close()


async def _sweeper(tracker: FieldTracker) -> None:
    while True:
        await asyncio.sleep(1)
        tracker.sweep()


async def _pusher(tracker: FieldTracker, clients: set[WebSocket]) -> None:
    """상태가 바뀐 경우에만 0.3초 간격으로 모아서 전송 (수신이 몰려도 화면 부하 일정)"""
    sent = -1
    while True:
        await asyncio.sleep(0.3)
        if tracker.version == sent or not clients:
            continue
        sent = tracker.version
        snap = tracker.snapshot()
        for ws in list(clients):
            try:
                await ws.send_json(snap)
            except Exception:  # noqa: BLE001  끊어진 화면은 목록에서 제거
                clients.discard(ws)


app = FastAPI(title="field-monitor", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/units")
def units(request: Request):
    return request.app.state.tracker.snapshot()


@app.get("/api/record")
def record_status(request: Request):
    r = request.app.state.recorder
    return {"recording": bool(r), "path": str(r.path) if r else None, "frames": r.count if r else 0}


@app.websocket("/ws")
async def ws_updates(ws: WebSocket):
    await ws.accept()
    ws.app.state.clients.add(ws)
    await ws.send_json(ws.app.state.tracker.snapshot())
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        ws.app.state.clients.discard(ws)


# --------------------------------------------------------------------------- 오프라인 지도 타일
def _open_tiles():
    path = config.MBTILES_PATH
    if not os.path.exists(path):
        return None
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


@app.get("/api/tiles/info")
def tiles_info():
    db = _open_tiles()
    if db is None:
        return {"available": False}
    with db:
        meta = dict(db.execute("SELECT name, value FROM metadata").fetchall())
    db.close()
    keys = ("name", "minzoom", "maxzoom", "bounds", "center", "attribution")
    return {"available": True, **{k: meta.get(k) for k in keys}}


@app.get("/tiles/{z}/{x}/{y}.png")
def tile(z: int, x: int, y: int):
    """MBTiles는 TMS 좌표계(y가 아래에서 위로 증가)라 웹 지도(XYZ)의 y를 뒤집어 조회한다."""
    db = _open_tiles()
    if db is None:
        raise HTTPException(404)
    tms_y = (1 << z) - 1 - y
    with db:
        row = db.execute("SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
                         (z, x, tms_y)).fetchone()
    db.close()
    if not row:
        return Response(status_code=204)
    return Response(row[0], media_type="image/png", headers={"Cache-Control": "max-age=86400"})
