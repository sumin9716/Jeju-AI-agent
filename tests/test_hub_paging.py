"""제주데이터허브 Open API 동작을 흉내 낸 가짜 허브로 페이지 수집·오류·최신 날짜·정밀 점검을 검증한다.

가짜 허브는 2026-11-07 실제 호출에서 확인한 형식을 따른다:
- startDate/endDate 가 없으면 HTTP 400 과 이스케이프된 JSON 메시지
- number(쪽)·limit(쪽당 건수) 페이지, totCnt 로 전체 건수
- limit 이 100 을 넘으면 줄여 주지 않고 HTTP 500 (Spring 검증 오류 "must be less than or equal to 100")
- 데이터는 오늘보다 며칠~몇 달 늦게 올라온다
"""

from datetime import date, timedelta

import pytest

from jeju_agent.datahub import DataHubClient, DataHubError, DataStore, load_registry
from jeju_agent.datahub.apicheck import check_apis, deep_check, format_report
from jeju_agent.datahub.client import hub_error_message
from jeju_agent.datahub.freshness import find_latest_date

TODAY = date(2026, 11, 7)
BAD_PARAMS = '400 - "{\\"code\\":400,\\"status\\":\\"BAD_REQUEST\\",\\"message\\":\\"잘못된 파라미터 요청(\'startDate\' or \'endDate\')\\"}"'
LIMIT_ERROR = (
    "500 - \"org.springframework.validation.BeanPropertyBindingResult: 1 errors\\\\nField error in object "
    "'populationDataSearch' on field 'limit': rejected value [{size}]; codes [Max.populationDataSearch.limit,Max.limit,"
    "Max.java.lang.Integer,Max]; arguments [org.springframework.context.support.DefaultMessageSourceResolvable: codes "
    "[populationDataSearch.limit,limit]; arguments []; default message [limit],{cap}]; default message "
    "[must be less than or equal to {cap}]\""
)


class FakeResp:
    def __init__(self, status, payload=None, text="x"):
        self.status_code, self._payload, self.text = status, payload, text

    def json(self):
        return self._payload


class FakeHub:
    """하루 per_day 건, 최신 데이터는 TODAY-lag 일, 쪽당 상한 cap (넘으면 cap_mode 에 따라 오류 또는 잘라 줌)."""

    def __init__(self, days=40, per_day=5, lag=3, cap=20, cap_mode="error", ignore_page=False):
        self.rows = []
        last = TODAY - timedelta(days=lag)
        for d in range(days):
            day = (last - timedelta(days=d)).strftime("%Y%m%d")
            for i in range(per_day):
                self.rows.append({"baseDate": day, "city": "제주시", "emd": f"동{i}", "visitPop": str(100 + d * 10 + i)})
        self.rows.sort(key=lambda r: (r["baseDate"], r["emd"]))
        self.cap, self.cap_mode, self.ignore_page, self.calls = cap, cap_mode, ignore_page, []

    def get(self, url, params=None, timeout=None):
        p = dict(params or {})
        self.calls.append(p)
        s, e = p.get("startDate"), p.get("endDate")
        if not s or not e:
            return FakeResp(400, text=BAD_PARAMS)
        limit = int(p["limit"])
        if limit > self.cap and self.cap_mode == "error":
            return FakeResp(500, text=LIMIT_ERROR.format(size=limit, cap=self.cap))
        s, e = s.replace("-", ""), e.replace("-", "")
        sel = [r for r in self.rows if s <= r["baseDate"] <= e]
        for key in ("city", "emd"):  # 필터 변수로 동작하는 컬럼
            if key in p:
                sel = [r for r in sel if r[key] == p[key]]
        size = min(limit, self.cap)
        page = 1 if self.ignore_page else int(p["number"])
        chunk = sel[(page - 1) * size : page * size]
        return FakeResp(200, {"code": 200, "totCnt": len(sel), "hasMore": page * size < len(sel), "data": chunk})


def _client(hub, tmp_path, page_size=50):
    return DataHubClient(project_key="KEY", session=hub, cache_dir=tmp_path, page_size=page_size, sleep_between_pages=0)


