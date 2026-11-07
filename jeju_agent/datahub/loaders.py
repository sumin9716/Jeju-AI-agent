"""데이터 로더: 레지스트리의 데이터셋을 API → 내려받은 파일 → 합성 샘플 순으로 해결한다.

각 결과에는 provenance(api/file/sample) 가 붙어 보고서에 출처와 한계를 명시할 수 있다.
"""

from __future__ import annotations

import glob
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

import pandas as pd

from ..config import RAW_DIR, SAMPLES_DIR, Settings
from .client import DataHubClient, DataHubError, render_params
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


RAW_EXTENSIONS = (".csv", ".tsv", ".txt", ".xlsx", ".xlsm", ".xls")
DATE_HINTS = ("날짜", "일자", "일시", "연월", "년월", "기준일", "기준년월", "date", "ymd", "yyyymm")


def _mostly_unnamed(df: pd.DataFrame) -> bool:
    cols = [str(c) for c in df.columns]
    return len(cols) > 1 and sum(c.startswith("Unnamed") for c in cols) >= len(cols) / 2


def read_table_file(path: Path, sheet: str | int | None = None, header: int = 0) -> pd.DataFrame:
    """CSV/TSV/TXT/XLSX 를 인코딩 자동 판별로 읽는다. 제목 행 때문에 열 이름이 비면 한 줄 아래를 머리글로 다시 읽는다."""
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xlsm", ".xls"):
        df = pd.read_excel(path, engine="openpyxl", sheet_name=0 if sheet is None else sheet, header=header)
        if _mostly_unnamed(df) and header == 0:
            return read_table_file(path, sheet=sheet, header=1)
        return df
    last_err: Exception | None = None
    for enc in ENCODINGS:
        try:
            if suffix == ".tsv":
                df = pd.read_csv(path, encoding=enc, sep="\t", low_memory=False, header=header)
            elif suffix == ".txt":
                df = pd.read_csv(path, encoding=enc, sep=None, engine="python", header=header)
            else:
                df = pd.read_csv(path, encoding=enc, low_memory=False, header=header)
            if _mostly_unnamed(df) and header == 0:
                return read_table_file(path, sheet=sheet, header=1)
            return df
        except UnicodeDecodeError as e:
            last_err = e
            continue
    raise ValueError(f"인코딩을 판별하지 못했습니다: {path} ({last_err})")


def sanitize_table_name(stem: str) -> str:
    """파일 이름을 파이썬 변수로 쓸 수 있는 표 이름으로 바꾼다 (한글 허용)."""
    name = re.sub(r"[^0-9A-Za-z_가-힣]+", "_", str(stem)).strip("_")
    if not name:
        name = "table"
    if name[0].isdigit():
        name = "t_" + name
    return name[:60]


