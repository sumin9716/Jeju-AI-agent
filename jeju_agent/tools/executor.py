"""도구 실행기: Claude 의 tool_use 입력을 받아 실제 작업을 수행하고 결과 텍스트(+UI용 payload)를 돌려준다."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from ..catalog import Catalog, expand_query
from ..charts import ChartError, build_chart
from ..config import CHART_DIR, REPORT_DIR, Settings
from ..datahub import DataHubError, DataStore
from ..datahub.loaders import API_UNAVAILABLE_LABELS
from ..report import write_report
from ..sandbox import run_code
from ..session import AnalysisSession


@dataclass
class ToolOutcome:
    text: str
    is_error: bool = False
    payload: dict = field(default_factory=dict)


class ToolExecutor:
    def __init__(
        self,
        session: AnalysisSession,
        store: DataStore,
        catalog: Catalog,
        settings: Settings | None = None,
        model_name: str = "",
    ):
        self.session = session
        self.store = store
        self.catalog = catalog
        self.settings = settings or Settings()
        self.model_name = model_name or self.settings.model

    # ------------------------------------------------------------------ 진입점
    def execute(self, name: str, args: dict[str, Any] | None) -> ToolOutcome:
        args = dict(args or {})
        handler = {
            "search_catalog": self.search_catalog,
            "list_datasets": self.list_datasets,
            "load_dataset": self.load_dataset,
            "load_file": self.load_file,
            "run_python": self.run_python,
            "make_chart": self.make_chart,
            "write_report": self.write_report,
        }.get(name)
        if handler is None:
            return ToolOutcome(text=f"알 수 없는 도구: {name}", is_error=True)
        try:
            return handler(**args)
        except TypeError as e:  # 잘못된 인자 이름/누락
            return ToolOutcome(text=f"도구 인자 오류: {e}", is_error=True)
        except (DataHubError, ChartError, KeyError, ValueError, FileNotFoundError, PermissionError) as e:
            return ToolOutcome(text=f"{type(e).__name__}: {e}", is_error=True)
        except Exception as e:  # noqa: BLE001 - 어떤 예외도 모델에게 돌려주어 스스로 복구하게 한다
            return ToolOutcome(text=f"예상치 못한 오류 {type(e).__name__}: {e}", is_error=True)

    # ------------------------------------------------------------------ 카탈로그
    def search_catalog(self, query: str, top_k: int = 8, only_api: bool = False, category: str | None = None) -> ToolOutcome:
        top_k = max(1, min(int(top_k or 8), 30))
        boost = {spec.catalog_no for spec in self.store.registry if spec.catalog_no is not None}
        entries = self.catalog.search(query, top_k=top_k, only_api=bool(only_api), category=category, boost_nos=boost)
        terms = expand_query(query)
        # 레지스트리(바로 불러오기 가능) 항목 중 검색어와 맞는 것도 함께 알려준다.
        ready: list[str] = []
        for spec in self.store.registry:
            text = " ".join([spec.name, spec.category, spec.notes, " ".join(c.name for c in spec.columns)]).lower()
            if any(t in text for t in terms[:6]):
                ready.append(f"- `{spec.id}` (No.{spec.catalog_no}) {spec.name} — 출처 상태: {self._source_label(spec)}")
        if not entries and not ready:
            return ToolOutcome(text=f"'{query}' 에 맞는 데이터를 찾지 못했습니다. 다른 표현(예: 유동인구, 카드, 버스, 기상, 관광객)으로 다시 검색하세요.")
        lines = [f"'{query}' 검색 결과 (제주데이터허브 데이터 목록 1,259종 중 상위 {len(entries)}개):", ""]
        lines.append("| No. | 데이터명 | 카테고리 | 타입 | 수집 기간 | 즐겨찾기 | 바로 불러오기 |")
        lines.append("|---|---|---|---|---|---|---|")
        for e in entries:
            spec = self.store.registry.by_catalog_no(e.no)
            loadable = f"가능 (`{spec.id}`)" if spec else "레지스트리 미등록"
            lines.append(f"| {e.no} | {e.name} | {e.category} | {e.data_type} | {e.period} | {e.favorites} | {loadable} |")
        if ready:
            lines += ["", "검색어와 관련 있는 등록 데이터셋(load_dataset 으로 바로 사용 가능):"] + ready
        raw_files = self.store.list_raw_files()
        if raw_files:
            lines += ["", "data/raw 에 있는 사용자 파일 (load_file 로 사용 가능): " + ", ".join(f["name"] for f in raw_files[:15])]
        lines += [
            "",
            "안내: '레지스트리 미등록' 데이터는 사용자가 제주데이터허브 상세 페이지에서 내려받아 data/raw 에 넣으면 load_file 로 분석할 수 있다. "
            "그 경우 사용자에게 데이터명과 No., 저장 위치(data/raw)를 알려주고, 등록된 대체 데이터로 먼저 분석할 수 있는지도 설명한다.",
        ]
        return ToolOutcome(text="\n".join(lines), payload={"kind": "catalog", "entries": [e.to_dict() for e in entries]})

    def _source_label(self, spec) -> str:
        pref = self.store.preferred_source(spec)
        label = {"api": "Open API 준비됨", "file": "내려받은 파일 있음", "sample": "합성 샘플만 있음", None: "사용 불가"}[pref]
        reason = self.store.api_unavailable_reason(spec)
        if pref != "api" and reason:
            label += f" ({API_UNAVAILABLE_LABELS[reason]})"
        return label

    def list_datasets(self) -> ToolOutcome:
        lines = ["등록 데이터셋 (load_dataset 으로 불러올 수 있음):", "", "| id | No. | 데이터명 | 제공 방식 | 현재 출처 | 주요 컬럼 |", "|---|---|---|---|---|---|"]
        for spec in self.store.registry:
            cols = ", ".join(c.name for c in spec.columns[:6])
            lines.append(f"| `{spec.id}` | {spec.catalog_no} | {spec.name} | {spec.access} | {self._source_label(spec)} | {cols} |")
        raw_files = self.store.list_raw_files()
        lines += ["", f"data/raw 의 사용자 파일 {len(raw_files)}개 (load_file 로 불러오기):"]
        if raw_files:
            lines.append("| 파일 | 크기(KB) | 수정 시각 |")
            lines.append("|---|---|---|")
            for f in raw_files[:40]:
                lines.append(f"| {f['name']} | {f['size_kb']} | {f['modified']} |")
            if len(raw_files) > 40:
                lines.append(f"| … 외 {len(raw_files) - 40}개 | | |")
        else:
            lines.append("(없음) 허브에서 내려받은 CSV/XLSX 를 data/raw 에 넣으면 여기 표시된다.")
        lines += ["", "현재 세션에 올라온 표:", self.session.tables_markdown()]
        return ToolOutcome(text="\n".join(lines), payload={"kind": "status", "status": self.store.status(), "raw_files": raw_files})

    # ------------------------------------------------------------------ 데이터 적재
    def load_dataset(self, dataset: str, source: str = "auto", params: dict | None = None, max_rows: int | None = None) -> ToolOutcome:
        ds = self.store.load(str(dataset), prefer=source or "auto", params=params or None, max_rows=max_rows)
        self.session.add_dataset(ds)
        lines = [f"'{ds.name}' (No.{ds.spec.catalog_no}) 을(를) 표 `{ds.id}` 로 불러왔습니다.", f"- 출처: {ds.provenance_label()} ({ds.source})", f"- 수집/갱신 시점: {ds.fetched_at}"]
        if ds.spec.update:
            lines.append(f"- 데이터 수집 기간(허브 명세): {ds.spec.update}")
        for note in ds.notes:
            lines.append(f"- 참고: {note}")
        if ds.is_synthetic:
            lines.append("- ⚠️ 합성 샘플입니다. 답변과 보고서에 '샘플 데이터 기준'임을 반드시 밝히세요.")
        if ds.spec.notes:
            lines.append(f"- 데이터 특성/한계: {ds.spec.notes}")
        lines += ["", "구조 요약:", ds.schema_markdown(preview_rows=5)]
        return ToolOutcome(text="\n".join(lines), payload={"kind": "dataset", "dataset": ds})

    def load_file(self, file: str, sheet: str | None = None, table_name: str | None = None, max_rows: int | None = None) -> ToolOutcome:
        ds = self.store.load_file(str(file), sheet=sheet, table_name=table_name, max_rows=max_rows)
        self.session.add_dataset(ds)
        lines = [
            f"파일 '{ds.name}' 을(를) 표 `{ds.id}` 로 불러왔습니다.",
            f"- 출처: {ds.provenance_label()} ({ds.source})",
            f"- 파일 수정 시각: {ds.fetched_at}",
            "- 이 파일의 데이터명·수집 기간·이용조건은 제주데이터허브 상세 페이지 기준이므로 답변에 파일 이름과 함께 밝히세요.",
        ]
        lines += [f"- 참고: {n}" for n in ds.notes]
        lines += ["", "구조 요약:", ds.schema_markdown(preview_rows=5)]
        return ToolOutcome(text="\n".join(lines), payload={"kind": "dataset", "dataset": ds})

    # ------------------------------------------------------------------ 코드 실행
    def run_python(self, purpose: str, code: str) -> ToolOutcome:
        res = run_code(code, self.session.tables, timeout=self.settings.sandbox_timeout, chart_dir=CHART_DIR)
        for name, df in res.new_tables.items():
            self.session.add_table(name, df, origin=f"step:{self.session.step_counter + 1}")
        result_name = ""
        if res.ok and isinstance(res.result_df, pd.DataFrame) and not res.new_tables:
            result_name = self.session.next_result_name()
            self.session.add_table(result_name, res.result_df, origin=f"step:{self.session.step_counter + 1}")
        charts = []
        for c in res.charts:
            rec = self.session.add_chart(kind=c["kind"], path=c["path"], title=c.get("title") or purpose)
            charts.append(rec)
        summary = res.error if not res.ok else (res.result_preview[:200] or res.stdout[:200])
        rec = self.session.record_code(purpose=purpose, code=code, ok=res.ok, summary=summary, elapsed=res.elapsed)
        text = res.to_model_text()
        if result_name:
            text += f"\n\n(result 표는 `{result_name}` 이름으로 세션에 저장되어 make_chart 에서 쓸 수 있습니다)"
        if charts:
            text += "\n\n차트 id: " + ", ".join(f"{c.id} ({c.title})" for c in charts)
        payload = {"kind": "code", "code": code, "purpose": purpose, "stdout": res.stdout, "result_df": res.result_df, "charts": charts, "ok": res.ok, "step": rec.step, "error": res.error}
        return ToolOutcome(text=text, is_error=not res.ok, payload=payload)

    # ------------------------------------------------------------------ 차트
    def make_chart(self, table: str, chart_type: str, x: str, title: str, y: str | None = None, value: str | None = None, color: str | None = None, agg: str | None = None, top_n: int | None = None, sort: str = "none", x_label: str | None = None, y_label: str | None = None, filter: str | None = None) -> ToolOutcome:  # noqa: A002
        rec, notes = build_chart(self.session, table=table, chart_type=chart_type, x=x, y=y, value=value, color=color, agg=agg, top_n=top_n, sort=sort or "none", title=title, x_label=x_label, y_label=y_label, filter=filter, chart_dir=CHART_DIR)
        lines = [f"차트 {rec.id} 생성: '{title}' ({chart_type}, 표 {table}, {rec.spec.get('rows_plotted', 0):,}행 표시)"]
        lines += [f"- {n}" for n in notes]
        lines.append("사용자 화면에 바로 표시됩니다. 보고서에 넣으려면 write_report 의 chart_ids 에 이 id 를 적으세요.")
        return ToolOutcome(text="\n".join(lines), payload={"kind": "chart", "chart": rec})

    # ------------------------------------------------------------------ 보고서
    def write_report(self, title: str, summary: str, sections: list[dict], conclusions: list[str], limitations: list[str], recommendations: list[str] | None = None) -> ToolOutcome:
        rec = write_report(self.session, title=title, summary=summary, sections=sections or [], conclusions=list(conclusions or []), limitations=list(limitations or []), recommendations=list(recommendations or []) or None, model=self.model_name, report_dir=REPORT_DIR)
        text = f"보고서를 저장했습니다.\n- Markdown: {rec.markdown_path}\n- HTML(차트 포함): {rec.html_path}\n사용자에게 파일 위치를 알려주고 핵심 결론을 짧게 다시 요약하세요."
        return ToolOutcome(text=text, payload={"kind": "report", "report": rec})


def tool_input_summary(name: str, args: dict | None) -> str:
    """UI 에 보여줄 짧은 도구 호출 설명."""
    args = args or {}
    if name == "search_catalog":
        return f"데이터 목록 검색: '{args.get('query', '')}'"
    if name == "list_datasets":
        return "사용 가능한 데이터 확인"
    if name == "load_dataset":
        return f"데이터 불러오기: {args.get('dataset', '')} (출처 {args.get('source', 'auto')})"
    if name == "load_file":
        return f"내려받은 파일 불러오기: {args.get('file', '')}"
    if name == "run_python":
        return f"분석 코드 실행: {args.get('purpose', '')}"
    if name == "make_chart":
        return f"차트 만들기: {args.get('title', '')}"
    if name == "write_report":
        return f"보고서 작성: {args.get('title', '')}"
    return f"{name} {json.dumps(args, ensure_ascii=False)[:80]}"
