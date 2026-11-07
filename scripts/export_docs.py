"""제출 문서를 인쇄용 HTML(A4)로 내보낸다. 브라우저에서 열어 Ctrl+P → 'PDF로 저장' 하면 제출용 PDF 가 된다.

- 대상: 기획서(docs/PLAN.md), 결과 설명자료, 데이터 활용 명세, 사용자 매뉴얼, README
- 출력: dist/docs/*.html (외부 파일 없이 한 파일로 열림, 글꼴은 Windows 의 맑은 고딕을 먼저 씀)
- --pdf: Playwright 가 설치된 환경이면 PDF 도 만들고 쪽수를 알려 준다(기획서 15쪽 제한 확인용)

실행: python scripts/export_docs.py [--out dist/docs] [--pdf]
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from datetime import date
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from jeju_agent import __version__  # noqa: E402

DOCS = [
    ("docs/PLAN.md", "01_AI_Agent_기획서", "AI Agent 기획서"),
    ("docs/RESULT_REPORT.md", "02_결과_설명자료", "결과 설명자료"),
    ("docs/DATA_SPEC.md", "03_데이터_활용_명세", "데이터 활용 명세"),
    ("docs/USER_MANUAL.md", "04_사용자_매뉴얼", "사용자 매뉴얼"),
    ("README.md", "05_실행환경_README", "실행환경·설치 안내 (README)"),
]
PAGE_LIMITS = {"01_AI_Agent_기획서": 15}

CSS = """
@page { size: A4; margin: 16mm 15mm 18mm; }
:root { color-scheme: light; }
body { font-family: "Malgun Gothic", "맑은 고딕", "Apple SD Gothic Neo", "Noto Sans KR", "Noto Sans CJK KR", sans-serif;
       font-size: 10.5pt; line-height: 1.6; color: #1f2328; background: #ffffff; margin: 0 auto; max-width: 180mm; padding: 8mm 0; }
h1 { font-size: 19pt; margin: 0 0 6pt; padding-bottom: 6pt; border-bottom: 2px solid #1f6feb; }
h2 { font-size: 14pt; margin: 18pt 0 6pt; padding-bottom: 3pt; border-bottom: 1px solid #d0d7de; break-after: avoid; }
h3 { font-size: 12pt; margin: 12pt 0 4pt; break-after: avoid; }
p, li { orphans: 2; widows: 2; word-break: keep-all; overflow-wrap: anywhere; }
table { border-collapse: collapse; width: 100%; margin: 6pt 0 10pt; font-size: 9.5pt; break-inside: auto; }
th, td { border: 1px solid #d0d7de; padding: 3pt 5pt; vertical-align: top; text-align: left; word-break: keep-all; overflow-wrap: anywhere; }
th { background: #f3f6f9; }
tr { break-inside: avoid; }
code { font-family: Consolas, "D2Coding", monospace; font-size: 9pt; background: #f3f6f9; padding: 0 2pt; border-radius: 3px; }
pre { background: #f6f8fa; border: 1px solid #d0d7de; padding: 6pt 8pt; font-size: 8.5pt; line-height: 1.45;
      white-space: pre-wrap; word-break: break-all; break-inside: avoid; }
pre code { background: none; padding: 0; }
blockquote { margin: 6pt 0; padding: 2pt 10pt; border-left: 3px solid #d0d7de; color: #57606a; }
a { color: #0969da; text-decoration: none; }
img { max-width: 100%; }
.meta { color: #57606a; font-size: 9pt; margin-bottom: 10pt; }
.foot { color: #57606a; font-size: 8.5pt; margin-top: 18pt; border-top: 1px solid #d0d7de; padding-top: 4pt; }
@media print { body { padding: 0; } a { color: inherit; } }
"""


def to_html(md_text: str, title: str, source: str) -> str:
    body = markdown.markdown(md_text, extensions=["tables", "fenced_code", "sane_lists"])
    # 저장소 안 상대 링크(.md)는 인쇄본에서 의미가 없으므로 글자만 남긴다
    body = re.sub(r'<a href="(?!https?://)[^"]*">(.*?)</a>', r"\1", body)
    meta = f"제주 데이터 길잡이 v{__version__} · {date.today().isoformat()} · 원본 {html.escape(source)}"
    return (
        "<!doctype html>\n<html lang=\"ko\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>{html.escape(title)} — 제주 데이터 길잡이</title>\n<style>{CSS}</style>\n</head>\n<body>\n"
        f"<div class=\"meta\">{meta}</div>\n{body}\n"
        "<div class=\"foot\">「제주 AI·빅데이터 활용 AI Agent 개발 챌린지」 제출 문서 · 브라우저에서 Ctrl+P → 'PDF로 저장'</div>\n"
        "</body>\n</html>\n"
    )


def export(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for src, stem, title in DOCS:
        text = (ROOT / src).read_text(encoding="utf-8")
        path = out_dir / f"{stem}.html"
        path.write_text(to_html(text, title, src), encoding="utf-8")
        paths.append(path)
    return paths


def to_pdf(paths: list[Path]) -> list[tuple[Path, int]]:
    from playwright.sync_api import sync_playwright  # 설치된 환경에서만

    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        for path in paths:
            page.goto(path.resolve().as_uri())
            pdf_path = path.with_suffix(".pdf")
            page.pdf(path=str(pdf_path), format="A4", print_background=True, prefer_css_page_size=True)
            pages = len(re.findall(rb"/Type\s*/Page[^s]", pdf_path.read_bytes()))
            results.append((pdf_path, pages))
        browser.close()
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="dist/docs")
    ap.add_argument("--pdf", action="store_true", help="Playwright 로 PDF 도 만들고 쪽수를 확인")
    args = ap.parse_args()
    paths = export(ROOT / args.out)
    for p in paths:
        print(f"HTML  {p.relative_to(ROOT)}")
    if args.pdf:
        try:
            results = to_pdf(paths)
        except ImportError:
            print("Playwright 가 없어 PDF 는 만들지 않았습니다. HTML 을 브라우저로 열어 Ctrl+P → 'PDF로 저장' 하세요.")
            return 0
        over = False
        for pdf, n in results:
            limit = PAGE_LIMITS.get(pdf.stem)
            flag = f" (제한 {limit}쪽 {'초과!' if n > limit else '이내'})" if limit else ""
            over = over or bool(limit and n > limit)
            print(f"PDF   {pdf.relative_to(ROOT)}  {n}쪽{flag}")
        return 1 if over else 0
    print("브라우저로 열어 Ctrl+P → 'PDF로 저장' 하면 제출용 PDF 가 됩니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
