#!/usr/bin/env bash
# 시연용 단축 명령. 사용법: ./demo.sh help
cd "$(dirname "$0")"
PY=python3; [ -x .venv/bin/python ] && PY=.venv/bin/python
mkdir -p logs

stop_devices() { [ -f logs/devices.pid ] && kill "$(cat logs/devices.pid)" 2>/dev/null; rm -f logs/devices.pid; }

case "$1" in
  start)    # 서버 + 장비 3대 (UNIT-02는 20초 뒤 송신 중단, 3% 오류 프레임)
    "$0" stop >/dev/null
    nohup $PY -m uvicorn fieldmon.app:app --port 8080 > logs/server.log 2>&1 &
    echo $! > logs/server.pid
    sleep 2
    nohup $PY -m sim.devices --drop UNIT-02 --drop-after 20 --noise 0.03 > logs/devices.log 2>&1 &
    echo $! > logs/devices.pid
    echo "화면: http://localhost:8080   (20초 뒤 UNIT-02 통신 두절)"
    ;;
  devices)  # 장비 송신 재시작 (두절 없이) → UNIT-02 통신 복구
    stop_devices
    nohup $PY -m sim.devices > logs/devices.log 2>&1 &
    echo $! > logs/devices.pid
    echo "장비 송신 재시작 → UNIT-02 통신 복구"
    ;;
  replay)   # 직전 기록을 4배속 재생, UNIT-01 10~30초 두절 + 5% 프레임 손상 주입
    stop_devices
    REC=$(ls -t logs/recordings/*.jsonl 2>/dev/null | sed -n 2p)
    [ -z "$REC" ] && REC=$(ls -t logs/recordings/*.jsonl 2>/dev/null | head -1)
    [ -z "$REC" ] && { echo "기록이 없습니다. 먼저 ./demo.sh start"; exit 1; }
    $PY -m tools.replay "$REC" --speed 4 --drop UNIT-01:10-30 --corrupt 0.05 --seed 1
    ;;
  stop)
    stop_devices
    [ -f logs/server.pid ] && kill "$(cat logs/server.pid)" 2>/dev/null; rm -f logs/server.pid
    echo "중지"
    ;;
  *)
    cat <<USAGE
./demo.sh start     서버 + 장비 3대 시작 (UNIT-02 20초 뒤 두절)
./demo.sh devices   장비 송신 재시작 (두절 복구)
./demo.sh replay    직전 기록을 4배속 재생 + UNIT-01 두절·5% 프레임 손상 주입
./demo.sh stop      모두 중지
USAGE
    ;;
esac
