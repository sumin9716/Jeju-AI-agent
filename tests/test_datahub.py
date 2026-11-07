from datetime import date

import pandas as pd
import pytest

from jeju_agent.datahub import DataHubClient, DataHubError, DataStore, load_registry
from jeju_agent.datahub.client import render_params
from jeju_agent.datahub.loaders import normalize_dataframe


def test_registry_loads_and_finds():
    r = load_registry()
    assert len(r) >= 9
    assert r.find("753").id == "floating_population_emd"
    assert r.find("기상").id == "daily_weather"
    assert r.find("nope") is None


def test_render_params_templates():
    p = render_params({"startDate": "{{today-90d}}", "endDate": "{{today}}", "x": 1}, today=date(2026, 11, 7))
    assert p == {"startDate": "20260809", "endDate": "20261107", "x": 1}


def test_parse_payload_variants():
    rows, total = DataHubClient.parse_payload({"status": {"code": "200"}, "totCnt": "2", "data": [{"a": 1}, {"a": 2}]})
    assert len(rows) == 2 and total == 2
    rows, total = DataHubClient.parse_payload({"result": {"list": [{"b": 1}], "totalCount": 1}})
    assert rows == [{"b": 1}] and total == 1
    rows, total = DataHubClient.parse_payload([{"c": 1}])
    assert rows == [{"c": 1}] and total is None
    with pytest.raises(DataHubError):
        DataHubClient.parse_payload({"status": "FAIL", "message": "bad key"})
    with pytest.raises(DataHubError):
        DataHubClient.parse_payload({"message": "no list here"})


def test_client_requires_key_and_api_id():
    c = DataHubClient(project_key="")
    assert not c.configured
    with pytest.raises(DataHubError):
        c.build_url("abc")
    c2 = DataHubClient(project_key="KEY")
    assert c2.build_url("abc").endswith("/abc/KEY")
    with pytest.raises(DataHubError):
        c2.build_url("")


def test_client_pagination_with_fake_session(tmp_path):
    class FakeResp:
        def __init__(self, payload, status=200):
            self._payload, self.status_code, self.text = payload, status, "x"

        def json(self):
            return self._payload

    class FakeSession:
        def __init__(self):
            self.calls = []

        def get(self, url, params=None, timeout=None):
            self.calls.append(dict(params))
            page = params["number"]
            rows = [{"n": page * 10 + i} for i in range(2)] if page <= 3 else []
            return FakeResp({"totCnt": 6, "data": rows})

    sess = FakeSession()
    c = DataHubClient(project_key="K", cache_dir=tmp_path, page_size=2, session=sess, sleep_between_pages=0)
    res = c.fetch("API123", {"startDate": "20240101"})
    assert len(res.df) == 6 and res.total == 6 and res.pages == 3 and not res.from_cache
    cached = c.fetch("API123", {"startDate": "20240101"})
    assert cached.from_cache and len(cached.df) == 6
    assert sess.calls[0]["startDate"] == "20240101" and sess.calls[0]["limit"] == 2


def test_normalize_renames_and_types():
    r = load_registry()
    spec = r.get("floating_population_emd")
    # 2026-11-07 실제 응답 컬럼명 (baseDate, emd, visitPop …)
    df = pd.DataFrame({"baseDate": ["20240101", "20240102"], "emd": ["애월읍", "애월읍"], "visitPop": ["1,200", "900"]})
    out = normalize_dataframe(df, spec)
    assert list(out.columns) == ["날짜", "읍면동", "방문자수"]
    assert str(out["날짜"].dtype).startswith("datetime64")
    assert out["방문자수"].tolist() == [1200, 900]


def test_store_loads_samples_and_status():
    store = DataStore()
    status = {s["id"]: s for s in store.status()}
    assert status["floating_population_emd"]["sample"] is True
    ds = store.load("유동인구")
    assert ds.provenance == "sample" and ds.is_synthetic and ds.rows > 1000
    assert {"날짜", "읍면동", "방문자수"} <= set(ds.df.columns)
    with pytest.raises(KeyError):
        store.load("없는데이터")
    with pytest.raises(DataHubError):
        store.load("daily_weather", prefer="api")


def test_client_masks_project_key_in_errors():
    class FakeResp:
        status_code, text = 500, "boom key=SECRETKEY123"

        def json(self):
            raise ValueError("no json")

    class FakeSession:
        def get(self, url, params=None, timeout=None):
            return FakeResp()

    c = DataHubClient(project_key="SECRETKEY123", session=FakeSession(), sleep_between_pages=0)
    with pytest.raises(DataHubError) as ei:
        next(c.iter_pages("API"))
    assert "SECRETKEY123" not in str(ei.value) and "{PROJECT_KEY}" in str(ei.value)
