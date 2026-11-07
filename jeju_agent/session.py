"""분석 세션 상태: 불러온 데이터, 파생 표, 차트, 실행 코드 이력, 보고서, 작업 기록."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from .datahub.loaders import LoadedDataset


@dataclass
class CodeRecord:
    step: int
    purpose: str
    code: str
    ok: bool
    summary: str
    elapsed: float
    created_at: str


@dataclass
class ChartRecord:
    id: str
    kind: str  # plotly | image
    path: str
    title: str
    table: str = ""
    spec: dict = field(default_factory=dict)
    created_at: str = ""


@dataclass
class ReportRecord:
    title: str
    markdown_path: str
    html_path: str
    created_at: str


class AnalysisSession:
    """한 사용자의 대화 동안 유지되는 작업 공간."""

    def __init__(self, session_id: str | None = None):
        self.id = session_id or datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
        self.created_at = datetime.now().isoformat(timespec="seconds")
        self.tables: dict[str, pd.DataFrame] = {}
        self.datasets: dict[str, LoadedDataset] = {}
        self.table_meta: dict[str, dict] = {}  # 파생 표의 출처(코드 step) 등
        self.charts: list[ChartRecord] = []
        self.code_history: list[CodeRecord] = []
        self.reports: list[ReportRecord] = []
        self.step_counter = 0
        self.result_counter = 0

    # ------------------------------------------------------------------ 데이터
    def add_dataset(self, ds: LoadedDataset) -> None:
        self.datasets[ds.id] = ds
        self.tables[ds.id] = ds.df
        self.table_meta[ds.id] = {"kind": "dataset", "name": ds.name, "provenance": ds.provenance}

    def add_table(self, name: str, df: pd.DataFrame, origin: str = "") -> None:
        self.tables[name] = df
        self.table_meta[name] = {"kind": "derived", "origin": origin}

    def next_result_name(self) -> str:
        self.result_counter += 1
        return f"result_{self.result_counter}"

    def next_step(self) -> int:
        self.step_counter += 1
        return self.step_counter

    def get_table(self, name: str) -> pd.DataFrame:
        if name in self.tables:
            return self.tables[name]
        # 느슨한 매칭: 대소문자/공백 무시, 데이터셋 이름으로도 찾는다
        key = str(name).strip().lower()
        for k in self.tables:
            if k.lower() == key:
                return self.tables[k]
        for ds in self.datasets.values():
            if ds.name == name or (ds.spec.catalog_no is not None and str(ds.spec.catalog_no) == str(name)):
                return ds.df
        raise KeyError(f"표 '{name}' 이(가) 없습니다. 사용 가능한 표: {', '.join(self.tables) or '(없음)'}")

    def tables_markdown(self) -> str:
        if not self.tables:
            return "(아직 불러온 표가 없습니다)"
        lines = ["| 표 이름 | 종류 | 크기 | 컬럼 |", "|---|---|---|---|"]
        for name, df in self.tables.items():
            meta = self.table_meta.get(name, {})
            kind = "데이터셋" if meta.get("kind") == "dataset" else "파생 표"
            if meta.get("kind") == "dataset":
                kind += f" ({self.datasets[name].provenance_label()})" if name in self.datasets else ""
            cols = ", ".join(str(c) for c in list(df.columns)[:12])
            if df.shape[1] > 12:
                cols += " …"
            lines.append(f"| {name} | {kind} | {len(df):,}행×{df.shape[1]}열 | {cols} |")
        return "\n".join(lines)

    # ------------------------------------------------------------------ 기록
    def record_code(self, purpose: str, code: str, ok: bool, summary: str, elapsed: float) -> CodeRecord:
        rec = CodeRecord(
            step=self.next_step(),
            purpose=purpose,
            code=code,
            ok=ok,
            summary=summary,
            elapsed=elapsed,
            created_at=datetime.now().isoformat(timespec="seconds"),
        )
        self.code_history.append(rec)
        return rec

    def add_chart(self, kind: str, path: str, title: str, table: str = "", spec: dict | None = None) -> ChartRecord:
        rec = ChartRecord(
            id=f"c{len(self.charts) + 1}",
            kind=kind,
            path=path,
            title=title,
            table=table,
            spec=spec or {},
            created_at=datetime.now().isoformat(timespec="seconds"),
        )
        self.charts.append(rec)
        return rec

    def get_chart(self, chart_id: str) -> ChartRecord | None:
        for c in self.charts:
            if c.id == chart_id:
                return c
        return None

    def citations(self) -> list[dict]:
        return [ds.citation() for ds in self.datasets.values()]

    def uses_synthetic(self) -> bool:
        return any(ds.is_synthetic for ds in self.datasets.values())

    def reproduction_script(self) -> str:
        """지금까지 실행한 코드를 순서대로 묶은 재현 스크립트."""
        lines = [
            "# 제주 데이터 길잡이 - 분석 재현 스크립트",
            f"# 세션 {self.id} / 생성 {datetime.now().isoformat(timespec='seconds')}",
            "# 실행: 프로젝트 루트에서 `python <이 파일>` (data/raw 또는 data/samples 의 데이터를 사용)",
            "import sys",
            "sys.path.insert(0, '.')",
            "import pandas as pd",
            "import numpy as np",
            "from jeju_agent.datahub import DataStore",
            "",
            "store = DataStore()",
            "tables = {}",
            "",
            "def save_table(name, df):",
            "    tables[name] = df",
            "",
            "def show(df, n=20):",
            "    print(df.head(n).to_string())",
            "",
        ]
        for ds in self.datasets.values():
            lines.append(f"{ds.id} = store.load({ds.id!r}, prefer={ds.provenance!r}).df  # {ds.name}")
            lines.append(f"tables[{ds.id!r}] = {ds.id}")
        lines.append("")
        for rec in self.code_history:
            status = "성공" if rec.ok else "실패(당시)"
            lines.append(f"# ──── 단계 {rec.step}: {rec.purpose} [{status}] ────")
            lines.append(rec.code.rstrip())
            lines.append("")
        return "\n".join(lines)
