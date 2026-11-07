"""명령줄 진행 표시, Ctrl+C 처리, 샌드박스의 차트 라이브러리 지연 불러오기."""

import io

import pandas as pd
import pytest

from jeju_agent.agent import Agent, AgentEvent, ScriptedBackend
from jeju_agent.config import Settings
from jeju_agent.progress import ConsoleProgress
from jeju_agent.sandbox import run_code


class TTY(io.StringIO):
    def isatty(self):
        return True


def test_progress_plain_stream_prints_each_phase_once():
    out = io.StringIO()
    pr = ConsoleProgress(stream=out)  # 터미널이 아니면 움직이는 표시 대신 한 번만 적는다
    assert pr.animate is False
    pr(AgentEvent("status", {"message": "생각하는 중…"}))
    pr(AgentEvent("text", {"delta": "먼저 데이터를 불러오겠습니다."}))
    pr(AgentEvent("tool_start", {"step": 1, "summary": "데이터 불러오기: x"}))
    pr(AgentEvent("tool_end", {"step": 1, "ok": True, "elapsed": 0.5, "kind": "dataset", "payload": {}}))
    pr(AgentEvent("status", {"message": "결과를 해석하는 중…"}))
    pr(AgentEvent("final", {}))
    pr.close()
    text = out.getvalue()
    assert "생각하는 중…" in text and "결과를 해석하는 중…" in text and "실행 중…" in text
    assert "불러오겠습니다.\n\n▶ 단계 1: 데이터 불러오기: x" in text and "✔ 0.5초" in text


def test_progress_terminal_redraws_one_line_and_clears_before_text():
    out = TTY()
    pr = ConsoleProgress(stream=out, interval=60)  # 스레드가 그리기 전에 끝나도록 간격을 길게
    pr(AgentEvent("status", {"message": "생각하는 중…"}))
    assert out.getvalue().startswith("\r  | 생각하는 중… 0초")
    pr(AgentEvent("text", {"delta": "결론"}))
    tail = out.getvalue().split("생각하는 중… 0초", 1)[1]
    assert tail.lstrip(" ").startswith("\r") and tail.endswith("결론")  # 진행 줄을 지우고 답을 쓴다
    pr(AgentEvent("status", {"message": "결과를 해석하는 중…"}))
    assert "결론\n\r  " in out.getvalue()  # 답 줄을 덮어쓰지 않고 다음 줄에 표시
    pr.close()


class InterruptingBackend(ScriptedBackend):
    def create(self, **kwargs):
        raise KeyboardInterrupt


def test_ctrl_c_rolls_back_the_unfinished_turn():
    agent = Agent(settings=Settings(), backend=ScriptedBackend([{"content": [{"type": "text", "text": "안녕"}], "stop_reason": "end_turn"}]))
    agent.run("첫 질문")
    before = list(agent.messages)
    agent.backend = InterruptingBackend([])
    with pytest.raises(KeyboardInterrupt):
        agent.run("두 번째 질문")
    assert agent.messages == before


def test_cli_turn_reports_interrupt_without_traceback(capsys):
    from jeju_agent.cli import _run_turn

    agent = Agent(settings=Settings(), backend=InterruptingBackend([]))
    result, elapsed = _run_turn(agent, "질문")
    assert result is None and elapsed >= 0
    assert "중단했습니다" in capsys.readouterr().out


def test_sandbox_loads_chart_libraries_only_when_used(tmp_path):
    plain = run_code("result = t['a'].sum()", {"t": pd.DataFrame({"a": [1, 2]})}, timeout=60, chart_dir=tmp_path)
    assert plain.ok and plain.loaded == [] and "3" in plain.result_preview
    with_px = run_code("fig = px.bar(t, x='a', y='a', title='p')", {"t": pd.DataFrame({"a": [1, 2]})}, timeout=60, chart_dir=tmp_path)
    assert with_px.ok and "plotly" in with_px.loaded and with_px.charts[0]["kind"] == "plotly"
    own_mpl = run_code("import matplotlib.pyplot as plt\nplt.plot([1, 2])\nplt.title('m')", {}, timeout=60, chart_dir=tmp_path)
    assert own_mpl.ok and "matplotlib" in own_mpl.loaded and own_mpl.charts[0]["kind"] == "image"


def test_cli_ask_prints_answer_once(monkeypatch, capsys):
    import argparse

    from jeju_agent import cli

    answer = "주말 방문자가 더 많습니다. 고유문장123"
    agent = Agent(settings=Settings(), backend=ScriptedBackend([{"content": [{"type": "text", "text": answer}], "stop_reason": "end_turn"}]))
    monkeypatch.setattr(cli, "_make_agent", lambda args: agent)
    code = cli.cmd_ask(argparse.Namespace(question="질문"))
    out = capsys.readouterr().out
    assert code == 0 and out.count("고유문장123") == 1 and "단계 0개" in out


def test_report_file_name_has_no_broken_parenthesis():
    from jeju_agent.report import _slug

    name = _slug("2026년 8월 애월읍 주말·평일 방문자 비교 (연령대별)")
    assert "(" not in name and ")" not in name and len(name) <= 40 and not name.endswith("_")
