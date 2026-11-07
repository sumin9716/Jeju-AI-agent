"""데이터 로더: 레지스트리의 데이터셋을 API → 내려받은 파일 → 합성 샘플 순으로 해결한다.

각 결과에는 provenance(api/file/sample) 가 붙어 보고서에 출처와 한계를 명시할 수 있다.
"""

from __future__ import annotations

import glob
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

from ..config import RAW_DIR, SAMPLES_DIR, Settings
from .client import DataHubClient, DataHubError
from .registry import DatasetSpec, Registry, load_registry

ENCODINGS = ("utf-8-sig", "cp949", "euc-kr", "utf-8")


@dataclass
class LoadedDataset:
    id: str
    name: str
    df: pd.DataFrame
    provenance: str  # api | file | sample
    source: str
    fetched_at: str
    is_synthetic: bool
    spec: DatasetSpec
    notes: list[str] = field(default_factory=list)

    @property
    def rows(self) -> int:
        return int(len(self.df))

    def provenance_label(self) -> str:
        return {
            "api": "제주데이터허브 Open API 실시간 수집",
            "file": "제주데이터허브에서 내려받은 파일(data/raw)",
            "sample": "합성 샘플 데이터(data/samples, 실제 값 아님)",
        }.get(self.provenance, self.provenance)

    def schema_markdown(self, preview_rows: int = 5) -> str:
        return describe_dataframe(self.df, preview_rows=preview_rows)

    def citation(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "catalog_no": self.spec.catalog_no,
            "provider": self.spec.provider,
            "source_url": self.spec.source_url,
            "provenance": self.provenance,
            "provenance_label": self.provenance_label(),
            "source": self.source,
            "fetched_at": self.fetched_at,
            "rows": self.rows,
            "period": self.spec.update,
            "license": self.spec.license,
            "is_synthetic": self.is_synthetic,
        }


def describe_dataframe(df: pd.DataFrame, preview_rows: int = 5, max_cols: int = 40) -> str:
    """에이전트(LLM)에게 보여줄 DataFrame 요약: 크기, 컬럼별 형식/결측/예시, 미리보기."""
    lines = [f"- 크기: {len(df):,}행 × {df.shape[1]}열"]
    lines.append("| 컬럼 | 형식 | 결측 | 예시값 / 범위 |")
    lines.append("|---|---|---|---|")
    for col in list(df.columns)[:max_cols]:
        s = df[col]
        dtype = str(s.dtype)
        n_null = int(s.isna().sum())
        example = ""
        try:
            if pd.api.types.is_numeric_dtype(s) and s.notna().any():
                example = f"{s.min():,.4g} ~ {s.max():,.4g} (평균 {s.mean():,.4g})"
            elif pd.api.types.is_datetime64_any_dtype(s) and s.notna().any():
                example = f"{s.min().date()} ~ {s.max().date()}"
            else:
                uniq = s.dropna().astype(str).unique()
                example = ", ".join(list(uniq[:5]))
                if len(uniq) > 5:
                    example += f" … (고유값 {len(uniq):,}개)"
        except Exception:  # noqa: BLE001 - 요약은 실패해도 분석을 막지 않는다
            example = ""
        lines.append(f"| {col} | {dtype} | {n_null:,} | {example} |")
    if df.shape[1] > max_cols:
        lines.append(f"| … | | | (열 {df.shape[1] - max_cols}개 생략) |")
    if preview_rows and len(df):
        try:
            lines.append("")
            lines.append("미리보기:")
            lines.append(df.head(preview_rows).to_markdown(index=False))
        except Exception:  # noqa: BLE001
            lines.append(df.head(preview_rows).to_string(index=False))
    return "\n".join(lines)


