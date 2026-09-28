"""행정구역 경계(GeoJSON)로 오프라인 래스터 지도(MBTiles)를 만든다.

    python -m tools.build_mbtiles data/source/songdo_dong_2013.geojson \
        --bbox 126.58,37.33,126.76,37.47 --zoom 11-16 -o data/songdo.mbtiles

- 인터넷 타일 서버를 쓰지 않는다. 공개 벡터 데이터를 직접 그려 PNG 타일로 만든다.
- 바다(배경) 위에 육지(행정구역 다각형)를 칠하고 경계선과 이름을 그린다.
- 2배 크기로 그린 뒤 축소해 선의 계단 현상을 줄인다.
- MBTiles 규격: SQLite 한 파일, tiles(zoom_level, tile_column, tile_row, tile_data),
  tile_row는 TMS 방식(아래에서 위로)이라 XYZ의 y를 뒤집어 저장한다.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import sqlite3
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

TILE = 256
SS = 2                                  # 슈퍼샘플링 배율
SEA, LAND, EDGE, TEXT = (207, 227, 242), (245, 242, 234), (190, 178, 160), (95, 88, 78)


# ----------------------------------------------------------------------------- 좌표 변환
def lonlat_to_px(lon: float, lat: float, z: int) -> tuple[float, float]:
    """위경도 → 줌 z의 전역 픽셀 좌표 (웹 메르카토르)"""
    n = TILE * (1 << z)
    x = (lon + 180.0) / 360.0 * n
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return x, y


def tile_range(bbox: tuple[float, float, float, float], z: int) -> tuple[range, range]:
    x0, y1 = lonlat_to_px(bbox[0], bbox[1], z)
    x1, y0 = lonlat_to_px(bbox[2], bbox[3], z)
    return range(int(x0 // TILE), int(x1 // TILE) + 1), range(int(y0 // TILE), int(y1 // TILE) + 1)


def xyz_to_tms_row(z: int, y: int) -> int:
    return (1 << z) - 1 - y


# ----------------------------------------------------------------------------- 데이터
def polygons(geom: dict) -> list[list[list[tuple[float, float]]]]:
    """Polygon/MultiPolygon → [폴리곤[링[(lon, lat)]]]"""
    if geom["type"] == "Polygon":
        return [geom["coordinates"]]
    if geom["type"] == "MultiPolygon":
        return geom["coordinates"]
    return []


def bounds(polys) -> tuple[float, float, float, float]:
    xs = [p[0] for poly in polys for ring in poly for p in ring]
    ys = [p[1] for poly in polys for ring in poly for p in ring]
    return min(xs), min(ys), max(xs), max(ys)


def label_point(polys) -> tuple[float, float]:
    """가장 큰 외곽 링의 면적 중심 (이름 표시 위치)"""
    ring = max((poly[0] for poly in polys), key=lambda r: abs(_area(r)))
    a = _area(ring) or 1e-12
    cx = cy = 0.0
    for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
        f = x0 * y1 - x1 * y0
        cx += (x0 + x1) * f
        cy += (y0 + y1) * f
    return cx / (6 * a), cy / (6 * a)


def _area(ring) -> float:
    return sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1])) / 2


def load_features(path: str | Path, bbox) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out = []
    for f in data["features"]:
        polys = polygons(f["geometry"])
        if not polys:
            continue
        b = bounds(polys)
        if b[2] < bbox[0] or b[0] > bbox[2] or b[3] < bbox[1] or b[1] > bbox[3]:
            continue
        out.append({"name": f["properties"].get("name", ""), "polys": polys, "bounds": b,
                    "label": label_point(polys)})
    return out


# ----------------------------------------------------------------------------- 그리기
def render_tile(features: list[dict], z: int, tx: int, ty: int, font_path: str | None) -> bytes:
    size = TILE * SS
    img = Image.new("RGB", (size, size), SEA)
    d = ImageDraw.Draw(img)
    ox, oy = tx * TILE, ty * TILE

    def to_img(lon, lat):
        x, y = lonlat_to_px(lon, lat, z)
        return (x - ox) * SS, (y - oy) * SS

    pad = 64  # 경계·글자가 타일 가장자리에서 잘리지 않도록 여유
    visible = []
    for f in features:
        b = f["bounds"]
        x0, y1 = to_img(b[0], b[1])
        x1, y0 = to_img(b[2], b[3])
        if x1 < -pad or y1 < -pad or x0 > size + pad or y0 > size + pad:
            continue
        visible.append(f)

    for f in visible:                                  # 1) 육지 채우기
        for poly in f["polys"]:
            d.polygon([to_img(*p) for p in poly[0]], fill=LAND)
            for hole in poly[1:]:
                d.polygon([to_img(*p) for p in hole], fill=SEA)
    width = max(1, SS * (1 if z < 14 else 2))
    for f in visible:                                  # 2) 경계선
        for poly in f["polys"]:
            for ring in poly:
                d.line([to_img(*p) for p in ring] + [to_img(*ring[0])], fill=EDGE, width=width, joint="curve")
    if z >= 12:                                        # 3) 이름
        font = _font(font_path, (11 if z < 14 else 13) * SS)
        for f in visible:
            x, y = to_img(*f["label"])
            if -pad <= x <= size + pad and -pad <= y <= size + pad:
                d.text((x, y), f["name"], fill=TEXT, font=font, anchor="mm",
                       stroke_width=SS, stroke_fill=LAND)
    img = img.resize((TILE, TILE), Image.LANCZOS)
    buf = io.BytesIO()
    img.quantize(colors=64).save(buf, "PNG", optimize=True)
    return buf.getvalue()


_fonts: dict = {}


def _font(path: str | None, size: int):
    key = (path, size)
    if key not in _fonts:
        _fonts[key] = ImageFont.truetype(path, size) if path else ImageFont.load_default()
    return _fonts[key]


# ----------------------------------------------------------------------------- MBTiles
def build(geojson: str, out: str, bbox, zmin: int, zmax: int, *, name: str, attribution: str,
          font_path: str | None) -> int:
    feats = load_features(geojson, bbox)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).unlink(missing_ok=True)
    db = sqlite3.connect(out)
    db.executescript("""
        CREATE TABLE metadata (name TEXT, value TEXT);
        CREATE TABLE tiles (zoom_level INTEGER, tile_column INTEGER, tile_row INTEGER, tile_data BLOB);
        CREATE UNIQUE INDEX tile_index ON tiles (zoom_level, tile_column, tile_row);
    """)
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    meta = {"name": name, "format": "png", "type": "baselayer", "version": "1",
            "minzoom": str(zmin), "maxzoom": str(zmax), "bounds": ",".join(map(str, bbox)),
            "center": f"{cx},{cy},{zmin + 2}", "attribution": attribution}
    db.executemany("INSERT INTO metadata VALUES (?, ?)", meta.items())
    n = 0
    for z in range(zmin, zmax + 1):
        xs, ys = tile_range(bbox, z)
        for x in xs:
            for y in ys:
                db.execute("INSERT INTO tiles VALUES (?, ?, ?, ?)",
                           (z, x, xyz_to_tms_row(z, y), render_tile(feats, z, x, y, font_path)))
                n += 1
    db.commit()
    db.close()
    return n


def main() -> None:
    p = argparse.ArgumentParser(description="GeoJSON 행정구역 → 오프라인 MBTiles")
    p.add_argument("geojson")
    p.add_argument("-o", "--out", default="data/map.mbtiles")
    p.add_argument("--bbox", default="126.58,37.33,126.76,37.47", help="서,남,동,북 (경위도)")
    p.add_argument("--zoom", default="11-16")
    p.add_argument("--name", default="송도 일대 행정구역")
    p.add_argument("--attribution", default="행정구역 경계: 통계청 센서스용 행정구역경계(2013), southkorea/southkorea-maps")
    p.add_argument("--font", help="한글 TTF/OTF/TTC 글꼴 경로 (예: NotoSansCJK, 맑은 고딕)")
    a = p.parse_args()
    bbox = tuple(float(v) for v in a.bbox.split(","))
    zmin, zmax = (int(v) for v in a.zoom.split("-"))
    n = build(a.geojson, a.out, bbox, zmin, zmax, name=a.name, attribution=a.attribution, font_path=a.font)
    print(f"{a.out}: 타일 {n}개 (줌 {zmin}~{zmax})")


if __name__ == "__main__":
    main()
