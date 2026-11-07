"""보고서 생성: Markdown + 자체 완결형 HTML (차트 내장) + 재현 스크립트.

보고서에는 에이전트가 쓴 본문 외에 다음이 자동으로 붙는다.
- 합성 샘플 사용 경고(해당 시), 데이터셋별 주의사항
- 데이터 출처·수집 시점·건수·기간·이용조건 표
- 실행한 코드 전체(재현용)와 별도 재현 스크립트 파일
- 생성 일시, 모델, 세션 id
"""

from __future__ import annotations

import base64
import html
import re
from datetime import datetime
from pathlib import Path

import markdown

from . import __version__
from .config import REPORT_DIR
from .session import AnalysisSession, ChartRecord, ReportRecord

CSS = """
body { font-family: 'Malgun Gothic','Apple SD Gothic Neo','NanumGothic','Noto Sans KR',sans-serif; color:#1f1f1d; background:#f6f6f4; margin:0; }
main { max-width: 960px; margin: 0 auto; padding: 32px 20px 64px; background:#fff; }
h1 { font-size: 1.8rem; margin-bottom: .2rem; }
h2 { font-size: 1.3rem; border-bottom: 2px solid #e4e4e0; padding-bottom: .25rem; margin-top: 2rem; }
h3 { font-size: 1.1rem; margin-top: 1.4rem; }
table { border-collapse: collapse; width: 100%; font-size: .92rem; margin: .6rem 0 1rem; }
th, td { border: 1px solid #ddd; padding: 6px 8px; text-align: left; vertical-align: top; }
th { background: #f0f0ec; }
code, pre { font-family: Consolas, 'D2Coding', monospace; font-size: .85rem; }
pre { background: #f4f4f1; padding: 12px; overflow-x: auto; border-radius: 6px; }
.meta { color: #666; font-size: .9rem; }
.warn { background: #fff4e5; border-left: 5px solid #eb6834; padding: 10px 14px; margin: 12px 0; }
.note { background: #eef5ff; border-left: 5px solid #2a78d6; padding: 10px 14px; margin: 12px 0; }
.chart { margin: 16px 0 24px; }
.chart img { max-width: 100%; }
.caption { color:#555; font-size:.88rem; margin-top:-6px; }
footer { color:#777; font-size:.85rem; margin-top: 40px; border-top:1px solid #e4e4e0; padding-top: 12px; }
"""


def _slug(text: str, limit: int = 40) -> str:
    """보고서 파일 이름: 괄호·특수문자를 밑줄로 바꾸고 길이를 제한한다 (괄호 중간에서 잘리지 않게)."""
    text = re.sub(r"[\\/:*?\"<>|()\[\]{}\s]+", "_", text.strip())
    text = re.sub(r"_+", "_", text)
    return text[:limit].strip("_·.") or "report"


def _md_to_html(text: str) -> str:
    return markdown.markdown(text, extensions=["tables", "fenced_code", "sane_lists", "nl2br"])


def _chart_markdown(rec: ChartRecord) -> str:
    if rec.kind == "image":
        return f"![{rec.title}]({rec.path})\n\n*그림 {rec.id}. {rec.title}*\n"
    return f"*그림 {rec.id}. {rec.title}* — 대화형 차트는 HTML 보고서에서 볼 수 있습니다 (`{Path(rec.path).name}`).\n"


def _chart_html(rec: ChartRecord) -> str:
    cap = f'<div class="caption">그림 {html.escape(rec.id)}. {html.escape(rec.title)}</div>'
    try:
        if rec.kind == "image":
            data = base64.b64encode(Path(rec.path).read_bytes()).decode("ascii")
            return f'<div class="chart"><img alt="{html.escape(rec.title)}" src="data:image/png;base64,{data}">{cap}</div>'
        from .charts import figure_html

        return f'<div class="chart">{figure_html(rec.path)}{cap}</div>'
    except Exception as e:  # noqa: BLE001
        return f'<div class="chart warn">차트를 불러오지 못했습니다: {html.escape(str(e))}</div>'


def _citations_markdown(session: AnalysisSession) -> str:
    cites = session.citations()
    if not cites:
        return "(불러온 데이터셋 없음)"
    lines = ["| 데이터 | 제공 | 출처 구분 | 수집 시점 | 건수 | 수집 기간 | 이용조건 |", "|---|---|---|---|---|---|---|"]
    for c in cites:
        no = f" (No.{c['catalog_no']})" if c.get("catalog_no") else ""
        lines.append(
            f"| {c['name']}{no} | {c['provider']} | {c['provenance_label']} | {c['fetched_at']} | {c['rows']:,}행 | {c['period']} | {c['license']} |"
        )
    return "\n".join(lines)


