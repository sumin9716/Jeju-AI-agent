"""제주데이터허브 Open API 동작을 흉내 낸 가짜 허브로 페이지 수집·오류·정밀 점검을 검증한다.

가짜 허브는 2026-11-07 실제 호출에서 확인한 형식을 따른다:
- startDate/endDate 가 없으면 HTTP 400 과 이스케이프된 JSON 메시지
- number(쪽)·limit(쪽당 건수) 페이지, totCnt 로 전체 건수
"""

from datetime import date, timedelta

import pytest

from jeju_agent.datahub import DataHubClient, DataHubError, DataStore, load_registry
from jeju_agent.datahub.apicheck import deep_check, format_report, check_apis

TODAY = date(2026, 11, 7)
BAD_PARAMS = '400 - "{\\"code\\":400,\\"status\\":\\"BAD_REQUEST\\",\\"message\\":\\"잘못된 파라미터 요청(\'startDate\' or \'endDate\')\\"}"'


class FakeResp:
    def __init__(self, status, payload=None, text="x"):
        self.status_code, self._payload, self.text = status, payload, text

    def json(self):
        return self._payload


class FakeHub:
    """하루 per_day 건, 최신 데이터는 today-lag 일, 서버 쪽당 상한 cap."""

    def __init__(self, days=40, per_day=5, lag=3, cap=20, ignore_page=False):
        self.rows = []
        last = TODAY - timedelta(days=lag)
        for d in range(days):
            day = (last - timedelta(days=d)).strftime("%Y%m%d")
            for i in range(per_day):
                self.rows.append({"baseDate": day, "emd": f"동{i}", "visitPop": str(100 + d * 10 + i)})
        self.rows.sort(key=lambda r: (r["baseDate"], r["emd"]))
        self.cap, self.ignore_page, self.calls = cap, ignore_page, []

    def get(self, url, params=None, timeout=None):
        p = dict(params or {})
        self.calls.append(p)
        s, e = p.get("startDate"), p.get("endDate")
        if not s or not e:
            return FakeResp(400, text=BAD_PARAMS)
        s, e = s.replace("-", ""), e.replace("-", "")
        sel = [r for r in self.rows if s <= r["baseDate"] <= e]
        size = min(int(p["limit"]), self.cap)
        page = 1 if self.ignore_page else int(p["number"])
        chunk = sel[(page - 1) * size : page * size]
        return FakeResp(200, {"code": 200, "totCnt": len(sel), "hasMore": page * size < len(sel), "data": chunk})


def _client(hub, tmp_path, page_size=50):
    return DataHubClient(project_key="KEY", session=hub, cache_dir=tmp_path, page_size=page_size, sleep_between_pages=0)


def test_fetch_detects_server_cap_and_collects_everything(tmp_path):
    hub = FakeHub(days=10, per_day=7, cap=20)
    res = _client(hub, tmp_path).fetch("API", {"startDate": "20261001", "endDate": "20261231"}, use_cache=False)
    assert res.total == 70 and len(res.df) == 70 and not res.truncated
    assert res.df["visitPop"].nunique() == 70  # 중복 없이 모두 수집
    assert [c["limit"] for c in hub.calls] == [50, 20, 20, 20]  # 첫 쪽에서 상한 20 을 감지


def test_repeated_page_is_an_error_not_duplicates(tmp_path):
    hub = FakeHub(days=10, per_day=7, cap=20, ignore_page=True)
    with pytest.raises(DataHubError, match="반복"):
        _client(hub, tmp_path, page_size=20).fetch("API", {"startDate": "20261001", "endDate": "20261231"}, use_cache=False)


def test_truncation_is_flagged(tmp_path):
    hub = FakeHub(days=10, per_day=7, cap=20)
    c = _client(hub, tmp_path, page_size=20)
    c.max_pages = 2
    res = c.fetch("API", {"startDate": "20261001", "endDate": "20261231"}, use_cache=False)
    assert res.truncated and len(res.df) == 40 and res.total == 70


def test_hub_400_message_is_readable(tmp_path):
    with pytest.raises(DataHubError) as ei:
        _client(FakeHub(), tmp_path).fetch_page("API", {})
    msg = str(ei.value)
    assert "HTTP 400" in msg and "잘못된 파라미터 요청('startDate' or 'endDate')" in msg and "\\\\" not in msg


def test_loader_reports_api_failure_reason_when_falling_back(tmp_path):
    class AlwaysBad:
        def get(self, url, params=None, timeout=None):
            return FakeResp(400, text=BAD_PARAMS)

    store = DataStore(client=DataHubClient(project_key="KEY", session=AlwaysBad(), cache_dir=tmp_path), raw_dir=tmp_path)
    ds = store.load("floating_population_emd")
    assert ds.provenance == "sample"
    assert "Open API" in ds.notes[0] and "잘못된 파라미터" in ds.notes[0] and "source='api'" in ds.notes[0]


def test_loader_api_rows_renamed_and_zero_rows_warned(tmp_path):
    hub = FakeHub(days=5, per_day=3, lag=3)
    store = DataStore(client=_client(hub, tmp_path), raw_dir=tmp_path)
    window = {"startDate": (TODAY - timedelta(days=7)).strftime("%Y%m%d"), "endDate": TODAY.strftime("%Y%m%d")}
    ds = store.load("floating_population_emd", params=window)
    assert ds.provenance == "api" and ds.rows == 15 and {"날짜", "읍면동", "방문자수"} <= set(ds.df.columns)
    assert str(ds.df["날짜"].dtype).startswith("datetime64") and ds.df["방문자수"].dtype.kind == "i"
    empty = store.load("floating_population_emd", params={"startDate": "20200101", "endDate": "20200107"})
    assert empty.provenance == "api" and empty.rows == 0 and any("0건" in n for n in empty.notes)


def test_deep_check_reports_paging_cap_and_latest_date(tmp_path):
    hub = FakeHub(days=40, per_day=5, lag=3, cap=20)
    spec = load_registry().get("floating_population_emd")
    c = _client(hub, tmp_path)
    params = {"startDate": "20261001", "endDate": "20261031"}
    out = deep_check(c, spec, params, today=TODAY, big_size=1000)
    assert out["paging"].startswith("정상")
    assert out["page_size"] == {"requested": 1000, "returned": 20, "total": 155}
    assert out["meta"]["totCnt"] == 155 and out["meta"]["hasMore"] is True
    assert out["latest_date"] == (TODAY - timedelta(days=3)).isoformat() and out["lag_days"] == 3 and out["rows_per_day"] == 5
    results = check_apis(c, [spec.__class__(**{**spec.__dict__, "api_params": params})], rows=2)
    results[0].deep = out
    text = format_report(results)
    assert "페이지 넘김: 정상" in text and "1000건 요청 → 20건 받음" in text and "오늘보다 3일 전" in text


def test_deep_check_flags_ignored_page_param(tmp_path):
    hub = FakeHub(days=10, per_day=7, cap=20, ignore_page=True)
    spec = load_registry().get("floating_population_emd")
    out = deep_check(_client(hub, tmp_path), spec, {"startDate": "20261001", "endDate": "20261231"}, today=TODAY)
    assert out["paging"].startswith("실패")