def read_table_file(path: Path) -> pd.DataFrame:
    """CSV/XLSX 를 인코딩 자동 판별로 읽는다."""
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xlsm", ".xls"):
        return pd.read_excel(path, engine="openpyxl")
    last_err: Exception | None = None
    for enc in ENCODINGS:
        try:
            return pd.read_csv(path, encoding=enc, low_memory=False)
        except UnicodeDecodeError as e:
            last_err = e
            continue
    raise ValueError(f"인코딩을 판별하지 못했습니다: {path} ({last_err})")


def _coerce_date(series: pd.Series) -> pd.Series:
    s = series.astype(str).str.strip()
    digits = s.str.fullmatch(r"\d{8}")
    if digits.fillna(False).all():
        return pd.to_datetime(s, format="%Y%m%d", errors="coerce")
    return pd.to_datetime(s.replace({"": None, "nan": None, "None": None}), errors="coerce")


def normalize_dataframe(df: pd.DataFrame, spec: DatasetSpec) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    rename = {k: v for k, v in (spec.rename or {}).items() if k in df.columns and v not in df.columns}
    if rename:
        df = df.rename(columns=rename)
    for col in spec.columns:
        if col.name not in df.columns:
            continue
        if col.dtype == "date":
            df[col.name] = _coerce_date(df[col.name])
        elif col.dtype in ("int", "float"):
            cleaned = df[col.name].astype(str).str.replace(",", "", regex=False).str.strip()
            num = pd.to_numeric(cleaned.replace({"": None, "nan": None, "None": None}), errors="coerce")
            if col.dtype == "int" and num.notna().all():
                num = num.astype("int64")
            df[col.name] = num
        else:
            df[col.name] = df[col.name].astype(str).str.strip().replace({"nan": None, "None": None, "": None})
    return df


