"""기간 변수가 있는 Open API 의 '가장 최근 데이터 날짜'를 찾는다.

허브 데이터는 '자동갱신'이어도 실제로는 몇 주~몇 달 늦게 올라올 수 있다.
오늘부터 31일 단위로 거슬러 올라가며 데이터가 있는 구간을 찾고, 그 안에서 이분 탐색으로
마지막 날짜를 정한다. 요청은 쪽당 1건으로 보내므로 가볍다 (보통 10회 안팎).

하루만 받는 API(예: 일별 기상 정보의 searchDate)는 최근 7일을 하루씩, 그 뒤로는 7일 간격으로
확인한 다음 이분 탐색한다.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from .client import DataHubClient
from .registry import DatasetSpec


def ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def fmt_date(d: date, spec: DatasetSpec) -> str:
    """레지스트리의 date_format(기본 YYYYMMDD, 월 단위는 YYYYMM)으로 날짜를 쓴다."""
    return d.strftime(spec.date_format or "%Y%m%d")


def parse_date_text(value) -> date | None:
    """YYYYMMDD·YYYY-MM-DD·YYYYMM·YYYY-MM 을 날짜로 읽는다 (월만 있으면 그달 1일)."""
    text = re.sub(r"[^0-9]", "", str(value or ""))
    try:
        if len(text) == 8:
            return datetime.strptime(text, "%Y%m%d").date()
        if len(text) == 6:
            return datetime.strptime(text, "%Y%m").date()
    except ValueError:
        return None
    return None


def to_api_params(spec: DatasetSpec, params: dict) -> dict:
    """하루만 받는 API 는 기간 변수(startDate·endDate)를 끝 날짜 하루짜리 날짜 변수로 바꾼다."""
    if not spec.day_param:
        return dict(params)
    p = dict(params)
    values = [p.pop(k, None) for k in spec.date_params[:2]]
    if spec.day_param not in p:
        d = parse_date_text(values[-1] if values and values[-1] else (values[0] if values else None))
        if d:
            p[spec.day_param] = fmt_date(d, spec)
    return p


def _has_data(client: DataHubClient, spec: DatasetSpec, base: dict, start: date, end: date) -> bool:
    s_key, e_key = spec.date_params[:2]
    r = client.fetch_page(spec.api_id, {**base, s_key: fmt_date(start, spec), e_key: fmt_date(end, spec)}, page=1, size=1)
    return bool(r.rows) or (r.total or 0) > 0


def _has_day(client: DataHubClient, spec: DatasetSpec, base: dict, d: date) -> bool:
    r = client.fetch_page(spec.api_id, {**base, spec.day_param: fmt_date(d, spec)}, page=1, size=1)
    return bool(r.rows) or (r.total or 0) > 0


def find_latest_day(
    client: DataHubClient,
    spec: DatasetSpec,
    base_params: dict | None = None,
    today: date | None = None,
    max_back_days: int = 400,
) -> tuple[date | None, int]:
    """하루만 받는 API 의 (가장 최근 데이터 날짜, 요청 횟수). 데이터는 그 날짜까지 빠짐없이 있다고 본다."""
    if not spec.day_param or not spec.api_id:
        return None, 0
    today = today or date.today()
    skip = {spec.day_param, *spec.date_params[:2]}
    base = {k: v for k, v in (base_params or {}).items() if k not in skip}
    requests = 0
    newest_empty = today  # 이 날짜와 그 뒤로는 데이터가 없다고 확인됨(오늘 자료는 보통 아직 없음)
    for k in range(1, 8):  # 최근 7일은 하루씩
        d = today - timedelta(days=k)
        requests += 1
        if _has_day(client, spec, base, d):
            return d, requests
        newest_empty = d
    k = 14
    while k <= max_back_days:  # 그 뒤로는 7일 간격
        d = today - timedelta(days=k)
        requests += 1
        if _has_day(client, spec, base, d):
            lo, hi = d, newest_empty - timedelta(days=1)
            while lo < hi:
                mid = lo + timedelta(days=(hi - lo).days // 2 + (hi - lo).days % 2)
                requests += 1
                if _has_day(client, spec, base, mid):
                    lo = mid
                else:
                    hi = mid - timedelta(days=1)
            return lo, requests
        newest_empty = d
        k += 7
    return None, requests


def find_latest_date(
    client: DataHubClient,
    spec: DatasetSpec,
    base_params: dict | None = None,
    today: date | None = None,
    window_days: int = 31,
    max_windows: int = 36,
) -> tuple[date | None, int]:
    """(가장 최근 데이터 날짜, 요청 횟수). 최근 max_windows×window_days 일 안에 없으면 날짜는 None."""
    if spec.day_param:
        return find_latest_day(client, spec, base_params, today=today)
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
