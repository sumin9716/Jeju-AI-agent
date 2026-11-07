"""하루만 받는 API(일별 기상 정보의 searchDate)와 월 단위 기간 변수(YYYYMM) 처리를 가짜 허브로 검증한다.

공개 저장소 코드에서 확인한 형식을 따른다:
- 일별 기상 정보: searchDate=YYYYMMDD 하루씩, 하루에 관측소마다 1행 (baseDate·observatoryName·dailyRainfall …)
- 방문목적별 입도객 현황: startDate·endDate=YYYYMM, 응답 dtYearMonth·visitPurpose·visitorCnt
"""

from datetime import date, timedelta

import pandas as pd

from jeju_agent.datahub import DataHubClient, DataStore, load_registry
from jeju_agent.datahub.apicheck import check_apis, deep_check
from jeju_agent.datahub.freshness import find_latest_date, find_latest_day, parse_date_text, to_api_params
from jeju_agent.config import SAMPLES_DIR

TODAY = date(2026, 11, 7)
STATIONS = ["제주", "고산", "성산", "서귀포"]


class Resp:
    def __init__(self, status, payload):
        self.status_code, self._payload, self.text = status, payload, "x"

    def json(self):
        return self._payload


class DayHub:
    """searchDate 하루만 받는 가짜 일별 기상 API. latest 이후 날짜는 0건."""

    def __init__(self, latest: date, first: date = date(2014, 1, 1)):
        self.latest, self.first, self.calls = latest, first, []

    def get(self, url, params=None, timeout=None):
        p = dict(params or {})
        self.calls.append(p)
        day = parse_date_text(p.get("searchDate"))
        rows = []
        if day and self.first <= day <= self.latest:
            for i, stn in enumerate(STATIONS):
                if "observatoryName" in p and p["observatoryName"] != stn:
                    continue
                rows.append({"baseDate": day.strftime("%Y%m%d"), "observatoryName": stn, "averageTemperature": "15.2",
                             "dailyRainfall": str(float((day.toordinal() + i) % 7))})
        size, page = int(p.get("limit", 100)), int(p.get("number", 1))
        chunk = rows[(page - 1) * size: page * size]
        return Resp(200, {"totCnt": len(rows), "hasMore": page * size < len(rows), "data": chunk})


class MonthHub:
    """startDate·endDate=YYYYMM 을 받는 가짜 입도객 API. 2018-01 ~ 2025-07."""

    def __init__(self):
        self.calls = []
        months = pd.period_range("2018-01", "2025-07", freq="M")
        self.rows = [{"dtYearMonth": m.strftime("%Y%m"), "visitPurpose": "휴양및관람", "visitorCnt": "700000"} for m in months]

    def get(self, url, params=None, timeout=None):
        p = dict(params or {})
        self.calls.append(p)
        s, e = str(p.get("startDate", "")), str(p.get("endDate", ""))
        assert len(s) == 6 and len(e) == 6, "월 단위 API 에 YYYYMM 이 아닌 값을 보냄"
        sel = [r for r in self.rows if s <= r["dtYearMonth"] <= e]
        size, page = int(p.get("limit", 100)), int(p.get("number", 1))
        return Resp(200, {"totCnt": len(sel), "hasMore": page * size < len(sel), "data": sel[(page - 1) * size: page * size]})


def _store(hub, tmp_path, rolling_weather: bool = True):
    """rolling_weather: 일별 기상의 기본 기간을 '오늘-14 ~ 오늘-1' 로 바꿔 갱신이 계속되는 경우를 흉내 낸다
    (실제 레지스트리는 갱신이 멈춘 2025-03 마지막 2주를 기본으로 쓴다)."""
    from dataclasses import replace

    from jeju_agent.datahub.registry import Registry

    specs = []
    for sp in load_registry():
        if rolling_weather and sp.id == "daily_weather":
            sp = replace(sp, api_params={"startDate": "{{today-14d}}", "endDate": "{{today-1d}}"})
        specs.append(sp)
    client = DataHubClient(project_key="KEY", session=hub, cache_dir=tmp_path, page_size=100, sleep_between_pages=0)
    return DataStore(registry=Registry(specs), client=client, raw_dir=tmp_path, today=lambda: TODAY)


