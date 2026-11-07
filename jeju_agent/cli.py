"""명령줄 도구.

  python -m jeju_agent.cli doctor                 # 실행 환경 점검
  python -m jeju_agent.cli ask "애월읍 주말 유동인구는?"   # 질문 한 번
  python -m jeju_agent.cli chat                   # 대화형
  python -m jeju_agent.cli catalog search "버스"   # 데이터 목록 검색
  python -m jeju_agent.cli data status            # 데이터 출처 상태
  python -m jeju_agent.cli data load 753          # 데이터 불러오기 미리보기
  python -m jeju_agent.cli data test-api floating_population_emd   # Open API 연결 점검
  python -m jeju_agent.cli scenario run scenarios/01_aewol_weekend.yaml   # 재현 시나리오 실행
  python -m jeju_agent.cli samples                # 합성 샘플 데이터 재생성
"""

from __future__ import annotations

import argparse
import json
import runpy
import sys
from datetime import datetime
from pathlib import Path

import yaml

from . import __version__
from .config import CATALOG_FILE, ROOT_DIR, SAMPLES_DIR, TRACE_DIR, Settings, ensure_dirs


def _print(msg: str = "") -> None:
    print(msg, flush=True)


# ----------------------------------------------------------------------------- doctor
def cmd_doctor(args: argparse.Namespace) -> int:
    ok = True
    _print(f"제주 데이터 길잡이 v{__version__} 환경 점검")
    _print(f"- Python: {sys.version.split()[0]} ({sys.executable})")
    if sys.version_info < (3, 10):
        ok = False
        _print("  ✖ Python 3.10 이상이 필요합니다.")
    for mod in ("anthropic", "pandas", "numpy", "streamlit", "plotly", "yaml", "openpyxl", "markdown", "tabulate"):
        try:
            m = __import__(mod)
            _print(f"- {mod}: ✔ {getattr(m, '__version__', '')}")
        except ImportError:
            ok = False
            _print(f"- {mod}: ✖ 설치 필요 (pip install -r requirements.txt)")
    s = Settings()
    _print(f"- ANTHROPIC_API_KEY: {'✔ 설정됨' if s.has_anthropic_key else '✖ 없음 (.env 에 입력)'}")
    _print(f"- 모델: {s.model} / 노력 {s.effort} / 최대 단계 {s.max_steps}")
    _print(f"- JEJUDATAHUB_PROJECT_KEY: {'✔ 설정됨' if s.has_datahub_key else '○ 없음 (파일/샘플 모드로 동작)'}")
    _print(f"- 데이터 목록: {'✔' if CATALOG_FILE.exists() else '✖'} {CATALOG_FILE}")
    n_samples = len(list(SAMPLES_DIR.glob('*_sample.csv')))
    _print(f"- 합성 샘플: {n_samples}개 ({SAMPLES_DIR})" + ("" if n_samples else " → python -m jeju_agent.cli samples"))
    try:
        from .sandbox import run_code

        res = run_code("result = pd.DataFrame({'a': [1, 2]}).sum()", {}, timeout=60)
        _print(f"- 코드 실행 샌드박스: {'✔' if res.ok else '✖ ' + res.error} ({res.elapsed:.1f}초)")
        ok = ok and res.ok
    except Exception as e:  # noqa: BLE001
        ok = False
        _print(f"- 코드 실행 샌드박스: ✖ {e}")
    if s.has_anthropic_key and not args.offline:
        try:
            import anthropic

            client = anthropic.Anthropic()
            r = client.messages.create(model=s.model, max_tokens=32, messages=[{"role": "user", "content": "안녕이라고만 답해"}])
            _print(f"- Claude API 호출: ✔ ({r.model})")
        except Exception as e:  # noqa: BLE001
            ok = False
            _print(f"- Claude API 호출: ✖ {e}")
    _print("결과: " + ("정상입니다. `streamlit run app.py` 로 시작하세요." if ok else "문제가 있습니다. 위 ✖ 항목을 해결하세요."))
    return 0 if ok else 1


# ----------------------------------------------------------------------------- ask / chat
def _make_agent(args: argparse.Namespace):
    from .agent import Agent

    s = Settings()
    if getattr(args, "model", None):
        s.model = args.model
    if getattr(args, "effort", None):
        s.effort = args.effort
    if getattr(args, "max_steps", None):
        s.max_steps = args.max_steps
    if not s.has_anthropic_key:
        _print("ANTHROPIC_API_KEY 가 없습니다. .env 파일을 확인하세요 (doctor 명령으로 점검).")
        sys.exit(2)
    return Agent(settings=s)


