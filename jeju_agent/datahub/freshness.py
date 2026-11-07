"""기간 변수가 있는 Open API 의 '가장 최근 데이터 날짜'를 찾는다.

허브 데이터는 '자동갱신'이어도 실제로는 몇 주~몇 달 늦게 올라올 수 있다.
오늘부터 31일 단위로 거슬러 올라가며 데이터가 있는 구간을 찾고, 그 안에서 이분 탐색으로
마지막 날짜를 정한다. 요청은 쪽당 1건으로 보내므로 가볍다 (보통 10회 안팎).
"""

from __future__ import annotations

from datetime import date, timedelta

from .client import DataHubClient
from .registry import DatasetSpec


def ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def _has_data(client: DataHubClient, spec: DatasetSpec, base: dict, start: date, end: date) -> bool:
    s_key, e_key = spec.date_params[:2]
    r = client.fetch_page(spec.api_id, {**base, s_key: ymd(start), e_key: ymd(end)}, page=1, size=1)
    return bool(r.rows) or (r.total or 0) > 0


def find_latest_date(
    client: DataHubClient,
    spec: DatasetSpec,
    base_params: dict | None = None,
    today: date | None = None,
    window_days: int = 31,
    max_windows: int = 36,
) -> tuple[date | None, int]:
    """(가장 최근 데이터 날짜, 요청 횟수). 최근 max_windows×window_days 일 안에 없으면 날짜는 None."""
    if len(spec.date_params) < 2 or not spec.api_id:
        return None, 0
    today = today or date.today()
    base = {k: v for k, v in (base_params or {}).items() if k not in spec.date_params[:2]}
    requests = 0
    end = today
    for _ in range(max_windows):
        start = end - timedelta(days=window_days - 1)
        requests += 1
        if _has_data(client, spec, base, start, end):
            lo, hi = start, end  # 답은 [lo, hi] 안에 있고 [lo, end] 에는 데이터가 있다
            while lo < hi:
                mid = lo + timedelta(days=(hi - lo).days // 2 + (hi - lo).days % 2)
                requests += 1
                if _has_data(client, spec, base, mid, end):
                    lo = mid
                else:
                    hi = mid - timedelta(days=1)
            return lo, requests
        end = start - timedelta(days=1)
    return None, requests
