"""샌드박스 하위 프로세스 실행기 (jeju_agent.sandbox 가 호출). 직접 실행하지 않는다.

사용법: python -I sandbox_runner.py <workdir> <chart_dir>
workdir/tables.pkl, workdir/code.py 를 읽어 실행하고 workdir/manifest.json 을 남긴다.
"""

from __future__ import annotations

import builtins
import io
import json
import os
import pickle
import re
import sys
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ALLOWED_MODULES = {
    "pandas",
    "numpy",
    "math",
    "statistics",
    "datetime",
    "json",
    "re",
    "collections",
    "itertools",
    "functools",
    "operator",
    "textwrap",
    "decimal",
    "fractions",
    "random",
    "string",
    "typing",
    "dataclasses",
    "unicodedata",
    "calendar",
    "time",
    "plotly",
    "matplotlib",
    "tabulate",
    "warnings",
    "copy",
    "heapq",
    "bisect",
    "enum",
    "numbers",
    "abc",
    "contextlib",
    "difflib",
    "pprint",
    "locale",
    "zoneinfo",
    "dateutil",
    "pytz",
    "scipy",
    "sklearn",
    "statsmodels",
}


def _top(name: str) -> str:
    return name.split(".")[0]


def make_safe_import(real_import):
    def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level == 0 and _top(name) not in ALLOWED_MODULES:
            raise ImportError(
                f"샌드박스에서는 '{name}' 모듈을 사용할 수 없습니다. 허용: pandas, numpy, plotly, matplotlib, math, statistics, datetime, json, re, collections 등"
            )
        return real_import(name, globals, locals, fromlist, level)

    return safe_import


def make_safe_open(workdir: Path, chart_dir: Path):
    real_open = builtins.open
    allowed_roots = [workdir.resolve(), chart_dir.resolve()]

    def safe_open(file, mode="r", *args, **kwargs):
        p = Path(str(file))
        if not p.is_absolute():
            p = workdir / p
        p = p.resolve()
        writable = any(ch in mode for ch in "wax+")
        if writable and not any(str(p).startswith(str(root)) for root in allowed_roots):
            raise PermissionError(f"샌드박스 밖에는 파일을 쓸 수 없습니다: {file}")
        if not writable and not any(str(p).startswith(str(root)) for root in allowed_roots):
            raise PermissionError(f"샌드박스 밖의 파일은 읽을 수 없습니다: {file}")
        return real_open(p, mode, *args, **kwargs)

    return safe_open


def preview(obj, max_rows: int = 30) -> tuple[str, str]:
    import pandas as pd

    if isinstance(obj, pd.DataFrame):
        head = obj.head(max_rows)
        try:
            text = head.to_markdown(index=not isinstance(obj.index, pd.RangeIndex))
        except Exception:  # noqa: BLE001
            text = head.to_string()
        if len(obj) > max_rows:
            text += f"\n… (전체 {len(obj):,}행 중 {max_rows}행 표시)"
        return "dataframe", text
    if isinstance(obj, pd.Series):
        return "series", preview(obj.to_frame(), max_rows)[1]
    if isinstance(obj, (dict, list, tuple)):
        try:
            text = json.dumps(obj, ensure_ascii=False, default=str, indent=1)
        except Exception:  # noqa: BLE001
            text = repr(obj)
        if len(text) > 4000:
            text = text[:4000] + "\n… (생략)"
        return "json", text
    if obj is None:
        return "none", ""
    return "scalar", repr(obj)[:2000]