def test_registry_has_day_and_month_specs():
    reg = load_registry()
    weather = reg.get("daily_weather")
    assert weather.day_param == "searchDate" and weather.api_id and weather.date_params == ["startDate", "endDate"]
    assert weather.api_params == {"startDate": "20250317", "endDate": "20250330"}  # 실제 자료는 2025-03-30 까지
    wifi = reg.get("public_wifi_ap")
    assert wifi.day_param == "baseDate" and wifi.snapshot and wifi.schema_verified
    for key in ("visitors_by_purpose", "card_domestic_tourist"):
        assert reg.get(key).date_format == "%Y%m"
    assert reg.get("weather_stations").catalog_no == 757 and reg.get("bus_route_daily_users").catalog_no == 724


def test_to_api_params_turns_range_into_one_day():
    spec = load_registry().get("daily_weather")
    assert to_api_params(spec, {"startDate": "20260101", "endDate": "2026-01-05", "x": "1"}) == {"searchDate": "20260105", "x": "1"}
    other = load_registry().get("floating_population_emd")
    assert to_api_params(other, {"startDate": "20260101", "endDate": "20260105"}) == {"startDate": "20260101", "endDate": "20260105"}


def test_day_api_default_window_fetches_each_day(tmp_path):
    hub = DayHub(latest=TODAY - timedelta(days=2))
    ds = _store(hub, tmp_path).load("daily_weather")
    assert ds.provenance == "api"
    # 기본 기간(오늘-14 ~ 오늘-1) 14일을 하루씩 부르고, 자료가 있는 13일 × 관측소 4곳을 받는다
    days = [c["searchDate"] for c in hub.calls]
    assert len(days) == 14 and days[0] == "20261024" and days[-1] == "20261106"
    assert len(ds.df) == 13 * 4 and {"날짜", "관측소명", "평균기온", "강수량"} <= set(ds.df.columns)
    assert pd.api.types.is_datetime64_any_dtype(ds.df["날짜"])
    assert any("하루씩 14일" in n for n in ds.notes)
    assert any("실제 데이터 기간: 2026-10-24 ~ 2026-11-05" in n for n in ds.notes)
    assert "{PROJECT_KEY}" in ds.source and "KEY/" not in ds.source


def test_day_api_finds_latest_when_default_window_is_empty(tmp_path):
    latest = TODAY - timedelta(days=40)
    hub = DayHub(latest=latest)
    ds = _store(hub, tmp_path).load("daily_weather")
    assert len(ds.df) == 14 * 4 and ds.df["날짜"].max().date() == latest
    assert any("가장 최근 날짜는 2026-09-28" in n for n in ds.notes)
    assert any("가장 최근 2주(2026-09-15 ~ 2026-09-28)" in n for n in ds.notes)


def test_weather_default_window_is_last_two_weeks_before_feed_stopped(tmp_path):
    hub = DayHub(latest=date(2025, 3, 30))
    ds = _store(hub, tmp_path, rolling_weather=False).load("daily_weather")
    assert len(hub.calls) == 14 and hub.calls[0]["searchDate"] == "20250317" and hub.calls[-1]["searchDate"] == "20250330"
    assert len(ds.df) == 14 * 4 and ds.df["날짜"].max().date() == date(2025, 3, 30)


