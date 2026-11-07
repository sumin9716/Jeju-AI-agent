"""제주데이터허브 Open API 일괄 점검.

등록 데이터셋 중 api_id 가 있는 것마다 첫 페이지(몇 건)만 호출해
성공 여부, 전체 건수, 응답 컬럼, 레지스트리에 없는 컬럼, 예시 행을 보고한다.
이 보고서를 보고 registry.yaml 의 api_params·rename 을 실제 응답에 맞춘다.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
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
            pages = client.iter_pages(spec.api_id, params)
            _, page_rows, total = next(pages)
            pages.close()
        except StopIteration:
            page_rows, total = [], None
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
