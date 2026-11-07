# 🍊 제주 데이터 길잡이 (Jeju Data Navigator)

[![CI](https://github.com/sumin9716/Jeju-AI-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/sumin9716/Jeju-AI-agent/actions/workflows/ci.yml)

> **「제주 AI·빅데이터 활용 AI Agent 개발 챌린지」 출품작** — 제주데이터허브 공개 데이터를 **자연어 질문 한 번**으로 탐색 → 수집 → 전처리 → 분석 → 시각화 → 보고서까지 자동 수행하는 AI Agent. IT 비전문가인 도민이 제주빅데이터센터 PC에서 바로 실행해 쓸 수 있도록 설계했습니다.

```
🧑 "애월읍은 주말과 평일 중 언제 방문자가 더 많아? 연령대별로도 보여줘"
🍊 ① 데이터 목록 1,259종 중 「읍면동 단위 내국인 유동인구」(No.753) 선택
   ② Open API / 파일 / 샘플 중 가능한 출처로 적재 → 컬럼·결측·기간 확인
   ③ pandas 코드로 주말·평일 × 연령대 집계 (격리 실행)
   ④ 차트 생성 → ⑤ 결론·근거·출처·한계를 쉬운 말로 답변 → ⑥ 보고서 HTML/MD 저장 (재현 코드 포함)
```

## 주요 기능

| 기능 | 설명 |
|---|---|
| 🔎 데이터 탐색 | 챌린지 제공 「제주데이터허브 데이터 목록」(1,259종)을 동의어·유사도·인기도로 검색해 질문에 맞는 데이터를 추천 |
| 📥 데이터 수집 | 제주데이터허브 Open API(`/api/proxy/{API ID}/{프로젝트 키}`) 자동 페이지 수집·캐시, 허브에서 내려받은 **임의의 CSV/XLSX**(`data/raw`) 자동 인식·형 추정, 키가 없을 때는 합성 샘플로 동작 |
| 🧮 자율 분석 | Claude 가 pandas 코드를 작성해 **격리된 하위 프로세스**(모듈 허용목록·시간·메모리 제한)에서 실행. 오류는 모델이 읽고 스스로 수정 |
| 📊 시각화 | 추세·비교·구성·분포·격자·지도 차트를 일관된 색 체계(색각 이상 안전 팔레트)로 생성 |
| 📝 보고서 | 결론·근거·한계 + **데이터 출처/이용조건 표 + 실행 코드 전체 + 재현 스크립트**를 자동 첨부한 Markdown/HTML |
| 🛡️ 신뢰성 | 모든 수치는 도구 실행 결과에서만 인용, 샘플 데이터 사용 시 경고 강제, 턴별 실행 기록(JSON) 저장 |
| 💬 도민 친화 UI | Streamlit 채팅 화면, 예시 질문 버튼, 분석 과정 펼쳐보기, 보고서·재현 스크립트 내려받기 |

## 빠른 시작

### 1) 요구 사항
- Windows 10/11, macOS, Linux · **Python 3.10 이상** (검증: 3.13)
- 인터넷 연결 (Claude API 호출, 제주데이터허브 Open API 수집)
- 센터 운영 PC 최소사양(i7-8700 / GTX 1060 / RAM 32GB)에서 충분히 동작합니다. GPU 는 사용하지 않습니다.

### 2) 설치
**가장 쉬운 방법 (센터 PC·Windows)**: 저장소를 내려받아 압축을 푼 뒤 `setup.bat` 을 더블클릭합니다. 가상환경 생성 → 패키지 설치 → `.env` 생성 → 환경 점검까지 자동으로 진행됩니다. 이후 실행은 `run.bat` 더블클릭 (바탕화면 바로가기 권장). macOS·Linux 는 `./setup.sh` 와 `./run.sh`.

수동 설치:
```bash
git clone https://github.com/sumin9716/Jeju-AI-agent.git
cd Jeju-AI-agent
python -m venv .venv
# Windows: .venv\Scripts\activate   /  macOS·Linux: source .venv/bin/activate
pip install -r requirements.txt
```

### 3) 환경변수
```bash
cp .env.example .env        # Windows: copy .env.example .env
```
`.env` 를 열어 `ANTHROPIC_API_KEY` 를 채웁니다. 제주데이터허브 Open API 로 실제 데이터를 수집하려면 `JEJUDATAHUB_PROJECT_KEY` 도 넣고, `jeju_agent/datahub/registry.yaml` 의 각 데이터셋 `api_id` 를 허브 상세 페이지의 API 탭에서 확인해 채웁니다 (자세한 방법: [docs/DATA_SPEC.md](docs/DATA_SPEC.md)).

### 4) 점검 후 실행
```bash
python -m jeju_agent.cli doctor      # 환경 점검 (패키지·키·샌드박스·API 호출)
streamlit run app.py                 # 웹 화면 (브라우저가 자동으로 열립니다)
```
명령줄에서 바로 묻기:
```bash
python -m jeju_agent.cli ask "비 오는 날에는 버스 이용객이 얼마나 줄어?"
```

> 키가 없어도 `data/samples/` 의 **합성 샘플 데이터**로 전체 흐름을 시연할 수 있습니다(답변과 보고서에 "합성 샘플" 경고가 붙습니다). 샘플은 `python -m jeju_agent.cli samples` 로 다시 만들 수 있습니다.

## 데이터 출처 설정 (세 가지 방식)

| 방식 | 설정 | 특징 |
|---|---|---|
| **Open API** (권장) | `.env` 의 `JEJUDATAHUB_PROJECT_KEY` + `registry.yaml` 의 `api_id` | 일 단위 자동 갱신 데이터를 그때그때 수집, 디스크 캐시 |
| **내려받은 파일** | 허브에서 CSV/XLSX 를 받아 `data/raw/` 에 저장 | 레지스트리의 `file_glob` 패턴(예: `*유동인구*.csv`)과 맞으면 등록 데이터로 자동 인식. 그 밖의 **어떤 파일이든** `load_file` 도구로 불러와 분석(숫자·날짜 자동 추정, 제목 행 건너뛰기, 엑셀 시트 선택) |
| **합성 샘플** | 기본 제공 | 실제 통계가 아님. 구조 확인·시연·테스트용 |

출처 우선순위는 `API → 파일 → 샘플` 이며, 답변·보고서마다 어떤 출처를 썼는지 표시됩니다. 현재 상태는 `python -m jeju_agent.cli data status` 로 확인합니다.

**Open API 연결 점검**: 아래 명령은 `api_id` 가 있는 등록 데이터셋마다 첫 몇 건만 호출해 성공 여부, 전체 건수, 응답 컬럼, 레지스트리에 없는 컬럼, 예시 행을 보고서로 출력하고 `outputs/checks/api_check_*.json` 에 저장합니다. 이 보고서로 `registry.yaml` 의 `api_params`·`rename` 을 실제 응답에 맞춥니다.
```bash
python -m jeju_agent.cli data check-api
python -m jeju_agent.cli data check-api --dataset floating_population_emd --param startDate=20240101 --param endDate=20240107
python -m jeju_agent.cli data check-api --deep    # 가장 최근 데이터 날짜, 페이지 넘김, 쪽당 최대 건수, 필터 변수 동작까지 확인
```
제주데이터허브는 웹 방화벽이 있어 클라우드 서버에서의 호출은 차단될 수 있습니다. 센터 PC 나 개인 PC 에서 실행하세요.

## 구조

```
app.py                      Streamlit 웹 화면
jeju_agent/
  agent.py                  에이전트 루프 (Claude 스트리밍 호출, 도구 실행, 기록)
  prompts/system.md         시스템 프롬프트 (역할·절차·신뢰성 규칙·자율 판단 범위)
  tools/schemas.py          도구 정의 7종 (search_catalog, list_datasets, load_dataset, load_file, run_python, make_chart, write_report)
  tools/executor.py         도구 실행기
  catalog.py                데이터 목록 1,259종 검색
  datahub/registry.yaml     등록 데이터셋 19종 (출처·컬럼·이용조건·API 설정, 12종은 Open API ID 포함)
  datahub/client.py         제주데이터허브 Open API 클라이언트 (페이지 수집·캐시·오류 처리)
  datahub/loaders.py        API → 파일 → 샘플 순 적재, 컬럼 정규화
  sandbox.py / sandbox_runner.py   격리 코드 실행
  charts.py                 차트 생성 (plotly)
  report.py                 보고서 생성 (MD/HTML + 재현 스크립트)
  session.py                세션 상태 (표·차트·코드 이력·보고서)
  cli.py                    명령줄 도구
data/catalog/               챌린지 제공 데이터 목록 (xlsx → csv)
data/samples/               합성 샘플 (scripts/make_samples.py)
data/raw/, data/cache/      사용자 다운로드 파일, API 캐시 (git 제외)
outputs/reports|charts|traces   산출물 (git 제외)
scenarios/*.yaml            재현 시나리오 5종
docs/                       기획서·사용자 매뉴얼·데이터 활용 명세·결과 설명자료
tests/                      pytest (API 키 없이 실행 가능)
setup.bat / run.bat         Windows 설치·실행 (macOS·Linux: setup.sh / run.sh)
scripts/make_submission.py  제출용 zip 생성 (dist/)
```

### 에이전트 동작 원리
1. **모델**: Anthropic Claude (`claude-opus-5-5` 기본, `.env` 로 변경). 적응형 사고(adaptive thinking)와 노력 수준(`effort`)으로 깊이를 조절합니다. 시스템 프롬프트의 고정 부분은 프롬프트 캐시로 비용을 줄입니다.
2. **도구 호출 루프**: 모델이 `tool_use` 를 내면 실행기가 결과를 돌려주고, 모델이 다음 행동을 정합니다(최대 25단계, `.env` 로 조절). 오류는 `is_error` 로 돌려주어 모델이 스스로 복구합니다.
3. **코드 실행 격리**: 모델이 쓴 pandas 코드는 `python -I` 하위 프로세스에서 실행되며 허용 모듈 외 import·네트워크·외부 파일 쓰기가 막힙니다. 결과 표·차트만 돌아옵니다.
4. **기록**: 턴마다 `outputs/traces/*.json` 에 질문·도구 호출·결과 요약·토큰 사용량이 남습니다.

## 명령줄 도구

```bash
python -m jeju_agent.cli doctor [--offline]
python -m jeju_agent.cli ask "질문" [--model ...] [--effort low|medium|high|xhigh|max] [--max-steps N]
python -m jeju_agent.cli chat
python -m jeju_agent.cli catalog search "감귤 가격" [--top-k 10] [--api-only] [--category "농축수산"]
python -m jeju_agent.cli data status | load <id|No.|이름> [--source auto|api|file|sample] | test-api <id> [--param k=v]
python -m jeju_agent.cli data check-api [--dataset <id>] [--param k=v] [--rows 2] [--deep]
python -m jeju_agent.cli scenario run scenarios/*.yaml [--quiet]
python -m jeju_agent.cli samples
```

## 제출용 zip 만들기
```bash
python scripts/make_submission.py            # dist/jeju-data-navigator_v<버전>_<일시>.zip
python scripts/make_submission.py --include-outputs   # outputs/ 의 보고서·차트·기록 포함
```
`.env`, `data/raw`, 캐시는 제외되며, zip 안의 `SUBMISSION_MANIFEST.md` 가 챌린지 요구 산출물과 파일의 대응을 설명합니다.

## 테스트
```bash
pip install -r requirements-dev.txt
python -m pytest -q
```
API 키 없이 실행됩니다(스크립트 백엔드로 전체 흐름 검증). UI 는 `JEJU_AGENT_DEMO_SCRIPT=tests/fixtures/demo_script.json streamlit run app.py` 로 키 없이 점검할 수 있습니다.

## 오류·제약사항

| 증상 | 원인·조치 |
|---|---|
| `Claude API 키가 올바르지 않습니다` | `.env` 의 `ANTHROPIC_API_KEY` 확인. 콘솔(console.anthropic.com)에서 발급 |
| `요청이 너무 많아 잠시 제한` | API 사용량 한도. 1분 뒤 재시도 |
| `JEJUDATAHUB_PROJECT_KEY 가 설정되지 않았습니다` | 허브 마이페이지에서 프로젝트 생성 후 키 입력. 없으면 파일/샘플로 동작 |
| `ANTHROPIC_API_KEY: ✖ 예시값이 그대로 있습니다` | `.env` 의 키 자리에 예시 문구가 남아 있음. 실제 키로 교체 |
| `Web firewall security policies have been blocked` | 허브 방화벽이 요청을 차단. 클라우드 서버가 아닌 센터·개인 PC 에서 실행 |
| `setup.bat` 이 Python 을 못 찾음 | Microsoft Store 바로가기만 있는 경우. python.org 에서 설치하고 "Add python.exe to PATH" 체크 |
| `API 고유 ID 미등록` · `api_id 가 레지스트리에 비어 있습니다` | 허브 데이터 상세 페이지의 요청 주소(`…/api/proxy/고유ID/…`)에서 고유 ID 를 `registry.yaml` 에 입력. 그 전까지는 샘플이나 `data/raw` 파일로 동작 |
| `인증 실패(401/403)` | 같은 키로 다른 API 는 되는데 일부만 403 이면, 그 API 를 허브 프로젝트에 아직 담지 않은 것. 허브에서 같은 프로젝트에 추가한 뒤 `data check-api` 를 다시 실행(보고서 끝의 '할 일'에 추가할 API 목록이 나옴). 모든 API 가 403 이면 `.env` 의 프로젝트 키 확인 |
| `check-api` 에서 성공인데 0건 | 날짜 변수가 있는 데이터는 허브 공개가 늦어 최근 기간이 비어 있을 수 있음. `--deep` 으로 가장 최근 날짜 확인. 질문할 때는 에이전트가 자동으로 최근 날짜를 찾음 |
| `제한 시간 90초를 넘겨` | 코드 실행 초과. 모델이 집계 범위를 줄여 재시도함. `JEJU_AGENT_SANDBOX_TIMEOUT` 로 조정 |
| 차트 한글이 □ 로 보임 (PNG) | matplotlib 글꼴 문제. plotly 차트(기본)는 영향 없음. Windows 는 `Malgun Gothic` 자동 사용 |
| 포트 8501 사용 중 | `streamlit run app.py --server.port 8600` |
| 새로 올라온 허브 데이터가 안 보임 | Open API 결과는 `data/cache/` 에 72시간 보관되고 0건 결과는 저장하지 않음. 바로 다시 받으려면 `data/cache/` 의 파일을 지우세요 |
| 답이 몇 분씩 걸림 | `.env` 의 `JEJU_AGENT_EFFORT=max` 는 단계마다 오래 생각함. 평소에는 `medium` 권장 (`ask ... --effort medium` 으로 한 번만 바꿀 수도 있음) |

- 제주데이터허브 Open API 의 실제 응답 필드명은 서비스마다 다를 수 있습니다. `data test-api` 로 확인하고 `registry.yaml` 의 `rename` 으로 한글 컬럼명에 맞추면 됩니다(맞추지 않아도 에이전트는 원본 컬럼명으로 분석합니다).
- 유동인구·카드 데이터는 통신사·카드사 추정치이며 모집단 전체가 아닙니다. 에이전트는 이러한 한계를 답변과 보고서에 명시하도록 설계되었으나 최종 해석은 사용자의 몫입니다.
- LLM 특성상 같은 질문에도 분석 경로가 조금씩 다를 수 있습니다. 보고서의 실행 코드와 재현 스크립트로 결과를 다시 확인할 수 있습니다.
- 외부 서비스 의존성: Anthropic Claude API(유료, 토큰 과금), 제주데이터허브 Open API. 둘 다 인터넷이 필요합니다.

## 문서
- [docs/PLAN.md](docs/PLAN.md) — AI Agent 기획서
- [docs/USER_MANUAL.md](docs/USER_MANUAL.md) — 사용자 매뉴얼 (도민용)
- [docs/DATA_SPEC.md](docs/DATA_SPEC.md) — 데이터 활용 명세
- [docs/RESULT_REPORT.md](docs/RESULT_REPORT.md) — 결과 설명자료

## 라이선스
코드: MIT. 데이터: 제주데이터허브 각 데이터셋의 이용조건을 따르며 출처를 표시합니다. `data/samples/` 는 합성 데이터입니다.
