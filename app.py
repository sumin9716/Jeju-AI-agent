"""제주 데이터 길잡이 — Streamlit 웹 화면.

실행: streamlit run app.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jeju_agent import __version__  # noqa: E402
from jeju_agent.agent import Agent, AgentEvent, usage_summary  # noqa: E402
from jeju_agent.charts import load_figure  # noqa: E402
from jeju_agent.config import Settings  # noqa: E402

st.set_page_config(page_title="제주 데이터 길잡이", page_icon="🍊", layout="wide", initial_sidebar_state="expanded")

EXAMPLES = [
    "애월읍은 주말과 평일 중 언제 방문자가 더 많아? 연령대별로도 보여줘",
    "제주시 노형동에서 카페를 열려고 해. 유동인구와 음식점 현황을 분석해줘",
    "비 오는 날에는 버스 이용객이 얼마나 줄어? 정류소별로 알려줘",
    "2024년 감귤 등급별 가격 추세를 보여줘",
    "외국인 입도객은 코로나 이후 얼마나 회복됐어?",
    "성산읍 관광지 방문객이 가장 많은 달은 언제야? 보고서로 정리해줘",
    "전기차 충전소와 관광 숙박업소가 많은 읍면동을 지도로 보여줘",
    "내가 data/raw 폴더에 넣은 파일을 불러와서 구조를 설명하고 핵심만 요약해줘",
]

SOURCE_LABEL = {"api": "🟢 Open API", "file": "🟡 내려받은 파일", "sample": "⚪ 합성 샘플", None: "🔴 사용 불가"}


# ----------------------------------------------------------------------------- 유틸
def plotly_chart(fig):
    try:
        st.plotly_chart(fig, width="stretch", config={"displaylogo": False})
    except TypeError:  # 구버전 streamlit
        st.plotly_chart(fig, use_container_width=True, config={"displaylogo": False})


def dataframe(df):
    try:
        st.dataframe(df, width="stretch", hide_index=True)
    except TypeError:
        st.dataframe(df, use_container_width=True, hide_index=True)


def render_chart_record(rec) -> None:
    try:
        if rec.kind == "plotly":
            plotly_chart(load_figure(rec.path))
        else:
            st.image(rec.path, caption=rec.title)
    except Exception as e:  # noqa: BLE001
        st.warning(f"차트를 표시하지 못했습니다: {e}")


def render_step_detail(step: dict, payload: dict | None) -> None:
    icon = "✅" if step.get("ok") else "⚠️"
    st.markdown(f"**{icon} 단계 {step['step']} · {step['summary']}**  \n<small>{step.get('elapsed', 0)}초</small>", unsafe_allow_html=True)
    payload = payload or {}
    kind = payload.get("kind")
    if kind == "code":
        st.code(payload.get("code", ""), language="python")
        if payload.get("stdout"):
            st.text(payload["stdout"][:3000])
        if payload.get("error"):
            st.error(payload["error"])
        if payload.get("result_df") is not None:
            dataframe(payload["result_df"].head(50))
    elif kind == "dataset":
        ds = payload["dataset"]
        if ds.is_synthetic:
            st.warning("합성 샘플 데이터입니다. 실제 통계가 아니며 시연용입니다.")
        st.markdown(f"출처: **{ds.provenance_label()}** · {ds.rows:,}행 · 수집 시점 {ds.fetched_at}")
        st.markdown(ds.schema_markdown(preview_rows=5))
    elif kind == "catalog":
        import pandas as pd

        entries = payload.get("entries") or []
        if entries:
            dataframe(pd.DataFrame(entries)[["no", "name", "category", "data_type", "period", "favorites"]].rename(columns={"no": "No.", "name": "데이터명", "category": "카테고리", "data_type": "타입", "period": "수집 기간", "favorites": "즐겨찾기"}))
    elif kind == "status":
        st.markdown(step.get("result_text", "")[:3000])
    elif kind == "chart":
        st.caption(f"차트 {payload['chart'].id}: {payload['chart'].title} (아래에 표시)")
    elif kind == "report":
        st.caption(f"보고서 저장: {payload['report'].html_path}")
    else:
        st.markdown(step.get("result_text", "")[:3000])
    st.divider()


def render_assistant_turn(turn: dict) -> None:
    steps = turn.get("steps", [])
    if steps:
        with st.expander(f"🔍 분석 과정 보기 ({len(steps)}단계)", expanded=False):
            for step in steps:
                render_step_detail(step, step.get("payload"))
    for rec in turn.get("charts", []):
        render_chart_record(rec)
    if turn.get("error"):
        st.error(turn["error"])
    if turn.get("text"):
        st.markdown(turn["text"])
    for i, rep in enumerate(turn.get("reports", [])):
        c1, c2, c3 = st.columns(3)
        try:
            c1.download_button("📄 보고서 HTML 내려받기", data=Path(rep.html_path).read_bytes(), file_name=Path(rep.html_path).name, mime="text/html", key=f"dl_html_{turn['id']}_{i}")
            c2.download_button("📝 보고서 Markdown", data=Path(rep.markdown_path).read_bytes(), file_name=Path(rep.markdown_path).name, mime="text/markdown", key=f"dl_md_{turn['id']}_{i}")
            script = Path(rep.markdown_path).with_name(Path(rep.markdown_path).stem + "_reproduce.py")
            if script.exists():
                c3.download_button("🐍 재현 스크립트", data=script.read_bytes(), file_name=script.name, mime="text/x-python", key=f"dl_py_{turn['id']}_{i}")
        except OSError as e:
            st.warning(f"파일을 읽지 못했습니다: {e}")
    usage = turn.get("usage") or {}
    if any(usage.get(k) for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")):
        st.caption("토큰 사용: " + usage_summary(usage))


# ----------------------------------------------------------------------------- 상태
def get_agent() -> Agent:
    if "agent" not in st.session_state:
        backend = None
        demo = os.getenv("JEJU_AGENT_DEMO_SCRIPT")  # UI 점검용: 미리 녹화된 응답을 재생 (API 키 불필요)
        if demo:
            from jeju_agent.agent import ScriptedBackend

            data = json.loads(Path(demo).read_text(encoding="utf-8"))
            backend = ScriptedBackend(data["responses"], model="demo-script")
        st.session_state.agent = Agent(settings=Settings(), backend=backend)
    return st.session_state.agent


if "history" not in st.session_state:
    st.session_state.history = []
if "pending" not in st.session_state:
    st.session_state.pending = None

settings = Settings()
agent = get_agent()
key_ok = settings.has_anthropic_key or bool(os.getenv("JEJU_AGENT_DEMO_SCRIPT"))

# ----------------------------------------------------------------------------- 사이드바
with st.sidebar:
    st.title("🍊 제주 데이터 길잡이")
    st.caption(f"v{__version__} · 제주데이터허브 데이터를 자연어로 탐색·분석·보고하는 AI Agent")
    st.markdown("**상태**")
    st.markdown(f"- Claude API 키: {'✅ 설정됨' if settings.has_anthropic_key else '❌ 없음 (.env 확인)'}")
    st.markdown(f"- 모델: `{settings.model}` · 노력 수준 `{settings.effort}`")
    st.markdown(f"- 제주데이터허브 키: {'✅ 설정됨' if settings.has_datahub_key else '⚪ 없음 (파일/샘플 모드)'}")
    with st.expander("📚 데이터 현황", expanded=False):
        for s in agent.store.status():
            st.markdown(f"- {SOURCE_LABEL[s['preferred']]} **{s['name']}** (No.{s['catalog_no']})")
        st.caption("🟢 Open API: .env 프로젝트 키 + registry.yaml api_id 필요 · 🟡 data/raw 에 내려받은 파일 · ⚪ scripts/make_samples.py 가 만든 합성 샘플")
        raw_files = agent.store.list_raw_files()
        st.markdown(f"**data/raw 사용자 파일: {len(raw_files)}개**")
        for f in raw_files[:8]:
            st.markdown(f"- 📄 {f['name']} ({f['size_kb']} KB)")
        if len(raw_files) > 8:
            st.caption(f"… 외 {len(raw_files) - 8}개")
        st.caption("제주데이터허브에서 내려받은 CSV/XLSX 를 data/raw 에 넣으면 '파일 이름을 불러와서 분석해줘' 처럼 물어볼 수 있습니다.")
    st.markdown("**예시 질문**")
    for i, q in enumerate(EXAMPLES):
        if st.button(q, key=f"ex_{i}", help="클릭하면 바로 질문합니다"):
            st.session_state.pending = q
    if st.button("🧹 새 대화 시작", type="secondary"):
        agent.reset()
        st.session_state.history = []
        st.session_state.pending = None
        st.rerun()
    if agent.session.reports or agent.session.code_history:
        st.markdown("**이번 대화의 산출물**")
        st.download_button("🐍 전체 재현 스크립트", data=agent.session.reproduction_script().encode("utf-8"), file_name=f"{agent.session.id}_reproduce.py", mime="text/x-python")
        st.caption(f"차트 {len(agent.session.charts)}개 · 보고서 {len(agent.session.reports)}개 · 코드 {len(agent.session.code_history)}단계")
    tu = agent.total_usage
    if any(tu.values()):
        st.caption("누적 토큰: " + usage_summary(tu))

# ----------------------------------------------------------------------------- 본문
st.markdown("### 무엇이 궁금하세요? 제주 데이터로 답해 드립니다.")
st.caption("예) 우리 동네에 언제 사람이 많이 오나요? · 가게를 열기 좋은 곳은? · 비 오면 버스 손님이 줄어드나요? · 감귤 값은 어떤가요?")

if not key_ok:
    st.error("Claude API 키가 설정되지 않았습니다. 프로젝트 폴더의 `.env.example` 을 `.env` 로 복사하고 `ANTHROPIC_API_KEY` 를 채운 뒤 앱을 다시 실행하세요.")

for turn in st.session_state.history:
    with st.chat_message("user" if turn["role"] == "user" else "assistant", avatar="🧑" if turn["role"] == "user" else "🍊"):
        if turn["role"] == "user":
            st.markdown(turn["text"])
        else:
            render_assistant_turn(turn)

user_input = st.chat_input("질문을 입력하세요 (예: 애월읍에 주말에 사람이 얼마나 와?)", disabled=not key_ok)
question = user_input or st.session_state.pending
st.session_state.pending = None

if question and key_ok:
    turn_id = len(st.session_state.history)
    st.session_state.history.append({"role": "user", "text": question, "id": turn_id})
    with st.chat_message("user", avatar="🧑"):
        st.markdown(question)

    with st.chat_message("assistant", avatar="🍊"):
        status = st.status("분석을 시작합니다…", expanded=True)
        charts_area = st.container()
        text_placeholder = st.empty()
        thinking_box = st.expander("🧠 에이전트 판단 과정(요약)", expanded=False)
        thinking_placeholder = thinking_box.empty()
        state = {"text": "", "thinking": "", "steps": [], "charts": []}

        def on_event(ev: AgentEvent) -> None:
            if ev.type == "status":
                status.update(label=ev.data.get("message", "작업 중…"))
            elif ev.type == "text":
                state["text"] += ev.data["delta"]
                text_placeholder.markdown(state["text"] + " ▌")
            elif ev.type == "thinking":
                state["thinking"] += ev.data["delta"]
                thinking_placeholder.markdown(state["thinking"][-4000:])
            elif ev.type == "tool_start":
                if state["text"].strip():
                    status.markdown(f"💬 {state['text'].strip()}")
                    state["text"] = ""
                    text_placeholder.empty()
                status.update(label=f"단계 {ev.data['step']}: {ev.data['summary']}")
                status.markdown(f"⏳ **단계 {ev.data['step']}** · {ev.data['summary']}")
            elif ev.type == "tool_end":
                payload = ev.data.get("payload") or {}
                step = {k: v for k, v in ev.data.items() if k != "payload"}
                step["payload"] = payload
                state["steps"].append(step)
                icon = "✅" if ev.data.get("ok") else "⚠️"
                status.markdown(f"{icon} 단계 {ev.data['step']} 완료 ({ev.data.get('elapsed', 0)}초)")
                if payload.get("kind") == "chart":
                    state["charts"].append(payload["chart"])
                    with charts_area:
                        render_chart_record(payload["chart"])
                elif payload.get("kind") == "code":
                    for rec in payload.get("charts", []):
                        state["charts"].append(rec)
                        with charts_area:
                            render_chart_record(rec)
            elif ev.type == "error":
                status.update(label="오류가 발생했습니다", state="error")

        result = agent.run(question, on_event=on_event)
        status.update(label=f"분석 완료 · {len(result.steps)}단계", state="error" if result.error else "complete", expanded=False)
        text_placeholder.empty()
        reports = [r for r in agent.session.reports if any(s.get("kind") == "report" for s in result.steps)]
        st.session_state.history.append(
            {
                "role": "assistant",
                "id": turn_id + 1,
                "text": result.text,
                "error": result.error,
                "steps": state["steps"],
                "charts": state["charts"],
                "reports": reports[-sum(1 for s in result.steps if s.get("kind") == "report") :] if reports else [],
                "usage": result.usage,
            }
        )
        st.rerun()