def _console_events(quiet: bool):
    from .agent import AgentEvent

    def on_event(ev: AgentEvent) -> None:
        if ev.type == "text" and not quiet:
            print(ev.data["delta"], end="", flush=True)
        elif ev.type == "tool_start":
            print(f"\n▶ 단계 {ev.data['step']}: {ev.data['summary']}", flush=True)
        elif ev.type == "tool_end":
            icon = "✔" if ev.data.get("ok") else "✖"
            kind = ev.data.get("kind")
            extra = ""
            if kind == "chart":
                extra = f" → 차트 {ev.data['payload']['chart'].id}: {ev.data['payload']['chart'].path}"
            elif kind == "report":
                extra = f" → {ev.data['payload']['report'].html_path}"
            elif not ev.data.get("ok"):
                extra = " → " + ev.data.get("result_text", "")[:160].replace("\n", " ")
            print(f"  {icon} {ev.data.get('elapsed', 0)}초{extra}", flush=True)
        elif ev.type == "error":
            print(f"\n[오류] {ev.data['message']}", flush=True)

    return on_event


def cmd_ask(args: argparse.Namespace) -> int:
    agent = _make_agent(args)
    result = agent.run(args.question, on_event=_console_events(quiet=False))
    _print("\n")
    _print("=" * 70)
    _print(result.text)
    _print("=" * 70)
    _print(f"단계 {len(result.steps)}개 · 토큰 입력 {result.usage.get('input_tokens', 0):,} / 출력 {result.usage.get('output_tokens', 0):,}")
    for r in agent.session.reports:
        _print(f"보고서: {r.html_path}")
    return 1 if result.error else 0


