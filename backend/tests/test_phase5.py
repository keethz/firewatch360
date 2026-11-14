"""Phase 5: provisional (NRT) months, current month-to-date, live FIRMS proxy. Synthetic data only."""
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services import analytics as an
from app.services.aoi import AOI, parse_aoi
from app.services.harmonize import HarmonizationParams, Harmonizer, monthly_cell_days
from app.services.nrt import NrtClient, NrtError, to_feature
from tests.test_phase1 import make_store, row

P = HarmonizationParams()


# ---------------------------------------------------------------- static-source mask

def test_nrt_mask_drops_known_static_cells():
    store = make_store([
        row("VIIRS", 40.005, -100.005, "2015-07-01", typ=2),            # archive: static source cell
        row("VIIRS", 40.006, -100.004, "2026-09-01", typ=None, level="nrt"),  # same cell, NRT -> dropped
        row("VIIRS", 41.005, -100.005, "2026-09-01", typ=None, level="nrt"),  # clean cell -> kept
        row("VIIRS", 42.005, -100.005, "2026-09-01", typ=0, level="standard"),
        row("VIIRS", 43.005, -100.005, "2026-09-01", typ=2, level="standard"),  # typed static -> dropped
    ])
    c = monthly_cell_days(store, AOI("region"), P, levels=("standard", "nrt"))
    assert c[("VIIRS", "2026-09")] == 2
    loose = HarmonizationParams(vegetation_only=False)
    assert monthly_cell_days(store, AOI("region"), loose, levels=("standard", "nrt"))[("VIIRS", "2026-09")] == 4


# ---------------------------------------------------------------- provisional months + current month

def nrt_store():
    """Final data 2012-02..2017-12 (VIIRS 20 cells/month mid-month, MODIS 10).
    NRT VIIRS for 2018-01..2018-02 (30 cells) and 2018-03-01..03-04; NRT MODIS to 2018-03-10."""
    rows = []
    for y in range(2012, 2018):
        for m in range(1, 13):
            if (y, m) < (2012, 2):
                continue
            for i in range(10):
                rows.append(row("MODIS", 36.0 + i * 0.02, -119.0, f"{y}-{m:02d}-02"))
            for i in range(20):
                rows.append(row("VIIRS", 36.0 + i * 0.02, -118.0, f"{y}-{m:02d}-02"))
    rows += [row("VIIRS", 37.0, -117.0, "2012-01-20"), row("MODIS", 36.0, -119.0, "2017-12-31"),
             row("VIIRS", 36.0, -118.0, "2017-12-31")]
    for m in ("2018-01", "2018-02"):
        for i in range(30):
            rows.append(row("VIIRS", 36.0 + i * 0.02, -118.0, f"{m}-02", typ=None, level="nrt"))
    for d in ("2018-03-02", "2018-03-04"):
        rows.append(row("VIIRS", 36.0, -118.0, d, typ=None, level="nrt"))
    for d in ("2018-03-02", "2018-03-09", "2018-03-10"):
        for i in range(5):
            rows.append(row("MODIS", 36.0 + i * 0.02, -119.0, d, typ=None, level="nrt"))
    return make_store(rows)


def test_provisional_windows_and_rows():
    h = Harmonizer(nrt_store())
    w = h.windows()
    assert w["series_end"] == "2017-12"
    # last NRT day is dropped as possibly incomplete
    assert w["nrt_as_of"] == {"VIIRS": "2018-03-03", "MODIS": "2018-03-09"}
    assert w["provisional_end"] == "2018-02"
    assert w["current"] == {"month": "2018-03", "through": "2018-03-09", "sensor": "MODIS"}
    s = h.harmonized(AOI("region"), P)["series"]
    prov = [r for r in s if r["provisional"]]
    assert [r["month"] for r in prov] == ["2018-01", "2018-02"]
    assert all(r["source"] == "VIIRS_provisional" and r["harmonized"] == 30.0 for r in prov)
    assert an.complete_years(s)[-1] == 2017  # provisional months never complete a year


def test_current_month_to_date_uses_same_days_and_scales_modis():
    h = Harmonizer(nrt_store())
    k = h.calibration(AOI("region"), P)["calibration"]["k"]
    cur = h.current_month(AOI("region"), P, list(range(2013, 2018)))
    assert cur["sensor"] == "MODIS" and cur["days"] == 9
    assert cur["raw_cell_days"] == 10  # 03-02 and 03-09 x 5 cells; 03-10 dropped
    assert cur["value"] == pytest.approx(10 * k)
    # history: the same days 1..9 of March each year -> the day-2 VIIRS scene, 20 cells
    assert [r["value"] for r in cur["baseline_rows"]] == [20.0] * 5
    mtd = an.month_to_date(cur, 2.0)
    assert mtd["median"] == 20.0 and mtd["status"] == "provisional"
    assert mtd["z"] is None  # MAD = 0 -> undefined, never flagged
    assert an.month_to_date({**cur, "baseline_rows": cur["baseline_rows"][:3]}, 2.0)["history_status"] == "insufficient_history"