def test_day_api_keeps_user_dates_and_caps_long_ranges(tmp_path):
    hub = DayHub(latest=TODAY - timedelta(days=40))
    store = _store(hub, tmp_path)
    # 사용자가 준 기간이 자료 이후면 다시 부르지 않고 최근 날짜만 알려 준다
    ds = store.load("daily_weather", params={"startDate": "20261101", "endDate": "20261105"})
    assert ds.df.empty and any("가장 최근 날짜는 2026-09-28" in n for n in ds.notes)
    assert not any("다시 불러왔습니다" in n for n in ds.notes)
    # 하루짜리 변수를 직접 줘도 된다 (거르기 변수와 함께)
    one = store.load("daily_weather", params={"searchDate": "20260901", "observatoryName": "고산"})
    assert len(one.df) == 1 and one.df["관측소명"].iloc[0] == "고산"
    # 한도(400일)를 넘는 기간은 끝 날짜 기준으로 줄인다
    hub.calls.clear()
    big = store.load("daily_weather", params={"startDate": "20240101", "endDate": "20250930"}, refresh=True)
    assert len(hub.calls) == 400 and any("최근 400일" in n for n in big.notes)


def test_find_latest_day_probe_counts():
    spec = load_registry().get("daily_weather")
    for lag, max_calls in ((1, 1), (5, 5), (10, 12), (100, 30), (390, 70)):
        hub = DayHub(latest=TODAY - timedelta(days=lag))
        client = DataHubClient(project_key="KEY", session=hub, sleep_between_pages=0)
        latest, n = find_latest_day(client, spec, {}, today=TODAY)
        assert latest == TODAY - timedelta(days=lag), lag
        assert n <= max_calls, (lag, n)
    # 갱신이 오래전에 멈춘 데이터도 마지막 날짜를 찾는다 (약 12년 전까지)
    for lag in (2000, 3000):
        hub = DayHub(latest=TODAY - timedelta(days=lag), first=date(2000, 1, 1))
        latest, n = find_latest_date(DataHubClient(project_key="KEY", session=hub, sleep_between_pages=0), spec, {}, today=TODAY)
        assert latest == TODAY - timedelta(days=lag) and n <= 60, (lag, n)
    hub = DayHub(latest=TODAY - timedelta(days=5000), first=date(2000, 1, 1))
    latest, n = find_latest_date(DataHubClient(project_key="KEY", session=hub, sleep_between_pages=0), spec, {}, today=TODAY)
    assert latest is None and n <= 60


def test_month_api_uses_yyyymm_everywhere(tmp_path):
    hub = MonthHub()
    store = _store(hub, tmp_path)
    ds = store.load("visitors_by_purpose")  # 기본 기간 202108 ~ 202507 (실제 응답 기준)
    assert len(ds.df) == 48 and pd.api.types.is_datetime64_any_dtype(ds.df["연월"])
    assert ds.df["연월"].min() == pd.Timestamp("2021-08-01") and ds.df["연월"].max() == pd.Timestamp("2025-07-01")
    # 최근 기간을 물어 0건이면 월 형식으로 최신 날짜를 찾는다 (YYYYMMDD 를 보내면 MonthHub 가 실패시킴)
    spec = store.registry.get("visitors_by_purpose")
    latest, _ = find_latest_date(store.client, spec, {}, today=date(2025, 9, 15))
    assert latest is not None and latest.strftime("%Y%m") == "202507"


class OldMonthHub(MonthHub):
    """2016-09 ~ 2018-12 에서 멈춘 월 단위 데이터 (카드 데이터처럼)."""

    def __init__(self):
        super().__init__()
        months = pd.period_range("2016-09", "2018-12", freq="M")
        self.rows = [{"dtYearMonth": m.strftime("%Y%m"), "useCost": "1000"} for m in months]


def test_month_api_finds_latest_month_of_a_feed_that_stopped_years_ago():
    spec = load_registry().get("card_domestic_tourist")
    hub = OldMonthHub()
    latest, n = find_latest_date(DataHubClient(project_key="KEY", session=hub, sleep_between_pages=0), spec, {}, today=TODAY)
    assert latest is not None and latest.strftime("%Y%m") == "201812" and n <= 40