def main() -> None:
    workdir = Path(sys.argv[1])
    chart_dir = Path(sys.argv[2])
    manifest: dict = {"ok": False, "stdout": "", "error": "", "traceback": "", "charts": [], "new_tables": {}}
    stamp = time.strftime("%Y%m%d_%H%M%S") + f"_{os.getpid() % 10000:04d}"

    # (리눅스) 메모리·CPU 제한
    try:
        import resource

        limit = int(os.environ.get("JEJU_SANDBOX_TIMEOUT", "90"))
        resource.setrlimit(resource.RLIMIT_CPU, (limit + 20, limit + 30))
        resource.setrlimit(resource.RLIMIT_AS, (6 * 1024**3, 6 * 1024**3))
    except Exception:  # noqa: BLE001 - 윈도우 등에서는 미지원
        pass

    import warnings

    warnings.filterwarnings("ignore")
    import numpy as np
    import pandas as pd

    code = (workdir / "code.py").read_text(encoding="utf-8")
    # 차트 라이브러리는 불러오는 데 몇 초가 걸리므로(특히 Windows) 코드에서 쓸 때만 불러온다.
    want_mpl = bool(re.search(r"\bplt\b|matplotlib", code))
    want_plotly = bool(re.search(r"\bpx\b|\bgo\b|plotly", code))

    plt = None
    if want_mpl:
        try:
            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            try:  # 한글 글꼴이 있으면 사용
                import matplotlib.font_manager as fm

                for name in ("Malgun Gothic", "NanumGothic", "AppleGothic", "Noto Sans CJK KR", "Noto Sans KR"):
                    if any(f.name == name for f in fm.fontManager.ttflist):
                        plt.rcParams["font.family"] = name
                        break
                plt.rcParams["axes.unicode_minus"] = False
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            plt = None

    with open(workdir / "tables.pkl", "rb") as f:
        tables: dict = pickle.load(f)

    saved: dict[str, pd.DataFrame] = {}

    def save_table(name: str, df) -> None:
        if not isinstance(df, pd.DataFrame):
            if isinstance(df, pd.Series):
                df = df.reset_index()
            else:
                raise TypeError("save_table 에는 DataFrame 을 넘겨야 합니다.")
        key = str(name).strip()
        if not key:
            raise ValueError("표 이름이 비어 있습니다.")
        saved[key] = df
        tables[key] = df

    def show(df, n: int = 20) -> None:
        kind, text = preview(df, n)
        print(text)

    namespace: dict = {
        "__name__": "__sandbox__",
        "pd": pd,
        "np": np,
        "tables": tables,
        "save_table": save_table,
        "show": show,
    }
    if plt is not None:
        namespace["plt"] = plt
    if want_plotly:
        try:
            import plotly.express as px
            import plotly.graph_objects as go

            namespace["px"] = px
            namespace["go"] = go
        except Exception:  # noqa: BLE001
            pass
    for key, df in tables.items():
        if key.isidentifier() and key not in namespace:
            namespace[key] = df

    safe_builtins = dict(vars(builtins))
    safe_builtins["__import__"] = make_safe_import(builtins.__import__)
    safe_builtins["open"] = make_safe_open(workdir, chart_dir)
    for banned in ("exec", "eval", "compile", "input", "exit", "quit", "breakpoint", "help"):
        safe_builtins.pop(banned, None)
    namespace["__builtins__"] = safe_builtins

    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            exec(compile(code, "<분석코드>", "exec"), namespace)  # noqa: S102 - 의도된 샌드박스 실행
        manifest["ok"] = True
    except Exception as e:  # noqa: BLE001
        manifest["ok"] = False
        manifest["error"] = f"{type(e).__name__}: {e}"
        tb = traceback.format_exc()
        # 실행기 내부 프레임은 숨기고 사용자 코드 부분만 남긴다.
        marker = '<분석코드>'
        idx = tb.find(marker)
        manifest["traceback"] = tb[max(idx - 200, 0):] if idx >= 0 else tb[-3000:]
    manifest["stdout"] = out.getvalue()
    if err.getvalue().strip():
        manifest["stdout"] += "\n[stderr]\n" + err.getvalue()[-1500:]

    # result 처리
    result = namespace.get("result")
    kind, text = preview(result)
    manifest["result_kind"] = kind
    manifest["result_preview"] = text
    if isinstance(result, (pd.DataFrame, pd.Series)):
        df = result.to_frame() if isinstance(result, pd.Series) else result
        df.to_pickle(workdir / "result.pkl")
        manifest["result_pickle"] = "result.pkl"

    # 저장된 표
    for i, (name, df) in enumerate(saved.items()):
        rel = f"table_{i}.pkl"
        df.to_pickle(workdir / rel)
        manifest["new_tables"][name] = rel

    # 차트: plotly fig / matplotlib figures
    charts: list[dict] = []
    fig = namespace.get("fig")
    if fig is not None and hasattr(fig, "to_json"):
        try:
            title = ""
            try:
                title = (fig.layout.title.text or "") if fig.layout.title else ""
            except Exception:  # noqa: BLE001
                title = ""
            path = chart_dir / f"chart_{stamp}.plotly.json"
            path.write_text(fig.to_json(), encoding="utf-8")
            charts.append({"kind": "plotly", "path": str(path), "title": title})
        except Exception as e:  # noqa: BLE001
            manifest["stdout"] += f"\n[차트 저장 실패] {e}"
    plt = sys.modules.get("matplotlib.pyplot")  # 코드가 직접 불러온 경우도 포함
    if plt is not None:
        try:
            for j, num in enumerate(plt.get_fignums()):
                figure = plt.figure(num)
                if not figure.axes:
                    continue
                path = chart_dir / f"chart_{stamp}_{j}.png"
                figure.savefig(path, dpi=130, bbox_inches="tight")
                title = ""
                try:
                    title = figure.axes[0].get_title() or (figure._suptitle.get_text() if figure._suptitle else "")
                except Exception:  # noqa: BLE001
                    title = ""
                charts.append({"kind": "image", "path": str(path), "title": title})
        except Exception as e:  # noqa: BLE001
            manifest["stdout"] += f"\n[차트 저장 실패] {e}"
    manifest["charts"] = charts
    manifest["loaded"] = sorted(m for m in ("matplotlib", "plotly") if m in sys.modules)

    with open(workdir / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False)


if __name__ == "__main__":
    main()