def test_latest_vs_median_labels_provisional():
    rows = [{"month": "2026-10", "harmonized": 5.0, "clim_median": 4.0, "pct_vs_median": 25.0, "z": 0.5,
             "anomaly": None, "provisional": True}]
    out = an.latest_vs_median(rows)
    assert out["provisional"] and out["data_status"] == "near-real-time, provisional"


# ---------------------------------------------------------------- live FIRMS proxy

CSV_V = "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight\n"
CSV_M = "latitude,longitude,brightness,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_t31,frp,daynight\n"


def mock_transport(calls):
    def handler(req: httpx.Request):
        calls.append(str(req.url))
        if "VIIRS" in req.url.path:
            body = CSV_V + "34.0,-118.0,330,0.4,0.4,2026-11-13,0930,N,VIIRS,n,2.0NRT,290,5.1,N\n" \
                           "34.0,-118.0,330,0.4,0.4,2026-11-13,0931,N,VIIRS,l,2.0NRT,290,1.0,N\n" \
                           "35.5,-118.0,330,0.4,0.4,2026-11-14,0930,N,VIIRS,h,2.0NRT,290,9.0,N\n"
        else:
            body = CSV_M + "34.01,-118.01,320,1,1,2026-11-14,1830,T,MODIS,85,6.1NRT,290,20.0,D\n"
        return httpx.Response(200, text=body)
    return httpx.MockTransport(handler)


def test_nrt_client_chunks_caches_and_filters(tmp_path):
    calls: list[str] = []
    c = NrtClient("SECRETKEY", tmp_path, transport=mock_transport(calls), today=date(2026, 11, 14))
    assert c._chunks(7) == [(date(2026, 11, 8), 5), (date(2026, 11, 13), 2)]
    circle = parse_aoi(lat=34.0, lon=-118.0, radius_km=50)
    rows = c.fetch(circle, 3, ["VIIRS", "MODIS"])
    assert len(calls) == 2  # one chunk per sensor
    assert {(r["sensor"], r["latitude"]) for r in rows} == {("VIIRS", "34.0"), ("MODIS", "34.01")}  # 35.5 outside
    c.fetch(circle, 3, ["VIIRS", "MODIS"])
    assert len(calls) == 2  # served from the disk cache
    region_rows = c.fetch(AOI("region"), 3, ["VIIRS"])
    assert len(calls) == 2 + 3  # three US boxes
    assert len(region_rows) == 9


def test_nrt_feature_normalizes_confidence():
    v = to_feature({"sensor": "VIIRS", "latitude": "1", "longitude": "2", "confidence": "h", "acq_date": "2026-11-14",
                    "acq_time": "930", "frp": "3.5", "daynight": "D"})
    m = to_feature({"sensor": "MODIS", "latitude": "1", "longitude": "2", "confidence": "25", "acq_date": "2026-11-14",
                    "acq_time": "1830", "frp": "", "daynight": "N"})
    assert v["properties"]["confidence"] == "high" and v["properties"]["time_utc"] == "0930"
    assert m["properties"]["confidence"] == "low" and m["properties"]["frp"] is None


def test_nrt_errors_do_not_leak_key(tmp_path):
    t = httpx.MockTransport(lambda req: httpx.Response(400, text="Invalid MAP_KEY."))
    c = NrtClient("SECRETKEY", tmp_path, transport=t, today=date(2026, 11, 14))
    with pytest.raises(NrtError) as e:
        c.fetch(AOI("region"), 1, ["MODIS"])
    assert "SECRETKEY" not in str(e.value)
    with pytest.raises(NrtError):
        NrtClient("", tmp_path).fetch(AOI("region"), 1, ["MODIS"])


def test_api_nrt_with_mock(tmp_path):
    app = create_app()
    calls: list[str] = []
    app.state.nrt = NrtClient("SECRETKEY", tmp_path, transport=mock_transport(calls), today=date(2026, 11, 14))
    client = TestClient(app)
    j = client.get("/api/nrt?lat=34&lon=-118&radius_km=50&days=2").json()
    assert j["total"] == 2  # the low-confidence VIIRS row is filtered at min_confidence=nominal
    assert j["counts"] == {"VIIRS": 1, "MODIS": 1}
    assert all(f["properties"]["live"] for f in j["features"])
    assert "SECRETKEY" not in str(j)
    assert client.get("/api/nrt?days=11").status_code == 422


def test_api_current_real_data():
    client = TestClient(create_app())
    r = client.get("/api/current")
    if r.status_code == 503:
        pytest.skip("processed data not built on this machine")
    cur = r.json()["current_month"]
    if cur is None:
        pytest.skip("no partial NRT month in the loaded data")
    assert cur["status"] == "provisional" and cur["n_baseline_years"] >= 5
    assert cur["through"] < r.json()["windows"]["nrt_as_of"][cur["sensor"]] or \
        cur["through"] == r.json()["windows"]["nrt_as_of"][cur["sensor"]]