def infer_types(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """스키마를 모르는 파일의 문자열 컬럼 중 숫자(쉼표 포함)·날짜로 보이는 것을 변환한다."""
    notes: list[str] = []
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    for col in df.columns:
        s = df[col]
        is_text = s.dtype == object or pd.api.types.is_string_dtype(s)
        # 정수로 읽힌 YYYYMMDD / YYYYMM 날짜 (예: 20200616, 202401)
        if not is_text and pd.api.types.is_integer_dtype(s) and any(h in col.lower() for h in DATE_HINTS):
            vals = s.dropna()
            if not vals.empty and (vals.between(19000101, 21001231).all() or vals.between(190001, 210012).all()):
                parsed = _coerce_date(s)
                if parsed.notna().sum() >= 0.9 * len(vals):
                    df[col] = parsed
                    notes.append(f"'{col}' → 날짜")
            continue
        if not is_text:
            continue
        text = s.dropna().astype(str).str.strip()
        text = text[(text != "") & (text.str.lower() != "nan")]
        if text.empty:
            continue
        sample = text.head(3000)
        # 날짜 힌트가 있는 열은 숫자(YYYYMMDD)로 보여도 날짜를 먼저 시도한다.
        if any(h in col.lower() for h in DATE_HINTS):
            parsed = _coerce_date(s)
            if parsed.notna().sum() >= 0.9 * len(text):
                df[col] = parsed
                notes.append(f"'{col}' → 날짜")
                continue
        if sample.str.fullmatch(r"[-+]?\d{1,3}(,\d{3})+(\.\d+)?|[-+]?\d+(\.\d+)?").mean() >= 0.95:
            cleaned = s.astype(str).str.replace(",", "", regex=False).str.strip().replace({"": None, "nan": None, "None": None})
            df[col] = pd.to_numeric(cleaned, errors="coerce")
            notes.append(f"'{col}' → 숫자")
    return df, notes


def _coerce_date(series: pd.Series) -> pd.Series:
    s = series.astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    s = s.replace({"": None, "nan": None, "None": None, "NaT": None, "<NA>": None})
    valid = s.dropna()
    if not valid.empty and valid.str.fullmatch(r"\d{8}").all():
        return pd.to_datetime(s, format="%Y%m%d", errors="coerce")
    if not valid.empty and valid.str.fullmatch(r"\d{6}").all():
        return pd.to_datetime(s, format="%Y%m", errors="coerce")
    return pd.to_datetime(s, errors="coerce")


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


def _parse_ymd(value) -> date | None:
    from .freshness import parse_date_text

    return parse_date_text(value)


def _coverage_notes(df: pd.DataFrame, spec: DatasetSpec, params: dict) -> list[str]:
    """요청한 기간과 실제로 받은 데이터의 날짜 범위가 다르면 알린다 (허브 갱신 지연 등)."""
    if len(spec.date_params) < 2:
        return []
    date_col = next((c.name for c in spec.columns if c.dtype == "date" and c.name in df.columns), None)
    if date_col is None or not pd.api.types.is_datetime64_any_dtype(df[date_col]) or df[date_col].isna().all():
        return []
    first, last = df[date_col].min().date(), df[date_col].max().date()
    want_start, want_end = _parse_ymd(params.get(spec.date_params[0])), _parse_ymd(params.get(spec.date_params[1]))
    notes = [f"실제 데이터 기간: {first.isoformat()} ~ {last.isoformat()}"]
    if want_end and last < want_end:
        notes.append(
            f"⚠️ 요청한 끝 날짜({want_end.isoformat()})보다 데이터가 일찍 끝납니다. 허브 갱신이 늦어서일 수 있으니 답변에 실제 기간을 밝히세요."
        )
    if want_start and first > want_start:
        notes.append(f"요청한 시작 날짜({want_start.isoformat()})보다 데이터가 늦게 시작합니다.")
    return notes


# Open API 를 쓸 수 없는 이유 → 모델과 사용자에게 보여 줄 짧은 이름과 안내
API_UNAVAILABLE_LABELS = {
    "no_api_id": "API 고유 ID 미등록",
    "no_key": "프로젝트 키 없음",
    "file_only": "허브에서 파일로만 제공",
}
API_UNAVAILABLE_NOTES = {
    "no_api_id": (
        "Open API 는 쓰지 않았습니다: 이 데이터는 API 고유 ID 미등록입니다. source='api' 로 다시 불러도 결과가 같습니다. "
        "실제 값이 필요하면 사용자에게 제주데이터허브에서 원본 파일을 내려받아 data/raw 에 넣거나, "
        "데이터 상세 페이지의 API 요청 주소를 알려 달라고 안내하세요."
    ),
    "no_key": (
        "Open API 는 쓰지 않았습니다: 프로젝트 키 없음(.env 의 JEJUDATAHUB_PROJECT_KEY). source='api' 로 다시 불러도 결과가 같습니다. "
        "실제 값이 필요하면 사용자에게 프로젝트 키 설정이나 원본 파일(data/raw)을 안내하세요."
    ),
    "file_only": (
        "이 데이터는 허브에서 파일로만 제공됩니다. 실제 값이 필요하면 사용자에게 원본 파일을 내려받아 data/raw 에 넣어 달라고 안내하세요."
    ),
}


class DataStore:
    def __init__(
        self,
        registry: Registry | None = None,
        settings: Settings | None = None,
        client: DataHubClient | None = None,
        raw_dir: Path | str = RAW_DIR,
        samples_dir: Path | str = SAMPLES_DIR,
        today: Callable[[], date] = date.today,
    ):
        self.today = today
        self._latest_cache: dict[str, date | None] = {}
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
            "api": self.api_unavailable_reason(spec) is None,
            "api_id_set": bool(spec.api_id),
            "files": [str(p) for p in self.find_files(spec)],
            "sample": str(self.sample_path(spec)) if self.sample_path(spec) else None,
        }

    def api_unavailable_reason(self, spec: DatasetSpec) -> str | None:
        """Open API 를 쓸 수 없는 이유. 쓸 수 있으면 None."""
        if spec.access not in ("api", "api,file"):
            return "file_only"
        if not spec.api_id:
            return "no_api_id"
        if not self.client.configured:
            return "no_key"
        return None

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
                    "api_unavailable": self.api_unavailable_reason(spec),
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
        labels = {"api": "Open API", "file": "내려받은 파일", "sample": "합성 샘플"}
        errors: list[str] = []
        retry_worth = False  # 질의 변수만 고치면 되는 오류였는지
        last_kind = "other"
        skipped_api: str | None = None  # 자동 모드에서 Open API 를 건너뛴 이유
        for source in order:
            # 자동 모드에서는 준비되지 않은 출처(키·api_id 없음, 파일 없음)를 건너뛰고, 이유는 결과에 남긴다.
            if prefer == "auto" and source == "api":
                skipped_api = self.api_unavailable_reason(spec)
                if skipped_api:
                    continue
            if prefer == "auto" and source == "file" and not self.find_files(spec):
                continue
            try:
                if source == "api":
                    ds = self._load_api(spec, params, refresh, max_rows)
                elif source == "file":
                    ds = self._load_file(spec, max_rows)
                else:
                    ds = self._load_sample(spec, max_rows)
            except (DataHubError, FileNotFoundError) as e:
                errors.append(f"{labels[source]}: {e}")
                last_kind = getattr(e, "kind", "other")
                if last_kind in DataHubError.RETRYABLE_WITH_NEW_PARAMS:
                    retry_worth = True
                continue
            if errors:
                advice = (
                    "질의 변수를 고쳐 source='api' 로 다시 시도할 수 있습니다."
                    if retry_worth
                    else "방화벽·네트워크·인증 문제라 다시 시도해도 같은 결과입니다. 대체된 데이터로 진행하고 답변에 그 사실을 밝히세요."
                )
                ds.notes.insert(0, "⚠️ 앞선 출처를 쓰지 못해 대체했습니다 → " + " | ".join(errors) + f" ({advice})")
            if skipped_api and ds.is_synthetic:
                ds.notes.insert(0, API_UNAVAILABLE_NOTES[skipped_api])
            return ds
        raise DataHubError(
            f"'{spec.name}' 을(를) 불러올 수 없습니다. " + (" | ".join(errors) or "사용 가능한 출처가 없습니다."),
            kind=last_kind,
        )

    def _load_api(self, spec: DatasetSpec, params: dict | None, refresh: bool, max_rows: int | None) -> LoadedDataset:
        reason = self.api_unavailable_reason(spec)
        if reason:
            raise DataHubError(
                f"Open API 를 쓸 수 없습니다({API_UNAVAILABLE_LABELS[reason]}). 다시 시도해도 결과가 같으니 "
                "source='auto' 로 다른 출처를 쓰고, 실제 값이 필요하면 사용자에게 원본 파일(data/raw)을 안내하세요.",
                kind="config",
            )
        if spec.day_param:
            return self._load_api_by_day(spec, params, refresh, max_rows)
        today = self.today()
        merged = render_params({**(spec.api_params or {}), **(params or {})}, today=today)
        client = self.client.for_paging(spec.api_paging)
        res = client.fetch(spec.api_id, merged, refresh=refresh, max_rows=max_rows)
        pre_notes: list[str] = []
        if res.df.empty and len(spec.date_params) >= 2:
            latest = self.latest_date(spec, merged, client=client)
            user_gave_dates = any(k in (params or {}) for k in spec.date_params[:2])
            if latest is not None:
                pre_notes.append(f"이 데이터의 가장 최근 날짜는 {latest.isoformat()} 입니다 (오늘보다 {(today - latest).days}일 전).")
                if not user_gave_dates:
                    s_key, e_key = spec.date_params[:2]
                    start = latest - timedelta(days=13)
                    merged = {**merged, s_key: start.strftime("%Y%m%d"), e_key: latest.strftime("%Y%m%d")}
                    res = client.fetch(spec.api_id, merged, refresh=refresh, max_rows=max_rows)
                    pre_notes.append(
                        f"기본 기간에 데이터가 없어 가장 최근 2주({start.isoformat()} ~ {latest.isoformat()})로 다시 불러왔습니다."
                    )
            else:
                pre_notes.append("최근 3년 안에서 이 데이터를 찾지 못했습니다. 기간을 직접 지정해 보세요.")
        df = normalize_dataframe(res.df, spec)
        shown = merged
        notes = pre_notes + [f"API 호출 {res.pages}페이지, 수집 {len(df):,}건 / 전체 {res.total if res.total is not None else '알 수 없음'}건"]
        if res.from_cache:
            notes.append("디스크 캐시에서 읽음 (refresh=True 로 새로 수집 가능)")
        if shown:
            notes.append(f"질의 변수: {shown}")
        if df.empty:
            notes.append("⚠️ 이 조건에 해당하는 데이터가 0건입니다. 기간을 앞당기거나 넓혀 다시 요청하세요.")
        else:
            notes.extend(_coverage_notes(df, spec, merged))
        if res.truncated:
            notes.append(
                f"⚠️ 전체 {res.total:,}건 중 {len(df):,}건만 수집했습니다(페이지 한도). 기간을 나눠 요청하세요."
            )
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

    def _load_api_by_day(self, spec: DatasetSpec, params: dict | None, refresh: bool, max_rows: int | None) -> LoadedDataset:
        """하루만 받는 API(예: searchDate): 요청 기간을 하루씩 나눠 부르고 합친다."""
        from .freshness import fmt_date

        today = self.today()
        merged = render_params({**(spec.api_params or {}), **(params or {})}, today=today)
        s_key, e_key = (spec.date_params + ["startDate", "endDate"])[:2] if len(spec.date_params) >= 2 else ("startDate", "endDate")
        given = params or {}
        user_gave_dates = any(k in given for k in (s_key, e_key, spec.day_param))
        if spec.day_param in given and not (s_key in given or e_key in given):
            start = end = _parse_ymd(given[spec.day_param])
        else:
            start, end = _parse_ymd(merged.get(s_key)), _parse_ymd(merged.get(e_key))
            start, end = start or end, end or start
        if not start or not end:
            raise DataHubError(
                f"기간을 {s_key}·{e_key}=YYYYMMDD 로 주세요(하루만 보려면 {spec.day_param}=YYYYMMDD).", kind="params"
            )
        if start > end:
            start, end = end, start
        base = {k: v for k, v in merged.items() if k not in (s_key, e_key, spec.day_param)}
        client = self.client.for_paging(spec.api_paging)
        cap = int((spec.api_paging or {}).get("max_days", 400))
        notes: list[str] = []
        if (end - start).days + 1 > cap:
            asked = (end - start).days + 1
            start = end - timedelta(days=cap - 1)
            notes.append(
                f"⚠️ 요청 기간이 {asked}일이라 하루 1회 호출 한도({cap}일)에 맞춰 끝 날짜 기준 최근 {cap}일"
                f"({start.isoformat()} ~ {end.isoformat()})만 불러왔습니다. 더 필요하면 기간을 나눠 요청하세요."
            )

        def fetch_range(a: date, b: date) -> tuple[pd.DataFrame, int, int, bool]:
            frames, pages, cached, truncated = [], 0, 0, False
            d = a
            while d <= b:
                res = client.fetch(spec.api_id, {**base, spec.day_param: fmt_date(d, spec)}, refresh=refresh)
                pages += res.pages
                cached += int(res.from_cache)
                truncated = truncated or res.truncated
                if not res.df.empty:
                    frames.append(res.df)
                if max_rows and sum(len(f) for f in frames) >= max_rows:
                    break
                if not res.from_cache and client.sleep_between_pages:
                    time.sleep(client.sleep_between_pages)
                d += timedelta(days=1)
            df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
            if max_rows:
                df = df.head(max_rows)
            return df, pages, cached, truncated

        raw, pages, cached, truncated = fetch_range(start, end)
        if raw.empty:
            latest = self.latest_date(spec, base, client=client)
            if latest is not None:
                notes.append(f"이 데이터의 가장 최근 날짜는 {latest.isoformat()} 입니다 (오늘보다 {(today - latest).days}일 전).")
                if not user_gave_dates:
                    start, end = latest - timedelta(days=13), latest
                    raw, p2, c2, t2 = fetch_range(start, end)
                    pages, cached, truncated = pages + p2, cached + c2, truncated or t2
                    notes.append(f"기본 기간에 데이터가 없어 가장 최근 2주({start.isoformat()} ~ {end.isoformat()})로 다시 불러왔습니다.")
            else:
                notes.append("최근 1년 안에서 이 데이터를 찾지 못했습니다. 기간을 직접 지정해 보세요.")
        df = normalize_dataframe(raw, spec)
        days = (end - start).days + 1
        notes.append(
            f"하루씩 {days}일을 나눠 불러왔습니다({spec.day_param}): API 호출 {pages}페이지(캐시 {cached}일), 수집 {len(df):,}건"
        )
        shown = {**base, s_key: start.strftime("%Y%m%d"), e_key: end.strftime("%Y%m%d")}
        notes.append(f"질의 변수: {shown}")
        if df.empty:
            notes.append("⚠️ 이 조건에 해당하는 데이터가 0건입니다. 기간을 앞당기거나 넓혀 다시 요청하세요.")
        else:
            notes.extend(_coverage_notes(df, spec, shown))
        if truncated:
            notes.append("⚠️ 일부 날짜는 페이지 한도로 다 받지 못했습니다. 거르기 변수로 범위를 줄이세요.")
        url = f"{self.client.base_url}/{spec.api_id}/{{PROJECT_KEY}}?{spec.day_param}=<{start.strftime('%Y%m%d')}~{end.strftime('%Y%m%d')} 하루씩>"
        return LoadedDataset(
            id=spec.id,
            name=spec.name,
            df=df,
            provenance="api",
            source=url,
            fetched_at=datetime.now().isoformat(timespec="seconds"),
            is_synthetic=False,
            spec=spec,
            notes=notes,
        )

    def latest_date(self, spec: DatasetSpec, params: dict | None = None, client: DataHubClient | None = None) -> date | None:
        """기간 변수가 있는 API 데이터의 가장 최근 날짜 (한 번 찾으면 이 실행 동안 기억)."""
        from .freshness import find_latest_date

        skip = {*spec.date_params[:2], spec.day_param}
        base = {k: v for k, v in (params or {}).items() if k not in skip}
        key = spec.id + "|" + repr(sorted(base.items()))
        if key not in self._latest_cache:
            try:
                latest, _ = find_latest_date(client or self.client.for_paging(spec.api_paging), spec, base, today=self.today())
            except DataHubError:
                latest = None
            self._latest_cache[key] = latest
        return self._latest_cache[key]

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

    # ------------------------------------------------------------------ data/raw 임의 파일
    def list_raw_files(self) -> list[dict]:
        files = []
        if not self.raw_dir.exists():
            return files
        for p in sorted(self.raw_dir.rglob("*")):
            if p.is_file() and p.suffix.lower() in RAW_EXTENSIONS and not p.name.startswith("."):
                st = p.stat()
                files.append(
                    {
                        "name": p.relative_to(self.raw_dir).as_posix(),
                        "size_kb": round(st.st_size / 1024, 1),
                        "modified": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="minutes"),
                    }
                )
        return files

    def resolve_raw_path(self, name: str) -> Path:
        """data/raw 안에서 파일을 찾는다 (정확한 이름 → 확장자 생략 → 부분 일치 순)."""
        root = self.raw_dir.resolve()
        name = str(name).strip()
        if not name:
            raise FileNotFoundError("파일 이름이 비어 있습니다.")
        candidate = (root / name).resolve()
        if not candidate.is_relative_to(root):
            raise PermissionError("data/raw 폴더 밖의 파일은 읽을 수 없습니다.")
        if candidate.is_file():
            return candidate
        files = [root / f["name"] for f in self.list_raw_files()]
        wanted = Path(name).stem.lower()
        exact = [p for p in files if p.stem.lower() == wanted or p.name.lower() == name.lower()]
        if len(exact) == 1:
            return exact[0]
        partial = [p for p in files if wanted and wanted in p.name.lower()]
        if len(partial) == 1:
            return partial[0]
        if len(partial) > 1:
            raise FileNotFoundError(
                f"'{name}' 에 해당하는 파일이 여러 개입니다: {', '.join(p.name for p in partial[:10])}. 정확한 이름을 지정하세요."
            )
        available = ", ".join(f["name"] for f in self.list_raw_files()[:20]) or "(없음)"
        raise FileNotFoundError(f"data/raw 에 '{name}' 파일이 없습니다. 현재 파일: {available}")

    def load_file(
        self,
        name: str,
        sheet: str | None = None,
        table_name: str | None = None,
        max_rows: int | None = None,
    ) -> LoadedDataset:
        """사용자가 data/raw 에 넣은 임의의 CSV/TSV/TXT/XLSX 를 불러온다 (스키마 자동 추정)."""
        path = self.resolve_raw_path(name)
        notes: list[str] = []
        if path.suffix.lower() in (".xlsx", ".xlsm", ".xls"):
            sheets = pd.ExcelFile(path, engine="openpyxl").sheet_names
            use: str | int
            if sheet is None:
                use = 0
            elif sheet in sheets:
                use = sheet
            else:
                raise ValueError(f"시트 '{sheet}' 이(가) 없습니다. 시트 목록: {sheets}")
            df = read_table_file(path, sheet=use)
            if len(sheets) > 1:
                notes.append(f"시트 {len(sheets)}개 중 '{sheets[0] if use == 0 else use}' 사용 (다른 시트: {', '.join(x for x in sheets if x != use)[:200]})")
        else:
            df = read_table_file(path)
        df, type_notes = infer_types(df)
        if type_notes:
            notes.append("자동 형 변환: " + ", ".join(type_notes[:12]))
        if max_rows:
            df = df.head(max_rows)
        tid = sanitize_table_name(table_name or path.stem)
        spec = DatasetSpec(
            id=tid,
            name=path.name,
            access="file",
            provider="제주데이터허브 (사용자 다운로드 파일)",
            source_url="https://www.jejudatahub.net",
            file_glob=path.name,
            update="파일 수정 시각 기준",
            license="제주데이터허브 해당 데이터 상세 페이지의 이용조건을 따름 (출처 표시)",
            notes="사용자가 내려받아 data/raw 에 저장한 파일. 수집 기간·단위·주의사항은 허브 데이터 상세 페이지에서 확인한다.",
        )
        return LoadedDataset(
            id=tid,
            name=path.name,
            df=df,
            provenance="file",
            source=str(path),
            fetched_at=datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
            is_synthetic=False,
            spec=spec,
            notes=notes,
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