def cmd_chat(args: argparse.Namespace) -> int:
    agent = _make_agent(args)
    _print("제주 데이터 길잡이와 대화합니다. 종료: /quit, 새 대화: /reset")
    while True:
        try:
            q = input("\n🧑 > ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q:
            continue
        if q in ("/quit", "/exit"):
            break
        if q == "/reset":
            agent.reset()
            _print("새 대화를 시작합니다.")
            continue
        print("🍊 > ", end="", flush=True)
        result = agent.run(q, on_event=_console_events(quiet=False))
        if result.steps:
            _print("\n" + result.text)
    return 0


# ----------------------------------------------------------------------------- catalog / data
def cmd_catalog_search(args: argparse.Namespace) -> int:
    from .catalog import Catalog

    c = Catalog()
    hits = c.search(args.query, top_k=args.top_k, only_api=args.api_only, category=args.category)
    _print(c.to_markdown(hits) if hits else "검색 결과가 없습니다.")
    return 0


def cmd_data_status(args: argparse.Namespace) -> int:
    from .datahub import DataStore

    store = DataStore()
    label = {"api": "Open API", "file": "내려받은 파일", "sample": "합성 샘플", None: "사용 불가"}
    _print("| id | No. | 데이터명 | 제공 | API id | 파일 | 샘플 | 현재 출처 |")
    _print("|---|---|---|---|---|---|---|---|")
    for s in store.status():
        _print(f"| {s['id']} | {s['catalog_no']} | {s['name']} | {s['access']} | {'✔' if s['api_id_set'] else '－'} | {s['files']} | {'✔' if s['sample'] else '－'} | {label[s['preferred']]} |")
    return 0


def cmd_data_load(args: argparse.Namespace) -> int:
    from .datahub import DataStore

    store = DataStore()
    ds = store.load(args.dataset, prefer=args.source, max_rows=args.max_rows)
    _print(f"{ds.name} ← {ds.provenance_label()} ({ds.source})")
    for n in ds.notes:
        _print(f"- {n}")
    _print(ds.schema_markdown(preview_rows=5))
    return 0


def cmd_data_test_api(args: argparse.Namespace) -> int:
    from .datahub import DataHubError, DataStore
    from .datahub.client import render_params

    store = DataStore()
    spec = store.registry.find(args.dataset)
    if spec is None:
        _print("레지스트리에 없는 데이터셋입니다.")
        return 2
    params = dict(spec.api_params or {})
    for kv in args.param or []:
        k, _, v = kv.partition("=")
        params[k] = v
    try:
        info = store.client.test_connection(spec.api_id, render_params(params))
    except DataHubError as e:
        _print(f"✖ {e}")
        return 1
    _print(f"✔ 연결 성공: 첫 페이지 {info['rows_in_first_page']}건, 전체 {info['total']}건")
    _print("응답 컬럼: " + ", ".join(info["columns"]))
    known = {c.name for c in spec.columns} | set(spec.rename.keys())
    unknown = [c for c in info["columns"] if c not in known and spec.rename.get(c) is None]
    if unknown:
        _print("레지스트리 rename 에 없는 컬럼(필요하면 registry.yaml 의 rename 에 한글 이름을 매핑하세요): " + ", ".join(unknown))
    return 0


# ----------------------------------------------------------------------------- scenario / samples
def cmd_scenario_run(args: argparse.Namespace) -> int:
    agent = _make_agent(args)
    ensure_dirs()
    exit_code = 0
    for file in args.files:
        spec = yaml.safe_load(Path(file).read_text(encoding="utf-8"))
        _print(f"\n### 시나리오: {spec.get('title', file)} ({spec.get('id', '')})")
        agent.reset()
        outputs = []
        for i, q in enumerate(spec.get("questions", []), 1):
            _print(f"\n[{i}] 🧑 {q}")
            result = agent.run(q, on_event=_console_events(quiet=args.quiet))
            _print("\n" + result.text if args.quiet else "")
            outputs.append({"question": q, "answer": result.text, "steps": len(result.steps), "error": result.error, "usage": result.usage})
            if result.error:
                exit_code = 1
        summary = {
            "scenario": spec.get("id"),
            "title": spec.get("title"),
            "ran_at": datetime.now().isoformat(timespec="seconds"),
            "model": agent.settings.model,
            "session_id": agent.session.id,
            "turns": outputs,
            "reports": [r.__dict__ for r in agent.session.reports],
            "charts": [c.__dict__ for c in agent.session.charts],
        }
        out = TRACE_DIR / f"scenario_{spec.get('id', Path(file).stem)}_{agent.session.id}.json"
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        _print(f"\n요약 저장: {out}")
    return exit_code


def cmd_samples(args: argparse.Namespace) -> int:
    runpy.run_path(str(ROOT_DIR / "scripts" / "make_samples.py"), run_name="__main__")
    return 0


# ----------------------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jeju-agent", description="제주 데이터 길잡이 명령줄 도구")
    p.add_argument("--version", action="version", version=f"jeju-agent {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("doctor", help="실행 환경 점검")
    d.add_argument("--offline", action="store_true", help="Claude API 호출 점검 생략")
    d.set_defaults(func=cmd_doctor)

    for name, fn in (("ask", cmd_ask), ("chat", cmd_chat)):
        a = sub.add_parser(name, help="질문 한 번" if name == "ask" else "대화형")
        if name == "ask":
            a.add_argument("question")
        a.add_argument("--model")
        a.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"])
        a.add_argument("--max-steps", type=int, dest="max_steps")
        a.set_defaults(func=fn)

    c = sub.add_parser("catalog", help="데이터 목록").add_subparsers(dest="sub", required=True)
    cs = c.add_parser("search", help="검색")
    cs.add_argument("query")
    cs.add_argument("--top-k", type=int, default=10, dest="top_k")
    cs.add_argument("--api-only", action="store_true", dest="api_only")
    cs.add_argument("--category")
    cs.set_defaults(func=cmd_catalog_search)

    dt = sub.add_parser("data", help="데이터 출처").add_subparsers(dest="sub", required=True)
    dt.add_parser("status", help="출처 상태").set_defaults(func=cmd_data_status)
    dl = dt.add_parser("load", help="불러오기 미리보기")
    dl.add_argument("dataset")
    dl.add_argument("--source", choices=["auto", "api", "file", "sample"], default="auto")
    dl.add_argument("--max-rows", type=int, dest="max_rows")
    dl.set_defaults(func=cmd_data_load)
    ta = dt.add_parser("test-api", help="Open API 연결 점검")
    ta.add_argument("dataset")
    ta.add_argument("--param", action="append", help="질의 변수 key=value (여러 번 가능)")
    ta.set_defaults(func=cmd_data_test_api)

    sc = sub.add_parser("scenario", help="재현 시나리오").add_subparsers(dest="sub", required=True)
    sr = sc.add_parser("run", help="시나리오 파일 실행")
    sr.add_argument("files", nargs="+")
    sr.add_argument("--quiet", action="store_true")
    sr.add_argument("--model")
    sr.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"])
    sr.add_argument("--max-steps", type=int, dest="max_steps")
    sr.set_defaults(func=cmd_scenario_run)

    sub.add_parser("samples", help="합성 샘플 재생성").set_defaults(func=cmd_samples)
    return p


def _configure_console() -> None:
    """윈도우 cmd(cp949/cp1252) 콘솔에서 한글·기호 출력이 UnicodeEncodeError 로 죽지 않게 한다."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):  # 리다이렉션된 스트림 등
            pass


def main(argv: list[str] | None = None) -> int:
    _configure_console()
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