def build_markdown_blocks(
    session: AnalysisSession,
    title: str,
    summary: str,
    sections: list[dict],
    conclusions: list[str],
    limitations: list[str],
    recommendations: list[str] | None,
    model: str,
) -> list[tuple[str, object]]:
    """('md', 문자열) 또는 ('chart', ChartRecord) 블록 목록."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    blocks: list[tuple[str, object]] = []
    head = [f"# {title}", "", f"*생성: {now} · 제주 데이터 길잡이 v{__version__} · 모델 {model} · 세션 {session.id}*", ""]
    if session.uses_synthetic():
        head.append(
            "> ⚠️ **주의: 이 보고서는 합성 샘플 데이터(실제 통계가 아님)로 작성되었습니다.** "
            "실제 분석은 제주데이터허브 Open API 또는 내려받은 원본 파일로 다시 실행해야 합니다.\n"
        )
    head += ["## 핵심 요약", "", summary.strip(), ""]
    blocks.append(("md", "\n".join(head)))

    used_chart_ids: set[str] = set()
    for sec in sections:
        heading = str(sec.get("heading", "")).strip() or "분석"
        body = str(sec.get("body", "")).strip()
        blocks.append(("md", f"## {heading}\n\n{body}\n"))
        for cid in sec.get("chart_ids") or []:
            rec = session.get_chart(str(cid))
            if rec and rec.id not in used_chart_ids:
                blocks.append(("chart", rec))
                used_chart_ids.add(rec.id)
    leftover = [c for c in session.charts if c.id not in used_chart_ids]
    if leftover:
        blocks.append(("md", "## 추가 차트\n"))
        for rec in leftover:
            blocks.append(("chart", rec))

    tail = ["## 결론", ""]
    tail += [f"- {c}" for c in conclusions] or ["- (없음)"]
    tail += ["", "## 한계와 주의사항", ""]
    auto_limits: list[str] = []
    for ds in session.datasets.values():
        if ds.is_synthetic:
            auto_limits.append(f"'{ds.name}' 은(는) 합성 샘플 데이터이므로 수치를 실제 현황으로 해석할 수 없습니다.")
        if ds.spec.notes:
            auto_limits.append(f"'{ds.name}': {ds.spec.notes}")
    seen: set[str] = set()
    for item in list(limitations) + auto_limits:
        if item and item not in seen:
            tail.append(f"- {item}")
            seen.add(item)
    if recommendations:
        tail += ["", "## 제안 및 다음 단계", ""]
        tail += [f"- {r}" for r in recommendations]
    tail += ["", "## 데이터 출처 및 이용조건", "", _citations_markdown(session), ""]
    tail.append("모든 데이터의 저작권과 이용조건은 제주데이터허브(https://www.jejudatahub.net) 각 데이터 상세 페이지를 따릅니다.")
    tail += ["", "## 재현 방법", ""]
    if session.code_history:
        tail.append("아래 코드는 보고서 작성 과정에서 실제로 실행된 순서 그대로입니다. 같은 데이터로 다시 실행하면 같은 결과를 얻습니다.")
        tail.append("")
        for rec in session.code_history:
            status = "" if rec.ok else " (실행 실패 — 참고용)"
            tail.append(f"### 단계 {rec.step}. {rec.purpose}{status}")
            tail.append("")
            tail.append("```python")
            tail.append(rec.code.rstrip())
            tail.append("```")
            tail.append("")
    else:
        tail.append("코드 실행 없이 작성된 보고서입니다.")
    tail.append("")
    tail.append("*재현 스크립트 파일과 차트 파일은 보고서와 같은 폴더(outputs/)에 저장됩니다.*")
    blocks.append(("md", "\n".join(tail)))
    return blocks


def render_markdown(blocks: list[tuple[str, object]]) -> str:
    parts = []
    for kind, payload in blocks:
        if kind == "md":
            parts.append(str(payload))
        else:
            parts.append(_chart_markdown(payload))  # type: ignore[arg-type]
    return "\n".join(parts).strip() + "\n"


def render_html(blocks: list[tuple[str, object]], title: str) -> str:
    body_parts = []
    needs_plotly = any(kind == "chart" and getattr(rec, "kind", "") == "plotly" for kind, rec in blocks)
    for kind, payload in blocks:
        if kind == "md":
            body_parts.append(_md_to_html(str(payload)))
        else:
            body_parts.append(_chart_html(payload))  # type: ignore[arg-type]
    plotly_js = ""
    if needs_plotly:
        try:
            from plotly.offline import get_plotlyjs

            plotly_js = f"<script>{get_plotlyjs()}</script>"
        except Exception:  # noqa: BLE001
            plotly_js = '<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>'
    return (
        "<!doctype html><html lang=\"ko\"><head><meta charset=\"utf-8\">"
        f"<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>{html.escape(title)}</title>"
        f"<style>{CSS}</style>{plotly_js}</head><body><main>"
        + "\n".join(body_parts)
        + "<footer>이 보고서는 제주 데이터 길잡이(AI Agent)가 제주데이터허브 데이터를 바탕으로 자동 생성했습니다. "
        "수치는 도구 실행 결과에서 인용되었으며, 해석에는 '한계와 주의사항'을 함께 참고하세요.</footer>"
        "</main></body></html>"
    )


def write_report(
    session: AnalysisSession,
    title: str,
    summary: str,
    sections: list[dict],
    conclusions: list[str],
    limitations: list[str],
    recommendations: list[str] | None = None,
    model: str = "",
    report_dir: Path | str = REPORT_DIR,
) -> ReportRecord:
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    blocks = build_markdown_blocks(session, title, summary, sections, conclusions, limitations, recommendations, model)
    stem = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{_slug(title)}"
    md_path = report_dir / f"{stem}.md"
    html_path = report_dir / f"{stem}.html"
    script_path = report_dir / f"{stem}_reproduce.py"
    md_path.write_text(render_markdown(blocks), encoding="utf-8")
    html_path.write_text(render_html(blocks, title), encoding="utf-8")
    script_path.write_text(session.reproduction_script(), encoding="utf-8")
    rec = ReportRecord(
        title=title,
        markdown_path=str(md_path),
        html_path=str(html_path),
        created_at=datetime.now().isoformat(timespec="seconds"),
    )
    session.reports.append(rec)
    return rec
