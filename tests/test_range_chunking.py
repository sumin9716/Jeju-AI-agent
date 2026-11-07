"""기간형 API 를 하루씩 나눠 받기: 허브에 없는 날 확인, 날짜별 캐시 재사용, 전체 한도."""

from datetime import timedelta

from test_hub_paging import TODAY, FakeHub, _store


class GapHub(FakeHub):
    """FakeHub 에서 몇몇 날짜의 자료를 뺀다 (허브 자체에 없는 날)."""

    def __init__(self, missing: set[str], **kw):
        super().__init__(**kw)
        self.rows = [r for r in self.rows if r["baseDate"] not in missing]


def _days(hub):
    return [(c["startDate"], c["endDate"]) for c in hub.calls]


def test_range_is_fetched_day_by_day_and_missing_days_are_named(tmp_path):
    last = TODAY - timedelta(days=3)
    missing = {(last - timedelta(days=k)).strftime("%Y%m%d") for k in (2, 5)}
    hub = GapHub(missing, days=20, per_day=5, lag=3)
    start, end = (last - timedelta(days=9)).strftime("%Y%m%d"), last.strftime("%Y%m%d")
    ds = _store(hub, tmp_path).load("floating_population_emd", params={"startDate": start, "endDate": end})
    calls = _days(hub)
    assert all(s == e for s, e in calls) and len({s for s, _ in calls}) == 10  # 하루씩 10일
    assert len(ds.df) == 8 * 5
    notes = " ".join(ds.notes)
    assert "하루씩 10일을 나눠 불러왔습니다" in notes and "허브 전체 40건" in notes
    gaps = sorted(f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in missing)
    assert f"허브에 자료가 없는 날 2일: {gaps[0]}, {gaps[1]}" in notes


def test_overlapping_ranges_reuse_cached_days(tmp_path):
    hub = FakeHub(days=30, per_day=4, lag=3)
    store = _store(hub, tmp_path)
    last = TODAY - timedelta(days=3)
    a = {"startDate": (last - timedelta(days=9)).strftime("%Y%m%d"), "endDate": (last - timedelta(days=1)).strftime("%Y%m%d")}
    b = {"startDate": (last - timedelta(days=8)).strftime("%Y%m%d"), "endDate": last.strftime("%Y%m%d")}
    store.load("floating_population_emd", params=a)
    before = len(hub.calls)
    ds = store.load("floating_population_emd", params=b)
    new_days = {c["startDate"] for c in hub.calls[before:]}
    assert new_days == {last.strftime("%Y%m%d")}  # 겹치는 8일은 캐시, 새 하루만 호출
    assert len(ds.df) == 9 * 4 and any("캐시 8일" in n for n in ds.notes)


def test_range_stops_at_row_cap(tmp_path):
    hub = FakeHub(days=30, per_day=5, lag=3)
    last = TODAY - timedelta(days=3)
    params = {"startDate": (last - timedelta(days=9)).strftime("%Y%m%d"), "endDate": last.strftime("%Y%m%d")}
    ds = _store(hub, tmp_path).load("floating_population_emd", params=params, max_rows=12)
    assert len(ds.df) == 12
    assert any("한 번에 받는 한도에 닿아" in n for n in ds.notes)


def test_single_day_and_monthly_ranges_are_not_split(tmp_path):
    hub = FakeHub(days=5, per_day=3, lag=3)
    day = (TODAY - timedelta(days=3)).strftime("%Y%m%d")
    ds = _store(hub, tmp_path).load("floating_population_emd", params={"startDate": day, "endDate": day})
    assert len(ds.df) == 3 and not any("하루씩" in n for n in ds.notes)
