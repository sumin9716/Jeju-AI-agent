"""제출 문서 인쇄용 HTML 내보내기 (scripts/export_docs.py)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import export_docs  # noqa: E402


def test_to_html_renders_tables_and_drops_repo_links():
    md = "# 제목\n\n| 단계 | 내용 |\n|---|---|\n| 1 | 탐색 |\n\n[매뉴얼](docs/USER_MANUAL.md) · [허브](https://www.jejudatahub.net)\n"
    html = export_docs.to_html(md, "AI Agent 기획서", "docs/PLAN.md")
    assert html.startswith("<!doctype html>") and "<title>AI Agent 기획서" in html and "<table>" in html
    assert 'href="docs/USER_MANUAL.md"' not in html and "매뉴얼" in html
    assert 'href="https://www.jejudatahub.net"' in html and "@page { size: A4" in html


def test_export_writes_every_submission_doc(tmp_path):
    paths = export_docs.export(tmp_path)
    assert [p.name for p in paths] == [f"{stem}.html" for _, stem, _ in export_docs.DOCS]
    assert all(p.stat().st_size > 2000 for p in paths)
    assert "구현 목표" in paths[0].read_text(encoding="utf-8")