def test_snapshot_api_refetches_only_the_latest_day(tmp_path):
    from dataclasses import replace

    from jeju_agent.datahub.registry import Registry

    hub = DayHub(latest=TODAY - timedelta(days=5))  # 기본 기간(어제)에 자료 없음
    reg = load_registry()
    wifi = replace(reg.get("daily_weather"), id="snap", snapshot=True, api_params={"startDate": "{{today-1d}}", "endDate": "{{today-1d}}"})
    client = DataHubClient(project_key="KEY", session=hub, cache_dir=tmp_path, page_size=100, sleep_between_pages=0)
    store = DataStore(registry=Registry([wifi]), client=client, raw_dir=tmp_path, today=lambda: TODAY)
    ds = store.load("snap")
    assert ds.df["날짜"].dt.date.unique().tolist() == [TODAY - timedelta(days=5)]
    assert any("가장 최근 하루(2026-11-02)" in n for n in ds.notes)


def test_check_and_deep_check_for_day_api(tmp_path):
    spec = load_registry().get("daily_weather")
    hub = DayHub(latest=TODAY - timedelta(days=3))
    client = DataHubClient(project_key="KEY", session=hub, cache_dir=tmp_path, page_size=2, max_pages=1, sleep_between_pages=0)
    (res,) = check_apis(client, [spec], extra_params={"startDate": "20261101", "endDate": "20261106"}, rows=2)
    assert res.params == {"searchDate": "20261106"} and res.status == "empty" and "--deep" in res.hint
    assert res.columns == []
    deep = deep_check(client, spec, res.params, today=TODAY)
    assert deep["latest_date"] == "2026-11-04" and deep["window"] == {"searchDate": "20261104"}
    assert deep["window_total"] == 4 and deep["filters"]["observatoryName"]["works"] is True
    # 정밀 점검 반복은 기본 점검이 0건이던 API 의 응답 컬럼·예시를 최근 구간 응답으로 채운다
    from jeju_agent.datahub.apicheck import format_report, run_deep_checks

    assert run_deep_checks(client, [res], [spec], pause=0) is False
    assert res.columns[:2] == ["baseDate", "observatoryName"] and res.unknown_columns == [] and res.sample
    assert "- 응답 컬럼 (가장 최근 데이터 구간 기준): baseDate, observatoryName" in format_report([res])


def test_samples_follow_registry_columns():
    for spec in load_registry():
        if not spec.sample_file:
            continue
        cols = list(pd.read_csv(SAMPLES_DIR / spec.sample_file, nrows=1, encoding="utf-8-sig").columns)
        assert cols, spec.id
        assert set(cols) <= {c.name for c in spec.columns}, (spec.id, set(cols) - {c.name for c in spec.columns})


def test_deep_check_tries_other_date_params_when_day_api_is_empty(tmp_path):
    from jeju_agent.datahub.apicheck import ApiCheckResult, format_report

    spec = load_registry().get("daily_weather")
    hub = DayHub(latest=TODAY - timedelta(days=6000), first=date(2000, 1, 1))  # 12년 안에 자료 없음
    client = DataHubClient(project_key="KEY", session=hub, cache_dir=tmp_path, page_size=2, max_pages=1, sleep_between_pages=0)
    deep = deep_check(client, spec, {"searchDate": "20261106"}, today=TODAY)
    assert deep["latest_date"] is None
    probe = deep["param_probe"]
    assert set(probe) >= {"searchDate=2026-11-05", "baseDate=20261105", "startDate·endDate=20261105", "날짜 변수 없이"}
    assert all(v == 0 for v in probe.values())
    res = ApiCheckResult(id=spec.id, catalog_no=756, name=spec.name, status="empty", total=0, deep=deep)
    text = format_report([res])
    assert "찾지 못함 (10여 년 전까지 확인" in text and "다른 날짜 변수 시험: searchDate=2026-11-05 → 0건" in text
