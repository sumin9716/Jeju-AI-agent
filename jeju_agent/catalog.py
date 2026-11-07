"""제주데이터허브 데이터 목록(1,259종) 검색.

챌린지 참가자용으로 배포된 「제주데이터허브 데이터 목록_20261105.xlsx」를 CSV로 변환해
동봉했고(data/catalog/), 에이전트는 이 목록을 키워드·유사도로 검색해 질문에 맞는
데이터셋을 추천한다. 외부 네트워크 없이 동작한다.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

import pandas as pd

from .config import CATALOG_FILE

COLUMN_MAP = {
    "No.": "no",
    "카테고리": "category",
    "데이터명": "name",
    "데이터 타입": "data_type",
    "등록일": "registered_at",
    "수정일": "updated_at",
    "수집 기간": "period",
    "등록자": "registrant",
    "조회수": "views",
    "즐겨찾기수": "favorites",
}

# 도민의 일상 어휘 → 데이터명에 자주 쓰이는 표현. 검색어를 확장할 때 사용한다.
SYNONYMS: dict[str, list[str]] = {
    "사람": ["유동인구", "인구", "방문자", "입도객"],
    "유동인구": ["유동인구", "인구", "방문"],
    "인구": ["인구", "유동인구"],
    "관광객": ["관광객", "입도객", "관광", "유입자"],
    "여행": ["관광", "여행", "관광지"],
    "손님": ["관광객", "유동인구", "카드"],
    "매출": ["카드", "소비", "매출", "이용금액"],
    "소비": ["카드", "소비", "매출"],
    "카드": ["카드"],
    "창업": ["카드", "업종", "매장", "상권", "창업", "음식점"],
    "상권": ["상권", "카드", "유동인구", "업종"],
    "가게": ["업종", "매장", "음식점", "소매"],
    "식당": ["음식점", "외식", "식음료"],
    "음식점": ["음식점", "외식"],
    "카페": ["음식점", "외식", "휴게음식점"],
    "버스": ["버스", "정류소", "노선"],
    "대중교통": ["버스", "정류소", "노선", "교통"],
    "교통": ["교통", "버스", "교통사고", "차량"],
    "정류장": ["정류소", "버스"],
    "날씨": ["기상", "날씨", "기온", "강수"],
    "기상": ["기상", "날씨"],
    "비": ["강수", "기상"],
    "태풍": ["태풍", "기상", "재난"],
    "전기차": ["전기자동차", "충전"],
    "충전소": ["충전", "전기자동차"],
    "농산물": ["농산물", "농축수산", "품종", "거래"],
    "가격": ["가격", "거래", "판매"],
    "감귤": ["감귤", "과수", "농산물", "품종"],
    "농사": ["농축수산", "재배", "생산"],
    "어업": ["수산", "어업", "농축수산"],
    "축산": ["축산", "가축", "농축수산"],
    "병원": ["병·의원", "병원", "의원", "보건"],
    "약국": ["약국", "보건"],
    "복지": ["복지", "보건"],
    "노인": ["노인", "고령", "복지"],
    "아이": ["아동", "어린이", "보육", "교육"],
    "학교": ["학교", "교육", "학생"],
    "쓰레기": ["폐기물", "클린하우스", "재활용"],
    "환경": ["환경", "대기", "미세먼지", "폐기물"],
    "미세먼지": ["미세먼지", "대기"],
    "바다": ["해수욕장", "해안", "수산"],
    "해수욕장": ["해수욕장"],
    "오름": ["오름"],
    "올레": ["올레길"],
    "숙박": ["숙박", "호텔", "민박", "펜션"],
    "호텔": ["숙박", "호텔"],
    "와이파이": ["와이파이", "wifi"],
    "주차": ["주차", "주차장"],
    "집": ["주택", "부동산", "인구"],
    "부동산": ["부동산", "주택", "지가"],
    "일자리": ["고용", "실업", "취업", "일자리"],
    "외국인": ["외국인", "국가별"],
    "중국": ["중국인", "외국인"],
    "사고": ["교통사고", "안전", "재난"],
    "안전": ["안전", "재난", "사고"],
    "읍면동": ["읍면동"],
    "동네": ["읍면동", "지역"],
    "지역": ["지역", "읍면동", "시도별"],
}

_TOKEN_RE = re.compile(r"[가-힣a-zA-Z0-9]+")


@dataclass
class CatalogEntry:
    no: int
    category: str
    name: str
    data_type: str
    registered_at: str
    updated_at: str
    period: str
    registrant: str
    views: int
    favorites: int
    score: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _bigrams(text: str) -> set[str]:
    compact = re.sub(r"[^가-힣a-z0-9]", "", _normalize(text))
    if len(compact) < 2:
        return {compact} if compact else set()
    return {compact[i : i + 2] for i in range(len(compact) - 1)}


def expand_query(query: str) -> list[str]:
    """검색어를 토큰화하고 동의어를 덧붙인다. 원래 토큰이 앞에 온다."""
    tokens = [t.lower() for t in _TOKEN_RE.findall(query or "")]
    expanded: list[str] = []
    for tok in tokens:
        if tok not in expanded:
            expanded.append(tok)
        # 조사 제거 흉내: "유동인구가" → "유동인구" 등 끝 1글자 제거본도 후보에 넣는다.
        if len(tok) >= 3 and re.fullmatch(r"[가-힣]+", tok):
            stem = tok[:-1]
            if stem not in expanded:
                expanded.append(stem)
    for tok in list(expanded):
        for syn in SYNONYMS.get(tok, []):
            if syn.lower() not in expanded:
                expanded.append(syn.lower())
    return expanded


class Catalog:
    """데이터 목록 검색기."""

    def __init__(self, path: Path | str = CATALOG_FILE):
        self.path = Path(path)
        df = pd.read_csv(self.path, dtype=str, keep_default_na=False, encoding="utf-8")
        df = df.rename(columns=COLUMN_MAP)
        for col in ("views", "favorites", "no"):
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
        for col in ("category", "name", "data_type", "registered_at", "updated_at", "period", "registrant"):
            df[col] = df[col].astype(str).str.strip()
        self.df = df.reset_index(drop=True)
        self._name_bigrams = [_bigrams(n) for n in self.df["name"]]
        self._search_text = [
            _normalize(f"{r.name} {r.category} {r.registrant} {r.period}") for r in self.df.itertuples()
        ]

    # ------------------------------------------------------------------ 기본 조회
    def __len__(self) -> int:
        return len(self.df)

    def categories(self) -> list[str]:
        return sorted(self.df["category"].unique().tolist())

    def get(self, no: int) -> CatalogEntry | None:
        rows = self.df[self.df["no"] == int(no)]
        if rows.empty:
            return None
        return self._to_entry(rows.iloc[0])

    def _to_entry(self, row: pd.Series, score: float = 0.0) -> CatalogEntry:
        return CatalogEntry(
            no=int(row["no"]),
            category=row["category"],
            name=row["name"],
            data_type=row["data_type"],
            registered_at=row["registered_at"],
            updated_at=row["updated_at"],
            period=row["period"],
            registrant=row["registrant"],
            views=int(row["views"]),
            favorites=int(row["favorites"]),
            score=round(float(score), 3),
        )

    # ------------------------------------------------------------------ 검색
    def search(
        self,
        query: str,
        top_k: int = 10,
        category: str | None = None,
        data_type: str | None = None,
        only_api: bool = False,
    ) -> list[CatalogEntry]:
        """키워드(동의어 확장) + 글자 바이그램 유사도 + 인기도로 점수를 매겨 상위 결과를 돌려준다."""
        terms = expand_query(query)
        q_bigrams = _bigrams(query)
        results: list[tuple[float, int]] = []
        for idx, row in enumerate(self.df.itertuples()):
            if category and category not in row.category:
                continue
            if data_type and data_type.lower() not in row.data_type.lower():
                continue
            if only_api and "api" not in row.data_type.lower():
                continue
            text = self._search_text[idx]
            name = _normalize(row.name)
            score = 0.0
            for rank, term in enumerate(terms):
                weight = 1.0 if rank < 3 else 0.6  # 원 검색어가 동의어보다 중요
                if term in name:
                    score += 3.0 * weight
                elif term in text:
                    score += 1.0 * weight
            if q_bigrams:
                overlap = len(q_bigrams & self._name_bigrams[idx]) / len(q_bigrams)
                score += 4.0 * overlap
            if score <= 0:
                continue
            # 인기도(즐겨찾기·조회수)는 소폭 가산: 같은 점수면 많이 쓰인 데이터가 앞에 온다.
            score += 0.15 * math.log1p(row.favorites) + 0.05 * math.log1p(row.views)
            if "api" in row.data_type.lower():
                score += 0.3  # 자동 수집이 가능한 API 데이터 우대
            results.append((score, idx))
        results.sort(key=lambda t: (-t[0], t[1]))
        return [self._to_entry(self.df.iloc[idx], score) for score, idx in results[:top_k]]

    def to_markdown(self, entries: Iterable[CatalogEntry]) -> str:
        lines = ["| No. | 데이터명 | 카테고리 | 타입 | 수집 기간 | 즐겨찾기 |", "|---|---|---|---|---|---|"]
        for e in entries:
            lines.append(f"| {e.no} | {e.name} | {e.category} | {e.data_type} | {e.period} | {e.favorites} |")
        return "\n".join(lines)