def _store(hub, tmp_path, page_size=50):
    return DataStore(client=_client(hub, tmp_path, page_size), raw_dir=tmp_path, today=lambda: TODAY)


WIDE = {"startDate": "20260101", "endDate": "20261231"}


def test_default_page_size_is_hub_maximum():
    assert DataHubClient(project_key="K").page_size == 100


def test_page_size_error_is_retried_with_server_maximum(tmp_path):
    hub = FakeHub(days=10, per_day=7, cap=20, cap_mode="error")
    res = _client(hub, tmp_path).fetch("API", WIDE, use_cache=False)
    assert res.total == 70 and len(res.df) == 70 and not res.truncated
    assert res.df["visitPop"].nunique() == 70  # 중복 없이 모두 수집
    assert [c["limit"] for c in hub.calls] == [50, 20, 20, 20, 20]  # 500 오류 → 서버 최대값 20 으로 재요청


def test_server_that_truncates_quietly_is_also_handled(tmp_path):
    hub = FakeHub(days=10, per_day=7, cap=20, cap_mode="truncate")
    res = _client(hub, tmp_path).fetch("API", WIDE, use_cache=False)
    assert len(res.df) == 70 and [c["limit"] for c in hub.calls] == [50, 20, 20, 20]


def test_repeated_page_is_an_error_not_duplicates(tmp_path):
    hub = FakeHub(days=10, per_day=7, cap=20, ignore_page=True)
    with pytest.raises(DataHubError, match="반복"):
        _client(hub, tmp_path, page_size=20).fetch("API", WIDE, use_cache=False)


def test_truncation_is_flagged(tmp_path):
    hub = FakeHub(days=10, per_day=7, cap=20)
    c = _client(hub, tmp_path, page_size=20)
    c.max_pages = 2
    res = c.fetch("API", WIDE, use_cache=False)
    assert res.truncated and len(res.df) == 40 and res.total == 70


def test_hub_error_messages_are_readable(tmp_path):
    with pytest.raises(DataHubError) as ei:
        _client(FakeHub(), tmp_path).fetch_page("API", {})
    msg = str(ei.value)
    assert "HTTP 400" in msg and "잘못된 파라미터 요청('startDate' or 'endDate')" in msg and "\\\\" not in msg
    assert hub_error_message(LIMIT_ERROR.format(size=1000, cap=100)) == "'limit' 값 1000 거부: must be less than or equal to 100"


def test_find_latest_date_with_lag(tmp_path):
    for lag in (3, 45, 200):
        hub = FakeHub(days=30, per_day=2, lag=lag)
        spec = load_registry().get("floating_population_emd")
        latest, n = find_latest_date(_client(hub, tmp_path), spec, {}, today=TODAY)
        assert latest == TODAY - timedelta(days=lag), lag
        assert n <= 15
    latest, n = find_latest_date(_client(FakeHub(days=1, lag=5000), tmp_path), load_registry().get("floating_population_emd"), {}, today=TODAY)
    assert latest is None and n == 36


def test_loader_reports_api_failure_reason_when_falling_back(tmp_path):
    class AlwaysBad:
        def get(self, url, params=None, timeout=None):
            return FakeResp(400, text=BAD_PARAMS)

    store = DataStore(client=DataHubClient(project_key="KEY", session=AlwaysBad(), cache_dir=tmp_path), raw_dir=tmp_path)
    ds = store.load("floating_population_emd")
    assert ds.provenance == "sample"
    assert "Open API" in ds.notes[0] and "잘못된 파라미터" in ds.notes[0] and "source='api'" in ds.notes[0]


def test_loader_renames_api_rows(tmp_path):
    hub = FakeHub(days=5, per_day=3, lag=3)
    window = {"startDate": (TODAY - timedelta(days=7)).strftime("%Y%m%d"), "endDate": TODAY.strftime("%Y%m%d")}
    ds = _store(hub, tmp_path).load("floating_population_emd", params=window)
    assert ds.provenance == "api" and ds.rows == 15 and {"날짜", "시군구", "읍면동", "방문자수"} <= set(ds.df.columns)
    assert str(ds.df["날짜"].dtype).startswith("datetime64") and ds.df["방문자수"].dtype.kind == "i"


