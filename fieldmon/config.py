"""환경 변수 설정 (기본값은 로컬 시연 기준)"""
import os

UDP_PORT = int(os.getenv("FIELD_UDP_PORT", "5600"))
TCP_PORT = int(os.getenv("FIELD_TCP_PORT", "5601"))
SERIAL = os.getenv("FIELD_SERIAL", "")                  # 예: /dev/ttyUSB0, COM3
BAUD = int(os.getenv("FIELD_BAUD", "9600"))
LOST_AFTER = float(os.getenv("FIELD_LOST_AFTER", "5"))  # 이 시간(초) 동안 수신 없으면 통신 두절
MBTILES_PATH = os.getenv("MBTILES_PATH", "data/songdo.mbtiles")
RECORD_DIR = os.getenv("RECORD_DIR", "logs/recordings")  # 빈 값이면 기록하지 않음
