import sqlite3

from fieldmon import app as appmod
from tools.build_mbtiles import build, lonlat_to_px, tile_range, xyz_to_tms_row


def _mbtiles(path):
    db = sqlite3.connect(path)
    db.executescript("CREATE TABLE metadata(name TEXT, value TEXT);"
                     "CREATE TABLE tiles(zoom_level INT, tile_column INT, tile_row INT, tile_data BLOB);")
    db.execute("INSERT INTO metadata VALUES('name','test'),('minzoom','1'),('maxzoom','1')")
    db.execute("INSERT INTO tiles VALUES(1, 0, 1, ?)", (b"PNGDATA",))   # TMS row 1 == XYZ y 0
    db.commit()
    db.close()


def test_tile_route_flips_y(tmp_path, monkeypatch):
    path = tmp_path / "t.mbtiles"
    _mbtiles(path)
    monkeypatch.setattr(appmod.config, "MBTILES_PATH", str(path))
    assert appmod.tile(1, 0, 0).body == b"PNGDATA"
    assert appmod.tile(1, 0, 1).status_code == 204          # 없는 타일은 빈 응답
    assert appmod.tiles_info()["name"] == "test"


def test_missing_mbtiles(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod.config, "MBTILES_PATH", str(tmp_path / "none.mbtiles"))
    assert appmod.tiles_info() == {"available": False}


def test_projection_matches_known_tile():
    # 줌 0: 전 세계가 256px 한 장. (0,0)은 중앙
    assert lonlat_to_px(0, 0, 0) == (128.0, 128.0)
    # 서울시청 근처는 줌 10에서 x=873, y=396 타일 (OSM 타일 번호 규칙)
    x, y = lonlat_to_px(126.978, 37.566, 10)
    assert (int(x // 256), int(y // 256)) == (873, 396)
    assert xyz_to_tms_row(10, 396) == 627


def test_build_small_mbtiles(tmp_path):
    gj = tmp_path / "a.geojson"
    gj.write_text('{"type":"FeatureCollection","features":[{"type":"Feature","properties":{"name":"A"},'
                  '"geometry":{"type":"Polygon","coordinates":[[[126.6,37.38],[126.7,37.38],[126.7,37.42],'
                  '[126.6,37.42],[126.6,37.38]]]}}]}', encoding="utf-8")
    out = tmp_path / "o.mbtiles"
    bbox = (126.6, 37.38, 126.7, 37.42)
    n = build(str(gj), str(out), bbox, 11, 12, name="t", attribution="a", font_path=None)
    expect = sum(len(xs) * len(ys) for xs, ys in (tile_range(bbox, z) for z in (11, 12)))
    db = sqlite3.connect(out)
    assert n == expect == db.execute("SELECT COUNT(*) FROM tiles").fetchone()[0]
    assert db.execute("SELECT tile_data FROM tiles LIMIT 1").fetchone()[0][:4] == b"\x89PNG"
