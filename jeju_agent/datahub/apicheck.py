"""제주데이터허브 Open API 일괄 점검.

등록 데이터셋 중 api_id 가 있는 것마다 첫 페이지(몇 건)만 호출해
성공 여부, 전체 건수, 응답 컬럼, 레지스트리에 없는 컬럼, 예시 행을 보고한다.
이 보고서를 보고 registry.yaml 의 api_params·rename 을 실제 응답에 맞춘다.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from .client import DataHubClient, DataHubError, render_params
from .registry import DatasetSpec


@dataclass
class ApiCheckResult:
    id: str
    catalog_no: int | None
    name: str
    status: str  # ok | empty | error | no_api_id
    params: dict = field(default_factory=dict)
    total: int | None = None
    columns: list[str] = field(default_factory=list)
    unknown_columns: list[str] = field(default_factory=list)
    sample: list[dict] = field(default_factory=list)
    error: str = ""
    deep: dict = field(default_factory=dict)


def check_apis(
    client: DataHubClient,
    specs: list[DatasetSpec],
    extra_params: dict | None = None,
    rows: int = 2,
) -> list[ApiCheckResult]:
    results: list[ApiCheckResult] = []
    for spec in specs:
        res = ApiCheckResult(id=spec.id, catalog_no=spec.catalog_no, name=spec.name, status="no_api_id")
        if not spec.api_id:
            results.append(res)
            continue
        params = render_params({**(spec.api_params or {}), **(extra_params or {})})
        res.params = params
        try:
            first = client.for_paging(spec.api_paging).fetch_page(spec.api_id, params, page=1, size=rows)
            page_rows, total = first.rows, first.total
        except DataHubError as e:
            res.status, res.error = "error", str(e)
            results.append(res)
            continue
        columns: list[str] = []
        for row in page_rows:
            for key in row:
                if key not in columns:
                    columns.append(key)
        known = {c.name for c in spec.columns} | set(spec.rename or {})
        res.status = "ok" if page_rows else "empty"
        res.total = total
        res.columns = columns
        res.unknown_columns = [c for c in columns if c not in known]
        res.sample = [{k: (None if v is None else str(v)[:80]) for k, v in row.items()} for row in page_rows[:rows]]
        results.append(res)
    return results


def _ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def deep_check(
    client: DataHubClient,
    spec: DatasetSpec,
    params: dict,
    today: date | None = None,
    big_size: int = 1000,
) -> dict:
    """페이지 넘김·서버 페이지 상한·응답 메타·최신 데이터 날짜를 확인한다 (요청 최대 약 17회)."""
    today = today or date.today()
    c = client.for_paging(spec.api_paging)
    out: dict = {}
    try:
        p1 = c.fetch_page(spec.api_id, params, page=1, size=2)
        p2 = c.fetch_page(spec.api_id, params, page=2, size=2)
        out["meta"] = p1.meta
        if not p1.rows:
            out["paging"] = "데이터 없음"
        elif not p2.rows:
            out["paging"] = "2쪽 비어 있음" if (p1.total or 0) > 2 else "전체 2건 이하"
        elif p2.rows == p1.rows:
            out["paging"] = "실패: 2쪽이 1쪽과 같음 (페이지 변수 무시)"
        else:
            out["paging"] = "정상: 2쪽이 1쪽과 다름"
        big = c.fetch_page(spec.api_id, params, page=1, size=big_size)
        out["page_size"] = {"requested": big_size, "returned": len(big.rows), "total": big.total}
    except DataHubError as e:
        out["error"] = str(e)
        return out

    if len(spec.date_params) >= 2:
        s_key, e_key = spec.date_params[:2]
        found_end: date | None = None
        for back in (0, 30, 60, 90, 180, 365, 730):
            end = today - timedelta(days=back)
            q = {**params, s_key: _ymd(end - timedelta(days=6)), e_key: _ymd(end)}
            try:
                r = c.fetch_page(spec.api_id, q, page=1, size=1)
            except DataHubError as e:
                out["latest_error"] = str(e)
                break
            if r.rows or (r.total or 0) > 0:
                found_end = end
                break
        if found_end is not None:
            latest = None
            for d in range(7):
                day = found_end - timedelta(days=d)
                try:
                    r = c.fetch_page(spec.api_id, {**params, s_key: _ymd(day), e_key: _ymd(day)}, page=1, size=1)
                except DataHubError:
                    break
                if r.rows or (r.total or 0) > 0:
                    latest = day
                    out["rows_per_day"] = r.total
                    break
            out["latest_date"] = latest.isoformat() if latest else None
            if latest:
                out["lag_days"] = (today - latest).days
        else:
            out["latest_date"] = None
    return out


def format_report(results: list[ApiCheckResult], saved_path: str | Path | None = None) -> str:
    lines = ["# 제주데이터허브 Open API 점검 결과", f"점검 시각: {datetime.now().isoformat(timespec='seconds')}", ""]
    for r in results:
        lines.append(f"## {r.id} (No.{r.catalog_no}) {r.name}")
        if r.status == "no_api_id":
            lines.append("- 상태: ○ api_id 미입력. 허브 데이터 상세 페이지의 API 탭에서 고유 ID 를 확인해 registry.yaml 에 넣으세요.")
        elif r.status == "error":
            lines.append(f"- 상태: ✖ 실패: {r.error}")
        else:
            total = f"{r.total:,}건" if r.total is not None else "건수 알 수 없음"
            lines.append(f"- 상태: ✔ 성공 (전체 {total}{', 이번 조건에 데이터 없음' if r.status == 'empty' else ''})")
            lines.append("- 응답 컬럼: " + (", ".join(r.columns) or "(없음)"))
            if r.unknown_columns:
                lines.append("- 레지스트리에 없는 컬럼: " + ", ".join(r.unknown_columns))
            for i, row in enumerate(r.sample, 1):
                lines.append(f"- 예시 {i}: " + json.dumps(row, ensure_ascii=False))
        if r.params:
            lines.append("- 질의 변수: " + json.dumps(r.params, ensure_ascii=False))
        if r.deep:
            d = r.deep
            if d.get("error"):
                lines.append(f"- 정밀 점검 실패: {d['error']}")
            else:
                lines.append(f"- 페이지 넘김: {d.get('paging')}")
                ps = d.get("page_size") or {}
                if ps:
                    lines.append(f"- 한 번에 받을 수 있는 건수: {ps.get('requested')}건 요청 → {ps.get('returned')}건 받음")
                if d.get("meta"):
                    lines.append("- 응답 메타 필드: " + json.dumps(d["meta"], ensure_ascii=False))
                if "latest_date" in d:
                    if d.get("latest_date"):
                        lines.append(
                            f"- 가장 최근 데이터 날짜: {d['latest_date']} (오늘보다 {d.get('lag_days')}일 전, 그날 {d.get('rows_per_day')}건)"
                        )
                    else:
                        lines.append("- 가장 최근 데이터 날짜: 최근 2년 안에서 찾지 못함")
                if d.get("latest_error"):
                    lines.append(f"- 최신 날짜 확인 중 오류: {d['latest_error']}")
        lines.append("")
    n_ok = sum(r.status in ("ok", "empty") for r in results)
    n_err = sum(r.status == "error" for r in results)
    n_none = sum(r.status == "no_api_id" for r in results)
    lines.append(f"요약: 성공 {n_ok} · 실패 {n_err} · api_id 미입력 {n_none}")
    lines.append("이 출력 전체를 복사해 Claude 에게 보내면 registry.yaml 의 api_params·rename 을 실제 응답에 맞춰 줍니다.")
    if saved_path:
        lines.append(f"결과 파일: {saved_path}")
    return "\n".join(lines)


def save_results(results: list[ApiCheckResult], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"api_check_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=1), encoding="utf-8")
    return path
