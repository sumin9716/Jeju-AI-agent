"""Claude 에 전달하는 도구 정의(JSON Schema). 순서를 바꾸면 프롬프트 캐시가 무효화되므로 고정한다."""

from __future__ import annotations

TOOLS: list[dict] = [
    {
        "name": "search_catalog",
        "description": (
            "제주데이터허브 데이터 목록(1,259종)에서 질문과 관련된 데이터셋을 찾는다. "
            "데이터명·카테고리 키워드와 동의어, 인기도로 점수를 매긴다. 결과에는 지금 바로 불러올 수 있는 "
            "데이터(load_dataset 가능)가 표시된다. 어떤 데이터를 써야 할지 모를 때 먼저 호출한다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "검색어 (예: '읍면동 유동인구', '버스 정류소 이용', '감귤 가격')"},
                "top_k": {"type": "integer", "description": "최대 결과 수 (기본 8)", "minimum": 1, "maximum": 30},
                "only_api": {"type": "boolean", "description": "Open API 로 자동 수집 가능한 데이터만"},
                "category": {
                    "type": "string",
                    "description": "카테고리 필터: '관광, 여행' | '보건, 복지' | '교육, 행정' | '소비, 경제' | '환경, 재난' | '교통, 안전' | '농축수산' | '인구, 생활'",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_datasets",
        "description": (
            "지금 바로 불러올 수 있는 등록 데이터셋 목록과 각 데이터의 출처 상태(Open API 준비/내려받은 파일/합성 샘플), "
            "그리고 현재 세션에 이미 올라온 표 목록을 보여준다."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "load_dataset",
        "description": (
            "등록 데이터셋을 세션 표(table)로 불러온다. 출처는 자동(auto)이면 Open API → data/raw 파일 → 합성 샘플 순으로 시도한다. "
            "불러온 뒤 컬럼 구조·결측·예시값 요약을 돌려주므로 분석 코드를 쓰기 전에 반드시 확인한다. "
            "표 이름은 데이터셋 id 와 같다 (예: floating_population_emd)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "dataset": {"type": "string", "description": "데이터셋 id, 카탈로그 No., 또는 데이터명 일부 (예: 'floating_population_emd', '753', '유동인구')"},
                "source": {"type": "string", "enum": ["auto", "api", "file", "sample"], "description": "출처 선택 (기본 auto)"},
                "params": {
                    "type": "object",
                    "description": "Open API 질의 변수 (예: {\"startDate\": \"20240101\", \"endDate\": \"20241231\"}). 파일/샘플에는 무시됨.",
                    "additionalProperties": True,
                },
                "max_rows": {"type": "integer", "description": "최대 행 수 제한 (큰 데이터 시험용)", "minimum": 1},
            },
            "required": ["dataset"],
            "additionalProperties": False,
        },
    },
    {
        "name": "load_file",
        "description": (
            "사용자가 제주데이터허브(또는 제주빅데이터센터)에서 내려받아 data/raw 폴더에 넣은 CSV/TSV/TXT/XLSX 파일을 세션 표로 불러온다. "
            "레지스트리에 없는 데이터(목록의 File 유형 등)는 이 도구로 분석한다. 파일 이름은 일부만 적어도 찾으며, "
            "숫자(쉼표 포함)·날짜 컬럼을 자동 추정하고 구조 요약을 돌려준다. data/raw 의 파일 목록은 list_datasets 로 볼 수 있다. "
            "파일이 없으면 사용자에게 허브에서 내려받아 data/raw 에 저장하도록 안내한다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file": {"type": "string", "description": "파일 이름 또는 일부 (예: '음식점', '제주특별자치도_클린하우스현황.csv')"},
                "sheet": {"type": "string", "description": "엑셀 시트 이름 (선택, 기본 첫 시트)"},
                "table_name": {"type": "string", "description": "세션에서 쓸 표 이름 (선택, 짧은 영문 권장. 기본은 파일 이름에서 생성)"},
                "max_rows": {"type": "integer", "description": "최대 행 수 제한 (선택)", "minimum": 1},
            },
            "required": ["file"],
            "additionalProperties": False,
        },
    },
    {
        "name": "run_python",
        "description": (
            "pandas 분석 코드를 격리 환경에서 실행한다. 세션의 표는 같은 이름의 변수(DataFrame)와 `tables` 딕셔너리로 접근한다. "
            "규칙: (1) 최종 표/값은 `result` 변수에 담는다 (DataFrame/Series/dict/숫자). "
            "(2) 다음 단계나 차트에서 쓸 표는 `save_table('이름', df)` 로 저장한다. "
            "(3) 중간 확인은 print() 또는 show(df). (4) plotly 그림은 `fig` 변수에 담으면 자동 저장된다. "
            "(5) 네트워크·파일시스템·os/sys 모듈은 사용할 수 없다. (6) pd, np, px, go, plt 는 이미 import 되어 있다. "
            "큰 데이터는 먼저 groupby 로 집계한 뒤 다룬다. 날짜 컬럼은 datetime 이므로 .dt 접근자를 쓴다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "purpose": {"type": "string", "description": "이 코드가 무엇을 계산하는지 한 문장 (사용자 화면에 표시됨)"},
                "code": {"type": "string", "description": "실행할 Python 코드"},
            },
            "required": ["purpose", "code"],
            "additionalProperties": False,
        },
    },
    {
        "name": "make_chart",
        "description": (
            "세션의 표로 보기 좋은 차트를 만든다(plotly, 일관된 디자인). 집계(agg)와 상위 N 선택을 지원하므로 "
            "원본 표를 그대로 넣어도 된다. 생성된 차트 id 는 보고서(write_report)의 chart_ids 에 쓸 수 있다. "
            "비교·추세·구성·분포·지도 중 데이터의 목적에 맞는 유형을 고른다. 이중 축 차트는 지원하지 않는다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "table": {"type": "string", "description": "표 이름 (데이터셋 id, save_table 이름, result_N)"},
                "chart_type": {
                    "type": "string",
                    "enum": ["line", "bar", "barh", "stacked_bar", "area", "scatter", "pie", "histogram", "box", "heatmap", "map"],
                    "description": "line=추세, bar/barh=비교, stacked_bar=구성 비교, area=누적 추세, scatter=관계, pie=구성(항목 6개 이하), histogram=분포, box=분포 비교, heatmap=x×y 격자 강도, map=위도/경도 점",
                },
                "x": {"type": "string", "description": "x축 컬럼 (map 은 경도 컬럼, heatmap 은 가로 범주)"},
                "y": {"type": "string", "description": "y축/값 컬럼 (map 은 위도 컬럼, heatmap 은 세로 범주, pie 는 값, histogram 은 생략 가능)"},
                "value": {"type": "string", "description": "heatmap 의 셀 값 컬럼, map 의 점 크기 컬럼 (선택)"},
                "color": {"type": "string", "description": "계열(색) 구분 컬럼 (선택). line/bar 에서 여러 계열을 그릴 때 사용"},
                "agg": {"type": "string", "enum": ["sum", "mean", "median", "count", "max", "min"], "description": "x(및 color)별 집계 방법 (선택, 기본은 집계 없음)"},
                "top_n": {"type": "integer", "description": "값 기준 상위 N개만 표시 (선택)", "minimum": 1, "maximum": 50},
                "sort": {"type": "string", "enum": ["none", "asc", "desc"], "description": "x 범주를 값으로 정렬 (선택, 기본 none)"},
                "title": {"type": "string", "description": "차트 제목 (사용자가 바로 이해할 한국어 문장)"},
                "x_label": {"type": "string", "description": "x축 이름 (선택)"},
                "y_label": {"type": "string", "description": "y축 이름, 단위 포함 (선택)"},
                "filter": {"type": "string", "description": "pandas query 식 필터 (선택, 예: \"읍면동 == '애월읍'\")"},
            },
            "required": ["table", "chart_type", "x", "title"],
            "additionalProperties": False,
        },
    },
    {
        "name": "write_report",
        "description": (
            "분석 결과를 사용자에게 줄 보고서 파일(Markdown + HTML)로 저장한다. 데이터 출처·수집 시점·이용조건, 실행한 코드(재현용), "
            "샘플 데이터 여부와 한계가 자동으로 덧붙는다. 분석이 여러 단계이거나 사용자가 보고서/정리를 원할 때 호출한다. "
            "본문은 비전문가가 읽을 쉬운 한국어로 쓴다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "보고서 제목"},
                "summary": {"type": "string", "description": "핵심 결론 요약 (3~5문장, 숫자 근거 포함)"},
                "sections": {
                    "type": "array",
                    "description": "본문 절 목록",
                    "items": {
                        "type": "object",
                        "properties": {
                            "heading": {"type": "string"},
                            "body": {"type": "string", "description": "Markdown 본문 (표 삽입 가능)"},
                            "chart_ids": {"type": "array", "items": {"type": "string"}, "description": "이 절 뒤에 넣을 차트 id 목록"},
                        },
                        "required": ["heading", "body"],
                        "additionalProperties": False,
                    },
                },
                "conclusions": {"type": "array", "items": {"type": "string"}, "description": "결론 bullet"},
                "limitations": {"type": "array", "items": {"type": "string"}, "description": "한계·주의사항 bullet (데이터 기간, 추정치, 샘플 여부 등)"},
                "recommendations": {"type": "array", "items": {"type": "string"}, "description": "제안·다음 단계 bullet (선택)"},
            },
            "required": ["title", "summary", "sections", "conclusions", "limitations"],
            "additionalProperties": False,
        },
    },
]

TOOL_NAMES = [t["name"] for t in TOOLS]
