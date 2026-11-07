"""data/raw 임의 파일 적재(load_file)와 관련 도구 동작."""

import pandas as pd
import pytest

from jeju_agent.agent import Agent, ScriptedBackend
from jeju_agent.catalog import Catalog
from jeju_agent.config import Settings
from jeju_agent.datahub import DataStore
from jeju_agent.datahub.loaders import infer_types, read_table_file, sanitize_table_name
from jeju_agent.session import AnalysisSession
from jeju_agent.tools import ToolExecutor


def _store(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    return DataStore(raw_dir=raw), raw


def test_sanitize_and_infer_types():
    assert sanitize_table_name("제주특별자치도_음식점 목록(통합)_20250831") == "제주특별자치도_음식점_목록_통합__20250831"
    assert sanitize_table_name("2024 data").startswith("t_")
    df = pd.DataFrame({"기준일자": ["20240101", "20240102"], "이용금액": ["1,200", "3,400"], "연월": ["2024-01", "2024-02"], "지역": ["a", "b"]})
    out, notes = infer_types(df)
    assert str(out["기준일자"].dtype).startswith("datetime64") and str(out["연월"].dtype).startswith("datetime64")
    assert out["이용금액"].tolist() == [1200, 3400]
    assert any("날짜" in n for n in notes) and any("숫자" in n for n in notes)


def test_read_table_file_skips_title_row(tmp_path):
    p = tmp_path / "t.csv"
    p.write_text("제주특별자치도 클린하우스 현황,,\n읍면동,개수,비고\n애월읍,12,x\n", encoding="cp949")
    df = read_table_file(p)
    assert list(df.columns) == ["읍면동", "개수", "비고"] and len(df) == 1


def test_list_resolve_and_load_file(tmp_path):
    store, raw = _store(tmp_path)
    assert store.list_raw_files() == []
    (raw / "제주특별자치도_클린하우스현황.csv").write_text("읍면동,설치일자,개수\n애월읍,20200616,\"1,200\"\n조천읍,20200617,300\n", encoding="utf-8-sig")
    sub = raw / "sub"
    sub.mkdir()
    (sub / "notes.txt").write_text("a\tb\n1\t2\n", encoding="utf-8")
    pd.DataFrame({"x": [1, 2]}).to_excel(raw / "book.xlsx", index=False, sheet_name="첫시트")
    names = [f["name"] for f in store.list_raw_files()]
    assert names == ["book.xlsx", "sub/notes.txt", "제주특별자치도_클린하우스현황.csv"]

    ds = store.load_file("클린하우스")
    assert ds.provenance == "file" and not ds.is_synthetic and ds.id == "제주특별자치도_클린하우스현황"
    assert str(ds.df["설치일자"].dtype).startswith("datetime64") and ds.df["개수"].tolist() == [1200, 300]
    assert "이용조건" in ds.citation()["license"]

    ds2 = store.load_file("book.xlsx", table_name="book")
    assert ds2.id == "book" and ds2.df["x"].tolist() == [1, 2]
    ds3 = store.load_file("sub/notes.txt")
    assert list(ds3.df.columns) == ["a", "b"]

    with pytest.raises(FileNotFoundError):
        store.load_file("없는파일")
    with pytest.raises(PermissionError):
        store.load_file("../../etc/passwd")
    with pytest.raises(ValueError):
        store.load_file("book.xlsx", sheet="없는시트")


def test_executor_load_file_and_listing(tmp_path):
    store, raw = _store(tmp_path)
    (raw / "my_data.csv").write_text("읍면동,값\n애월읍,1\n", encoding="utf-8")
    session = AnalysisSession("t")
    ex = ToolExecutor(session, store, Catalog(), Settings())
    out = ex.execute("list_datasets", {})
    assert "my_data.csv" in out.text and not out.is_error
    out = ex.execute("load_file", {"file": "my_data"})
    assert not out.is_error and "my_data" in session.tables and out.payload["kind"] == "dataset"
    out = ex.execute("load_file", {"file": "nope"})
    assert out.is_error and "없습니다" in out.text
    out = ex.execute("search_catalog", {"query": "클린하우스"})
    assert "data/raw" in out.text and "my_data.csv" in out.text


def test_catalog_registry_boost():
    c = Catalog()
    plain = c.search("감귤 가격", top_k=5)
    boosted = c.search("감귤 가격", top_k=5, boost_nos={5})
    assert boosted[0].no == 5 and plain[0].no != 5


def test_agent_flow_with_load_file(tmp_path):
    store, raw = _store(tmp_path)
    (raw / "클린하우스.csv").write_text("읍면동,개수\n애월읍,12\n조천읍,30\n", encoding="utf-8-sig")
    backend = ScriptedBackend(
        [
            {"content": [{"type": "tool_use", "id": "a", "name": "load_file", "input": {"file": "클린하우스", "table_name": "clean"}}], "stop_reason": "tool_use"},
            {"content": [{"type": "tool_use", "id": "b", "name": "run_python", "input": {"purpose": "합계", "code": "result = clean['개수'].sum()"}}], "stop_reason": "tool_use"},
            {"content": [{"type": "text", "text": "합계는 42개입니다."}], "stop_reason": "end_turn"},
        ]
    )
    agent = Agent(settings=Settings(), store=store, backend=backend)
    result = agent.run("클린하우스 개수 합계 알려줘")
    assert result.error == "" and all(s["ok"] for s in result.steps)
    assert "42" in result.steps[1]["result_text"]
    assert agent.session.citations()[0]["provenance"] == "file"