def test_loader_default_window_moves_to_latest_two_weeks(tmp_path):
    hub = FakeHub(days=60, per_day=2, lag=40)  # 기본 기간(오늘-21~오늘-8)에는 데이터가 없다
    ds = _store(hub, tmp_path).load("floating_population_emd")
    latest = TODAY - timedelta(days=40)
    assert ds.provenance == "api" and ds.rows == 14 * 2
    assert ds.df["날짜"].max().date() == latest and ds.df["날짜"].min().date() == latest - timedelta(days=13)
    assert any("가장 최근 2주" in n for n in ds.notes) and any(latest.isoformat() in n for n in ds.notes)


def test_loader_explicit_window_with_no_data_reports_latest_date(tmp_path):
    hub = FakeHub(days=60, per_day=2, lag=40)
    ds = _store(hub, tmp_path).load("floating_population_emd", params={"startDate": "20261020", "endDate": "20261031"})
    assert ds.rows == 0 and any("0건" in n for n in ds.notes)
    assert any((TODAY - timedelta(days=40)).isoformat() in n for n in ds.notes)


def test_deep_check_full_report(tmp_path):
    hub = FakeHub(days=40, per_day=5, lag=50, cap=20, cap_mode="error")
    spec = load_registry().get("floating_population_emd")
    c = _client(hub, tmp_path)
    params = {"startDate": "20261017", "endDate": "20261030"}  # 실제 PC 점검처럼 데이터 없는 기본 기간
    out = deep_check(c, spec, params, today=TODAY, big_size=1000)
    latest = TODAY - timedelta(days=50)
    assert out["latest_date"] == latest.isoformat() and out["lag_days"] == 50
    assert out["window"] == {"startDate": (latest - timedelta(days=6)).strftime("%Y%m%d"), "endDate": latest.strftime("%Y%m%d")}
    assert out["window_total"] == 35 and out["paging"].startswith("정상")
    assert out["page_size"] == {"requested": 1000, "returned": 20, "max": 20}
    assert out["filters"]["emd"]["works"] and out["filters"]["emd"]["total"] == 7
    assert not out["filters"]["city"]["works"]  # 모두 제주시라 줄지 않음
    assert out["errors"] == []
    results = check_apis(c, [spec], extra_params=params, rows=2)
    results[0].deep = out
    text = format_report(results)
    for part in ("가장 최근 데이터 날짜", "오늘보다 50일 전", "페이지 넘김: 정상", "쪽당 최대 건수: 20건", "필터 emd=동0: ✔ 동작"):
        assert part in text, part


def test_deep_check_flags_ignored_page_param(tmp_path):
    hub = FakeHub(days=10, per_day=7, cap=20, ignore_page=True)
    spec = load_registry().get("floating_population_emd")
    out = deep_check(_client(hub, tmp_path), spec, dict(WIDE), today=TODAY)
    assert out["paging"].startswith("실패")


def test_loader_filter_params_and_coverage_notes(tmp_path):
    hub = FakeHub(days=60, per_day=4, lag=40)
    latest = TODAY - timedelta(days=40)
    params = {"startDate": (latest - timedelta(days=9)).strftime("%Y%m%d"), "endDate": TODAY.strftime("%Y%m%d"), "emd": "동1"}
    ds = _store(hub, tmp_path).load("floating_population_emd", params=params)
    assert ds.rows == 10 and set(ds.df["읍면동"]) == {"동1"}  # 거르기 변수 적용
    assert any(f"실제 데이터 기간: {(latest - timedelta(days=9)).isoformat()} ~ {latest.isoformat()}" in n for n in ds.notes)
    assert any("일찍 끝납니다" in n for n in ds.notes)


def test_registry_filter_params_reach_system_prompt():
    from jeju_agent.agent import Agent, ScriptedBackend
    from jeju_agent.config import Settings

    spec = load_registry().get("floating_population_emd")
    assert set(spec.filter_params) == {"emd", "city", "gender", "ageGroup"}
    text = Agent(settings=Settings(), backend=ScriptedBackend([])).system_blocks()[0]["text"]
    assert "거르기 변수" in text and "emd=" in text and "startDate" in text
