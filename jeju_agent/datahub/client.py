"""제주데이터허브 Open API 클라이언트.

호출 형식 (허브 안내 기준):
    GET https://open.jejudatahub.net/api/proxy/{API 고유 ID}/{프로젝트 키}?변수=값&number=1&limit=100

- API 고유 ID : 데이터 상세 페이지의 "API" 탭에 표시
- 프로젝트 키 : 허브 로그인 → 마이페이지 → 프로젝트 생성 후 발급 (.env JEJUDATAHUB_PROJECT_KEY)
- 응답        : JSON. 보통 {"status": ..., "totCnt": N, "data": [...]} 형태이나 서비스별로
                키 이름이 다를 수 있어 리스트 필드를 관대하게 찾는다.

네트워크가 없거나 키가 없으면 DataHubError 를 던지고, 상위 로더가 파일/샘플로 대체한다.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

import pandas as pd
import requests

from ..config import CACHE_DIR

LIST_KEYS = ("data", "items", "list", "rows", "result", "results", "records")
TOTAL_KEYS = ("totCnt", "totalCount", "totalCnt", "total", "count", "totalRecords")
ERROR_STATUS = {"fail", "failed", "error", "err", "unauthorized", "forbidden", "invalid"}


class DataHubError(RuntimeError):
    """Open API 호출 실패 (설정 누락, 네트워크, 인증, 응답 형식 오류)."""


@dataclass
class FetchResult:
    df: pd.DataFrame
    total: int | None
    pages: int
    from_cache: bool
    url: str


def _render_param(value: Any, today: date | None = None) -> Any:
    """'{{today}}', '{{today-90d}}', '{{today-1y}}' 같은 템플릿을 YYYYMMDD 로 치환한다."""
    if not isinstance(value, str):
        return value
    today = today or date.today()

    def repl(m: re.Match) -> str:
        expr = m.group(1).replace(" ", "")
        d = today
        mm = re.fullmatch(r"today([+-]\d+)([dmy])", expr)
        if mm:
            n = int(mm.group(1))
            unit = mm.group(2)
            if unit == "d":
                d = today + timedelta(days=n)
            elif unit == "m":
                d = today + timedelta(days=30 * n)
            else:
                d = today.replace(year=today.year + n)
        elif expr != "today":
            return m.group(0)
        return d.strftime("%Y%m%d")

    return re.sub(r"\{\{\s*([^}]+?)\s*\}\}", repl, value)


def render_params(params: dict | None, today: date | None = None) -> dict:
    return {k: _render_param(v, today) for k, v in (params or {}).items()}


class DataHubClient:
    def __init__(
        self,
        project_key: str,
        base_url: str = "https://open.jejudatahub.net/api/proxy",
        cache_dir: Path | str = CACHE_DIR,
        timeout: float = 30.0,
        page_param: str = "number",
        size_param: str = "limit",
        page_size: int = 100,
        max_pages: int = 300,
        sleep_between_pages: float = 0.1,
        session: requests.Session | None = None,
    ):
        self.project_key = (project_key or "").strip()
        self.base_url = base_url.rstrip("/")
        self.cache_dir = Path(cache_dir)
        self.timeout = timeout
        self.page_param = page_param
        self.size_param = size_param
        self.page_size = page_size
        self.max_pages = max_pages
        self.sleep_between_pages = sleep_between_pages
        self.session = session or requests.Session()
        if session is None:
            from .. import __version__

            self.session.headers.update(
                {
                    "User-Agent": f"JejuDataNavigator/{__version__} (+https://github.com/sumin9716/Jeju-AI-agent)",
                    "Accept": "application/json",
                }
            )

    # ------------------------------------------------------------------ 준비 상태
    @property
    def configured(self) -> bool:
        return bool(self.project_key)

    def _mask(self, text: str) -> str:
        """오류 메시지·로그에 프로젝트 키가 남지 않도록 가린다."""
        return text.replace(self.project_key, "{PROJECT_KEY}") if self.project_key else text

    def build_url(self, api_id: str) -> str:
        if not self.configured:
            raise DataHubError("JEJUDATAHUB_PROJECT_KEY 가 설정되지 않았습니다 (.env 참고).")
        if not api_id:
            raise DataHubError("이 데이터셋의 api_id 가 레지스트리에 비어 있습니다 (registry.yaml 참고).")
        return f"{self.base_url}/{api_id}/{self.project_key}"

    # ------------------------------------------------------------------ 응답 파싱
    @staticmethod
    def parse_payload(payload: Any) -> tuple[list[dict], int | None]:
        """응답 JSON 에서 (행 목록, 전체 건수) 를 찾는다. 모르는 형식이면 DataHubError."""
        if isinstance(payload, list):
            return [r for r in payload if isinstance(r, dict)], None
        if not isinstance(payload, dict):
            raise DataHubError(f"알 수 없는 응답 형식: {type(payload).__name__}")

        status = payload.get("status")
        status_text = ""
        if isinstance(status, dict):
            status_text = str(status.get("code", "")) + " " + str(status.get("message", ""))
        elif status is not None:
            status_text = str(status)
        if status_text and status_text.strip().lower().split(" ")[0] in ERROR_STATUS:
            raise DataHubError(f"API 오류 응답: {status_text.strip()} / {payload.get('message', '')}".strip())

        rows: list[dict] | None = None
        for key in LIST_KEYS:
            val = payload.get(key)
            if isinstance(val, list):
                rows = [r for r in val if isinstance(r, dict)]
                break
            if isinstance(val, dict):  # {"data": {"list": [...]}} 중첩 형식
                for k2 in LIST_KEYS:
                    if isinstance(val.get(k2), list):
                        rows = [r for r in val[k2] if isinstance(r, dict)]
                        break
                if rows is not None:
                    break
        if rows is None:
            # 마지막 수단: 값 중 첫 번째 리스트
            for val in payload.values():
                if isinstance(val, list) and (not val or isinstance(val[0], dict)):
                    rows = [r for r in val if isinstance(r, dict)]
                    break
        if rows is None:
            msg = payload.get("message") or payload.get("msg") or payload.get("error")
            raise DataHubError(f"응답에서 데이터 목록을 찾지 못했습니다: {msg or list(payload.keys())}")

        total: int | None = None
        nested = [v for v in payload.values() if isinstance(v, dict)]
        for key in TOTAL_KEYS:
            val = payload.get(key)
            for inner in nested:
                if val is None:
                    val = inner.get(key)
            if val is not None:
                try:
                    total = int(val)
                    break
                except (TypeError, ValueError):
                    continue
        return rows, total

    # ------------------------------------------------------------------ 호출
    def _cache_path(self, api_id: str, params: dict) -> Path:
        key = json.dumps({"api_id": api_id, "params": params}, sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
        return self.cache_dir / f"{api_id[:12]}_{digest}.json"

    def iter_pages(self, api_id: str, params: dict | None = None) -> Iterator[tuple[int, list[dict], int | None]]:
        url = self.build_url(api_id)
        params = dict(params or {})
        page = 1
        fetched = 0
        while page <= self.max_pages:
            q = dict(params)
            q[self.page_param] = page
            q[self.size_param] = self.page_size
            try:
                resp = self.session.get(url, params=q, timeout=self.timeout)
            except requests.RequestException as e:  # 네트워크 단절, DNS 실패 등
                raise DataHubError(f"네트워크 오류로 제주데이터허브에 연결하지 못했습니다: {self._mask(str(e))}") from e
            if resp.status_code in (401, 403):
                raise DataHubError(f"인증 실패({resp.status_code}): 프로젝트 키 또는 API 신청 상태를 확인하세요.")
            if resp.status_code == 404:
                raise DataHubError("API 주소를 찾을 수 없습니다(404): api_id 를 확인하세요.")
            if resp.status_code >= 400:
                raise DataHubError(f"HTTP {resp.status_code}: {self._mask(resp.text[:300])}")
            try:
                payload = resp.json()
            except ValueError as e:
                snippet = re.sub(r"<[^>]+>", " ", resp.text[:600])
                snippet = re.sub(r"\s+", " ", snippet).strip()[:300]
                raise DataHubError(f"JSON 이 아닌 응답: {self._mask(snippet)}") from e
            rows, total = self.parse_payload(payload)
            yield page, rows, total
            fetched += len(rows)
            if not rows or len(rows) < self.page_size:
                break
            if total is not None and fetched >= total:
                break
            page += 1
            if self.sleep_between_pages:
                time.sleep(self.sleep_between_pages)

    def fetch(
        self,
        api_id: str,
        params: dict | None = None,
        use_cache: bool = True,
        refresh: bool = False,
        max_rows: int | None = None,
    ) -> FetchResult:
        """페이지를 끝까지(또는 max_rows 까지) 읽어 DataFrame 으로 돌려준다. 결과는 디스크에 캐시한다."""
        params = render_params(params)
        cache_path = self._cache_path(api_id, params)
        url = self.build_url(api_id)
        if use_cache and not refresh and cache_path.exists():
            with open(cache_path, encoding="utf-8") as f:
                cached = json.load(f)
            df = pd.DataFrame(cached["rows"])
            return FetchResult(df=df, total=cached.get("total"), pages=cached.get("pages", 0), from_cache=True, url=url)

        all_rows: list[dict] = []
        total: int | None = None
        pages = 0
        for page, rows, page_total in self.iter_pages(api_id, params):
            pages = page
            total = page_total if page_total is not None else total
            all_rows.extend(rows)
            if max_rows and len(all_rows) >= max_rows:
                all_rows = all_rows[:max_rows]
                break
        df = pd.DataFrame(all_rows)
        if use_cache:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "api_id": api_id,
                        "params": params,
                        "fetched_at": datetime.now().isoformat(timespec="seconds"),
                        "total": total,
                        "pages": pages,
                        "rows": all_rows,
                    },
                    f,
                    ensure_ascii=False,
                )
        return FetchResult(df=df, total=total, pages=pages, from_cache=False, url=url)

    def test_connection(self, api_id: str, params: dict | None = None) -> dict:
        """첫 페이지만 호출해 연결·인증·응답 형식을 점검한다."""
        gen = self.iter_pages(api_id, render_params(params))
        page, rows, total = next(gen)
        return {"ok": True, "rows_in_first_page": len(rows), "total": total, "columns": list(rows[0].keys()) if rows else []}