class DataStore:
    def __init__(
        self,
        registry: Registry | None = None,
        settings: Settings | None = None,
        client: DataHubClient | None = None,
        raw_dir: Path | str = RAW_DIR,
        samples_dir: Path | str = SAMPLES_DIR,
    ):
        self.registry = registry or load_registry()
        self.settings = settings or Settings()
        self.raw_dir = Path(raw_dir)
        self.samples_dir = Path(samples_dir)
        self.client = client or DataHubClient(
            project_key=self.settings.datahub_project_key, base_url=self.settings.datahub_base_url
        )

    # ------------------------------------------------------------------ 상태
    def find_files(self, spec: DatasetSpec) -> list[Path]:
        found: list[Path] = []
        for pattern in spec.file_patterns:
            found.extend(Path(p) for p in glob.glob(str(self.raw_dir / pattern)))
            found.extend(Path(p) for p in glob.glob(str(self.raw_dir / "**" / pattern), recursive=True))
        uniq = sorted({p.resolve() for p in found if p.is_file()})
        return list(uniq)

    def sample_path(self, spec: DatasetSpec) -> Path | None:
        if not spec.sample_file:
            return None
        p = self.samples_dir / spec.sample_file
        return p if p.exists() else None

    def sources(self, spec: DatasetSpec) -> dict:
        return {
            "api": bool(spec.api_id) and self.client.configured and spec.access in ("api", "api,file"),
            "api_id_set": bool(spec.api_id),
            "files": [str(p) for p in self.find_files(spec)],
            "sample": str(self.sample_path(spec)) if self.sample_path(spec) else None,
        }

    def preferred_source(self, spec: DatasetSpec) -> str | None:
        src = self.sources(spec)
        if src["api"]:
            return "api"
        if src["files"]:
            return "file"
        if src["sample"]:
            return "sample"
        return None

    def status(self) -> list[dict]:
        rows = []
        for spec in self.registry:
            src = self.sources(spec)
            rows.append(
                {
                    "id": spec.id,
                    "catalog_no": spec.catalog_no,
                    "name": spec.name,
                    "access": spec.access,
                    "api_ready": src["api"],
                    "api_id_set": src["api_id_set"],
                    "files": len(src["files"]),
                    "sample": bool(src["sample"]),
                    "preferred": self.preferred_source(spec),
                }
            )
        return rows

    # ------------------------------------------------------------------ 로드
    def load(
        self,
        key: str,
        prefer: str = "auto",
        params: dict | None = None,
        refresh: bool = False,
        max_rows: int | None = None,
    ) -> LoadedDataset:
        spec = self.registry.find(key)
        if spec is None:
            raise KeyError(f"레지스트리에 없는 데이터셋: {key!r}. 등록된 id: {', '.join(self.registry.ids())}")
        order = {
            "auto": ["api", "file", "sample"],
            "api": ["api"],
            "file": ["file"],
            "sample": ["sample"],
        }.get(prefer, ["api", "file", "sample"])
        errors: list[str] = []
        for source in order:
            try:
                if source == "api":
                    return self._load_api(spec, params, refresh, max_rows)
                if source == "file":
                    return self._load_file(spec, max_rows)
                if source == "sample":
                    return self._load_sample(spec, max_rows)
            except (DataHubError, FileNotFoundError) as e:
                errors.append(f"{source}: {e}")
                continue
        raise DataHubError(f"'{spec.name}' 을(를) 불러올 수 없습니다. " + " | ".join(errors))

    def _load_api(self, spec: DatasetSpec, params: dict | None, refresh: bool, max_rows: int | None) -> LoadedDataset:
        if spec.access not in ("api", "api,file"):
            raise DataHubError("Open API 로 제공되지 않는 데이터입니다.")
        merged = dict(spec.api_params or {})
        merged.update(params or {})
        res = self.client.fetch(spec.api_id, merged, refresh=refresh, max_rows=max_rows)
        df = normalize_dataframe(res.df, spec)
        notes = [f"API 호출 {res.pages}페이지, 전체 {res.total if res.total is not None else '알 수 없음'}건"]
        if res.from_cache:
            notes.append("디스크 캐시에서 읽음 (refresh=True 로 새로 수집 가능)")
        if merged:
            notes.append(f"질의 변수: {merged}")
        return LoadedDataset(
            id=spec.id,
            name=spec.name,
            df=df,
            provenance="api",
            source=res.url.replace(self.client.project_key, "{PROJECT_KEY}") if self.client.project_key else res.url,
            fetched_at=datetime.now().isoformat(timespec="seconds"),
            is_synthetic=False,
            spec=spec,
            notes=notes,
        )

    def _load_file(self, spec: DatasetSpec, max_rows: int | None) -> LoadedDataset:
        paths = self.find_files(spec)
        if not paths:
            raise FileNotFoundError(
                f"data/raw 에 '{spec.file_glob}' 패턴의 파일이 없습니다. 제주데이터허브에서 내려받아 저장하세요."
            )
        frames = [read_table_file(p) for p in paths]
        df = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
        df = normalize_dataframe(df, spec)
        if max_rows:
            df = df.head(max_rows)
        return LoadedDataset(
            id=spec.id,
            name=spec.name,
            df=df,
            provenance="file",
            source="; ".join(str(p) for p in paths),
            fetched_at=datetime.fromtimestamp(max(p.stat().st_mtime for p in paths)).isoformat(timespec="seconds"),
            is_synthetic=False,
            spec=spec,
            notes=[f"파일 {len(paths)}개 병합"] if len(paths) > 1 else [],
        )

    def _load_sample(self, spec: DatasetSpec, max_rows: int | None) -> LoadedDataset:
        p = self.sample_path(spec)
        if p is None:
            raise FileNotFoundError("샘플 파일이 없습니다 (scripts/make_samples.py 실행).")
        df = normalize_dataframe(read_table_file(p), spec)
        if max_rows:
            df = df.head(max_rows)
        return LoadedDataset(
            id=spec.id,
            name=spec.name,
            df=df,
            provenance="sample",
            source=str(p),
            fetched_at=datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"),
            is_synthetic=True,
            spec=spec,
            notes=["합성 샘플: 실제 통계가 아니며 구조·형식 확인과 시연용입니다. 결론을 실제 현황으로 해석하면 안 됩니다."],
        )
