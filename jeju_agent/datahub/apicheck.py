"""제주데이터허브 Open API 일괄 점검.

등록 데이터셋 중 api_id 가 있는 것마다 첫 페이지(몇 건)만 호출해
성공 여부, 전체 건수, 응답 컬럼, 레지스트리에 없는 컬럼, 예시 행을 보고한다.
이 보고서를 보고 registry.yaml 의 api_params·rename 을 실제 응답에 맞춘다.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from .client import DataHubClient, DataHubError, PageSizeTooLarge, render_params
from .freshness import find_latest_date, fmt_date, to_api_params
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
    error_kind: str = ""
    hint: str = ""
    deep: dict = field(default_factory=dict)


def status_line(r: "ApiCheckResult") -> str:
    """점검 중 화면에 바로 보여 줄 한 줄 요약."""
    head = f"No.{r.catalog_no} {r.name}"
    if r.status == "no_api_id":
        return f"○ {head} — api_id 미입력"
    if r.status == "error":
        return f"✖ {head} — {r.error.split(':')[0]}"
    total = f"전체 {r.total:,}건" if r.total is not None else "건수 알 수 없음"
    return f"✔ {head} — 성공 ({total}{', 이번 기간 0건' if r.status == 'empty' else ''})"


def check_apis(
    client: DataHubClient,
    specs: list[DatasetSpec],
    extra_params: dict | None = None,
    rows: int = 2,
    log=None,
) -> list[ApiCheckResult]:
    say = log or (lambda _m: None)
    results: list[ApiCheckResult] = []
    for spec in specs:
        res = ApiCheckResult(id=spec.id, catalog_no=spec.catalog_no, name=spec.name, status="no_api_id")
        if not spec.api_id:
            results.append(res)
            say(status_line(res))
            continue
        params = to_api_params(spec, render_params({**(spec.api_params or {}), **(extra_params or {})}))
        res.params = params
        try:
            first = client.for_paging(spec.api_paging).fetch_page(spec.api_id, params, page=1, size=rows)
            page_rows, total = first.rows, first.total
        except DataHubError as e:
            res.status, res.error, res.error_kind = "error", str(e), getattr(e, "kind", "")
            results.append(res)
            say(status_line(res))
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
        if not page_rows and len(spec.date_params) >= 2:
            res.hint = (
                "점검 기간에 자료가 없습니다. 허브 공개가 늦어 최근 날짜가 비어 있는 경우가 많습니다. "
                "--deep 을 붙이면 가장 최근 날짜를 찾아 다시 점검합니다. 질문할 때는 에이전트가 최근 날짜를 알아서 찾습니다."
            )
        results.append(res)
        say(status_line(res))
    add_hints(results)
    return results


def add_hints(results: list[ApiCheckResult]) -> None:
    """오류 종류와 다른 API 의 성공 여부를 보고, 사용자가 할 일을 hint 에 적는다."""
    any_ok = any(r.status in ("ok", "empty") for r in results)
    for r in results:
        if r.status != "error" or r.hint:
            continue
        if r.error_kind == "auth":
            r.hint = (
                "같은 프로젝트 키로 다른 API 는 성공했습니다. 이 API 가 프로젝트에 담겨 있지 않은 경우가 가장 흔합니다. "
                "제주데이터허브에서 이 데이터의 Open API 를 같은 프로젝트에 추가한 뒤 다시 점검하세요. "
                "추가한 뒤에도 403 이면 API 고유 ID 가 다를 수 있으니, 데이터 상세 페이지의 요청 주소를 확인하세요."
                if any_ok
                else "성공한 API 가 하나도 없습니다. .env 의 JEJUDATAHUB_PROJECT_KEY(프로젝트 키)가 맞는지, "
                "이 API 가 프로젝트에 담겨 있는지 확인하세요."
            )
        elif r.error_kind == "not_found":
            r.hint = "API 고유 ID 가 틀렸을 수 있습니다. 데이터 상세 페이지의 요청 주소에서 ID 를 다시 확인하세요."
        elif r.error_kind == "blocked":
            r.hint = "허브 웹 방화벽이 이 컴퓨터를 막았습니다. 클라우드 서버가 아닌 집·학교 PC 에서 실행하세요."
        elif r.error_kind == "network":
            r.hint = "인터넷 연결을 확인한 뒤 다시 실행하세요."
        elif r.error_kind == "params":
            r.hint = "질의 변수가 맞지 않습니다. 허브 메시지를 보고 --param 이름=값 으로 바꿔 다시 점검하세요."


def _looks_numeric(v) -> bool:
    try:
        float(str(v).replace(",", ""))
        return True
    except ValueError:
        return False


def deep_check(
    client: DataHubClient,
    spec: DatasetSpec,
    params: dict,
    today: date | None = None,
    big_size: int = 1000,
    log=None,
    max_filters: int = 4,
    pause: float = 0.0,
) -> dict:
    """가장 최근 데이터 날짜, 페이지 넘김, 쪽당 최대 건수, 응답 메타, 필터 변수 동작을 확인한다.

    단계마다 따로 오류를 기록하므로 한 단계가 실패해도 나머지 결과는 남는다.
    log 로 단계마다 진행 상황을 알리고, 거르기 변수는 글자 컬럼 max_filters 개까지만 확인한다.
    """
    say = log or (lambda _m: None)
    today = today or date.today()
    c = client.for_paging(spec.api_paging)
    out: dict = {"errors": []}
    window = dict(params)

    def page(p: dict, n: int, size: int):
        if pause:
            time.sleep(pause)
        return c.fetch_page(spec.api_id, p, page=n, size=size)

    # 1) 가장 최근 데이터 날짜 → 점검 기간을 그 날짜 기준 최근 7일로 (하루만 받는 API 는 그날 하루)
    window_keys = [spec.day_param] if spec.day_param else spec.date_params[:2]
    if spec.day_param or len(spec.date_params) >= 2:
        say("가장 최근 데이터 날짜 확인 …")
        try:
            latest, n = find_latest_date(c, spec, params, today=today)
            out["latest_date"] = latest.isoformat() if latest else None
            out["probe_requests"] = n
            if latest:
                out["lag_days"] = (today - latest).days
                if spec.day_param:
                    window = {**params, spec.day_param: fmt_date(latest, spec)}
                else:
                    s_key, e_key = spec.date_params[:2]
                    window = {**params, s_key: fmt_date(latest - timedelta(days=6), spec), e_key: fmt_date(latest, spec)}
        except DataHubError as e:
            out["errors"].append(f"최신 날짜 확인: {e}")
    out["window"] = {k: window.get(k) for k in window_keys} if window_keys else {}

    # 1-1) 하루 단위 API 인데 어느 날짜에도 데이터가 없으면, 다른 날짜 변수 이름·형식을 시험해 본다
    if spec.day_param and "latest_date" in out and not out["latest_date"]:
        say("다른 날짜 변수 시험 …")
        skip = {spec.day_param, *spec.date_params[:2]}
        base = {k: v for k, v in params.items() if k not in skip}
        probes: dict = {}
        for d in (today - timedelta(days=2), date(2024, 6, 1)):
            ymd8, dash = d.strftime("%Y%m%d"), d.isoformat()
            variants = {
                f"{spec.day_param}={dash}": {spec.day_param: dash},
                f"baseDate={ymd8}": {"baseDate": ymd8},
                f"startDate·endDate={ymd8}": {"startDate": ymd8, "endDate": ymd8},
            }
            for label, extra in variants.items():
                try:
                    r = page({**base, **extra}, 1, 1)
                    probes[label] = r.total if r.total is not None else len(r.rows)
                except DataHubError as e:
                    probes[label] = f"오류: {str(e)[:80]}"
        try:
            r = page(base, 1, 1)
            probes["날짜 변수 없이"] = r.total if r.total is not None else len(r.rows)
        except DataHubError as e:
            probes["날짜 변수 없이"] = f"오류: {str(e)[:80]}"
        out["param_probe"] = probes

    # 2) 페이지 넘김과 응답 메타
    first_row: dict = {}
    window_total = None
    say("페이지 넘김 확인 …")
    try:
        p1 = page(window, 1, 2)
        p2 = page(window, 2, 2)
        out["meta"] = p1.meta
        window_total = p1.total
        out["window_total"] = window_total
        first_row = p1.rows[0] if p1.rows else {}
        if not p1.rows:
            out["paging"] = "확인 불가: 점검 기간에 데이터 없음"
        elif not p2.rows:
            out["paging"] = "2쪽 비어 있음" if (p1.total or 0) > 2 else "전체 2건 이하"
        elif p2.rows == p1.rows:
            out["paging"] = "실패: 2쪽이 1쪽과 같음 (페이지 변수 무시)"
        else:
            out["paging"] = "정상: 2쪽이 1쪽과 다름"
    except DataHubError as e:
        out["errors"].append(f"페이지 넘김: {e}")

    # 3) 쪽당 최대 건수
    say("쪽당 최대 건수 확인 …")
    try:
        big = page(window, 1, big_size)
        out["page_size"] = {"requested": big_size, "returned": len(big.rows), "max": None}
    except PageSizeTooLarge as e:
        out["page_size"] = {"requested": big_size, "returned": None, "max": e.max_size}
        try:
            ok = page(window, 1, e.max_size)
            out["page_size"]["returned"] = len(ok.rows)
        except DataHubError as e2:
            out["errors"].append(f"쪽당 최대 건수: {e2}")
    except DataHubError as e:
        out["errors"].append(f"쪽당 최대 건수: {e}")

    # 4) 필터 변수: 응답의 글자 컬럼 이름을 같은 이름의 질의 변수로 넣어 건수가 줄어드는지 본다
    filters: dict = {}
    if first_row and window_total:
        cols = [
            (col, val) for col, val in first_row.items()
            if val is not None and not _looks_numeric(val) and col not in spec.date_params and col != spec.day_param
        ][:max_filters]
        if cols:
            say(f"거르기 변수 {len(cols)}개 확인 …")
        for col, val in cols:
            try:
                r = page({**window, col: val}, 1, 1)
                total = r.total if r.total is not None else len(r.rows)
                filters[col] = {"value": val, "total": total, "works": bool(total) and total < window_total}
            except DataHubError as e:
                filters[col] = {"value": val, "error": str(e)[:160], "works": False}
    out["filters"] = filters
    return out


def format_report(results: list[ApiCheckResult], saved_path: str | Path | None = None) -> str:
    lines = ["# 제주데이터허브 Open API 점검 결과", f"점검 시각: {datetime.now().isoformat(timespec='seconds')}", ""]
    for r in results:
        lines.append(f"## {r.id} (No.{r.catalog_no}) {r.name}")
        if r.status == "no_api_id":
            lines.append(
                "- 상태: ○ api_id 미입력. 허브 데이터 상세 페이지의 API 요청 주소(…/api/proxy/고유ID/…)에서 "
                "고유 ID 를 확인해 registry.yaml 에 넣거나 Claude 에게 알려 주세요."
            )
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
        if r.hint:
            lines.append(f"- 도움말: {r.hint}")
        if r.params:
            lines.append("- 질의 변수: " + json.dumps(r.params, ensure_ascii=False))
        if r.deep:
            d = r.deep
            lines.append("- 정밀 점검:")
            if "latest_date" in d:
                if d.get("latest_date"):
                    lines.append(
                        f"  - 가장 최근 데이터 날짜: {d['latest_date']} (오늘보다 {d.get('lag_days')}일 전, 확인 요청 {d.get('probe_requests')}회)"
                    )
                else:
                    lines.append(f"  - 가장 최근 데이터 날짜: 찾지 못함 (10여 년 전까지 확인, 요청 {d.get('probe_requests')}회)")
            if d.get("param_probe"):
                tried = ", ".join(f"{k} → {v if isinstance(v, str) else f'{v:,}건'}" for k, v in d["param_probe"].items())
                lines.append(f"  - 다른 날짜 변수 시험: {tried}")
            if d.get("window"):
                total = d.get("window_total")
                lines.append(
                    "  - 점검 기간: " + " ~ ".join(str(v) for v in d["window"].values())
                    + (f", 전체 {total:,}건" if isinstance(total, int) else "")
                )
            if d.get("paging"):
                lines.append(f"  - 페이지 넘김: {d['paging']}")
            ps = d.get("page_size") or {}
            if ps.get("max"):
                lines.append(f"  - 쪽당 최대 건수: {ps['max']}건 ({ps['requested']}건 요청은 서버가 거부, {ps['max']}건 요청 → {ps.get('returned')}건 받음)")
            elif ps:
                lines.append(f"  - 쪽당 건수: {ps['requested']}건 요청 → {ps.get('returned')}건 받음")
            for col, f in (d.get("filters") or {}).items():
                if "error" in f:
                    lines.append(f"  - 필터 {col}={f['value']}: ✖ 오류 ({f['error']})")
                else:
                    mark = "✔ 동작" if f.get("works") else "✖ 효과 없음"
                    lines.append(f"  - 필터 {col}={f['value']}: {mark} (전체 {d.get('window_total')} → {f.get('total')}건)")
            if d.get("meta"):
                lines.append("  - 응답 메타 필드: " + json.dumps(d["meta"], ensure_ascii=False))
            for err in d.get("errors") or []:
                lines.append(f"  - 오류: {err}")
        lines.append("")
    n_ok = sum(r.status in ("ok", "empty") for r in results)
    n_err = sum(r.status == "error" for r in results)
    n_none = sum(r.status == "no_api_id" for r in results)
    lines.append(f"요약: 성공 {n_ok} · 실패 {n_err} · api_id 미입력 {n_none}")
    todo = _todo_lines(results)
    if todo:
        lines += ["", "할 일:"] + todo
    lines.append("")
    lines.append("이 출력 전체를 복사해 Claude 에게 보내면 registry.yaml 의 api_params·rename 을 실제 응답에 맞춰 줍니다.")
    if saved_path:
        lines.append(f"결과 파일: {saved_path}")
    return "\n".join(lines)


def run_deep_checks(client: DataHubClient, results: list[ApiCheckResult], specs: list[DatasetSpec], log=None, pause: float = 0.2) -> bool:
    """성공한 API 마다 정밀 점검. Ctrl+C 를 누르면 거기서 멈추고 True(중단됨)를 돌려준다."""
    say = log or (lambda _m: None)
    by_id = {sp.id: sp for sp in specs}
    try:
        for r in results:
            if r.status not in ("ok", "empty"):
                continue
            say(f"정밀 점검 중: No.{r.catalog_no} {r.name} …")
            r.deep = deep_check(client, by_id[r.id], r.params, log=lambda m: say("  " + m), pause=pause)
            if r.status == "empty":
                r.hint = ""  # 정밀 점검이 최근 날짜를 이미 찾았으므로 --deep 안내는 필요 없다
    except KeyboardInterrupt:
        say("⚠️ 정밀 점검을 중단했습니다. 지금까지의 결과만 보여 줍니다.")
        return True
    return False


def _todo_lines(results: list[ApiCheckResult]) -> list[str]:
    def names(rs: list[ApiCheckResult]) -> str:
        return ", ".join(f"No.{r.catalog_no} {r.name}" for r in rs)

    any_ok = any(r.status in ("ok", "empty") for r in results)
    out: list[str] = []
    auth = [r for r in results if r.status == "error" and r.error_kind == "auth"]
    if auth and any_ok:
        out.append(f"- 허브 프로젝트에 추가할 API: {names(auth)}")
    elif auth:
        out.append("- .env 의 JEJUDATAHUB_PROJECT_KEY 가 맞는지 확인")
    missing = [r for r in results if r.status == "error" and r.error_kind == "not_found"]
    if missing:
        out.append(f"- API 고유 ID 다시 확인: {names(missing)}")
    empty = [r for r in results if r.status == "empty" and r.hint]
    if empty:
        out.append(f"- 최근 날짜 확인(--deep): {names(empty)}")
    return out


def save_results(results: list[ApiCheckResult], out_dir: Path, path: Path | None = None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = path or out_dir / f"api_check_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=1), encoding="utf-8")
    return path
