"""스크립트 백엔드로 전체 흐름(데이터 적재 → 코드 실행 → 차트 → 보고서 → 답변)을 검증한다. API 키 불필요."""

from pathlib import Path

from jeju_agent.agent import Agent, AgentEvent, ScriptedBackend
from jeju_agent.config import Settings

CODE = """
df = floating_population_emd[floating_population_emd['읍면동'] == '애월읍'].copy()
df['주말'] = df['날짜'].dt.dayofweek.isin([5, 6]).map({True: '주말', False: '평일'})
daily = df.groupby(['날짜', '주말', '연령대'])['방문자수'].sum().reset_index()
result = daily.groupby(['주말', '연령대'])['방문자수'].mean().round(0).reset_index().rename(columns={'방문자수': '일평균 방문자수'})
save_table('aewol_age', result)
"""


def tu(id_, name, inp):
    return {"type": "tool_use", "id": id_, "name": name, "input": inp}


def test_full_flow_with_scripted_backend():
    backend = ScriptedBackend(
        [
            {"content": [{"type": "text", "text": "먼저 유동인구 데이터를 불러오겠습니다."}, tu("t1", "load_dataset", {"dataset": "floating_population_emd"})], "stop_reason": "tool_use"},
            {"content": [tu("t2", "run_python", {"purpose": "애월읍 주말/평일 연령대별 일평균 방문자 계산", "code": CODE})], "stop_reason": "tool_use"},
            {"content": [tu("t3", "make_chart", {"table": "aewol_age", "chart_type": "bar", "x": "연령대", "y": "일평균 방문자수", "color": "주말", "title": "애월읍 주말이 평일보다 방문자가 많다"})], "stop_reason": "tool_use"},
            {"content": [tu("t4", "write_report", {"title": "애월읍 주말 방문자 분석", "summary": "주말 방문자가 더 많다.", "sections": [{"heading": "결과", "body": "표 참고", "chart_ids": ["c1"]}], "conclusions": ["주말 > 평일"], "limitations": ["샘플 데이터"]})], "stop_reason": "tool_use"},
            {"content": [{"type": "text", "text": "**결론**: 합성 샘플 기준으로 주말 방문자가 더 많습니다."}], "stop_reason": "end_turn"},
        ]
    )
    settings = Settings()
    agent = Agent(settings=settings, backend=backend)
    events: list[AgentEvent] = []
    result = agent.run("애월읍은 주말에 사람이 더 많아?", on_event=events.append)

    assert result.error == ""
    assert "주말" in result.text
    assert [s["name"] for s in result.steps] == ["load_dataset", "run_python", "make_chart", "write_report"]
    assert all(s["ok"] for s in result.steps), [s["result_text"] for s in result.steps if not s["ok"]]
    assert "aewol_age" in agent.session.tables and agent.session.charts[0].id == "c1"
    report = agent.session.reports[0]
    md = Path(report.markdown_path).read_text(encoding="utf-8")
    assert "합성 샘플" in md and "재현 방법" in md and "데이터 출처" in md and "save_table" in md
    html = Path(report.html_path).read_text(encoding="utf-8")
    assert "plotly" in html.lower() and "그림 c1" in html
    assert {e.type for e in events} >= {"tool_start", "tool_end", "final", "text"}
    # 대화 이력: user → assistant(tool_use) → user(tool_result) … → assistant(text)
    assert agent.messages[0]["role"] == "user" and agent.messages[-1]["role"] == "assistant"
    assert backend.calls[0]["system"][0]["cache_control"] == {"type": "ephemeral"}
    # 두 번째 턴도 같은 세션에서 이어진다
    backend.responses.append({"content": [{"type": "text", "text": "네, 이어서 답합니다."}], "stop_reason": "end_turn"})
    r2 = agent.run("고마워")
    assert r2.text.startswith("네") and len(agent.turns) == 2


def test_tool_error_is_returned_to_model_not_raised():
    backend = ScriptedBackend(
        [
            {"content": [tu("t1", "load_dataset", {"dataset": "없는데이터"})], "stop_reason": "tool_use"},
            {"content": [tu("t2", "run_python", {"purpose": "오류", "code": "x = 1/0"})], "stop_reason": "tool_use"},
            {"content": [{"type": "text", "text": "데이터를 찾지 못했습니다."}], "stop_reason": "end_turn"},
        ]
    )
    agent = Agent(settings=Settings(), backend=backend)
    result = agent.run("아무거나")
    assert result.error == "" and not result.steps[0]["ok"] and not result.steps[1]["ok"]
    tool_result_msg = backend.calls[1]["messages"][-1]
    assert tool_result_msg["role"] == "user" and tool_result_msg["content"][0].get("is_error") is True
    assert "ZeroDivisionError" in backend.calls[2]["messages"][-1]["content"][0]["content"]


def test_max_steps_wrap_up():
    settings = Settings()
    settings.max_steps = 1
    backend = ScriptedBackend(
        [
            {"content": [tu("t1", "list_datasets", {})], "stop_reason": "tool_use"},
            {"content": [{"type": "text", "text": "여기까지의 결과로 답합니다."}], "stop_reason": "end_turn"},
        ]
    )
    agent = Agent(settings=settings, backend=backend)
    result = agent.run("질문")
    assert result.text.startswith("여기까지")
    last_user = backend.calls[1]["messages"][-1]["content"]
    assert any(b.get("type") == "text" and "한도" in b["text"] for b in last_user)
