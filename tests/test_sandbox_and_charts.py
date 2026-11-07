import pandas as pd

from jeju_agent.charts import ChartError, build_chart, load_figure
from jeju_agent.sandbox import run_code
from jeju_agent.session import AnalysisSession


def test_sandbox_runs_and_returns_tables(tmp_path):
    df = pd.DataFrame({"읍면동": ["애월읍", "애월읍", "노형동"], "방문자수": [100, 150, 300]})
    res = run_code(
        "agg = t.groupby('읍면동')['방문자수'].sum().reset_index()\nsave_table('agg', agg)\nresult = agg\nprint('ok')",
        {"t": df},
        timeout=30,
        chart_dir=tmp_path,
    )
    assert res.ok and "ok" in res.stdout
    assert list(res.new_tables) == ["agg"] and res.result_df is not None
    assert "애월읍" in res.result_preview


def test_sandbox_blocks_dangerous_imports(tmp_path):
    res = run_code("import os\nos.listdir('/')", {}, timeout=20, chart_dir=tmp_path)
    assert not res.ok and "os" in res.error
    res2 = run_code("open('/etc/passwd').read()", {}, timeout=20, chart_dir=tmp_path)
    assert not res2.ok and "PermissionError" in res2.error


def test_sandbox_timeout(tmp_path):
    res = run_code("while True: pass", {}, timeout=3, chart_dir=tmp_path)
    assert not res.ok and "제한 시간" in res.error


def test_sandbox_plotly_fig_saved(tmp_path):
    df = pd.DataFrame({"x": [1, 2, 3], "y": [3, 1, 2]})
    res = run_code("fig = px.line(t, x='x', y='y', title='테스트')", {"t": df}, timeout=30, chart_dir=tmp_path)
    assert res.ok and res.charts and res.charts[0]["kind"] == "plotly" and res.charts[0]["title"] == "테스트"


def test_build_chart_aggregates_and_saves(tmp_path):
    s = AnalysisSession("t1")
    s.add_table("t", pd.DataFrame({"읍면동": ["a", "a", "b", "c"], "값": [1, 2, 3, 4], "구분": ["x", "y", "x", "y"]}))
    rec, notes = build_chart(s, "t", "bar", x="읍면동", y="값", agg="sum", sort="desc", top_n=2, title="합계", chart_dir=tmp_path)
    assert rec.id == "c1" and rec.spec["rows_plotted"] == 2 and any("집계" in n for n in notes)
    fig = load_figure(rec.path)
    assert fig.layout.title.text == "합계"
    rec2, _ = build_chart(s, "t", "pie", x="읍면동", y="값", title="구성", chart_dir=tmp_path)
    assert rec2.id == "c2"
    rec3, _ = build_chart(s, "t", "heatmap", x="읍면동", y="구분", value="값", agg="sum", title="격자", chart_dir=tmp_path)
    assert rec3.kind == "plotly"
    try:
        build_chart(s, "t", "bar", x="없음", y="값", title="x", chart_dir=tmp_path)
        assert False, "should raise"
    except ChartError as e:
        assert "없는 컬럼" in str(e)
