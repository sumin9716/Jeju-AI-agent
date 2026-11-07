"""에이전트가 작성한 분석 코드를 격리된 하위 프로세스에서 실행한다.

- 별도 파이썬 프로세스(`python -I`)에서 실행하고 제한 시간을 둔다 → UI 가 멈추지 않는다.
- 허용된 모듈(pandas, numpy, plotly, matplotlib 등)만 import 할 수 있고, 네트워크·시스템 모듈은 막는다.
- 파일 쓰기는 작업 폴더 안으로만 허용한다.
- 코드는 `tables` (이름 → DataFrame) 와 같은 이름의 변수로 데이터를 받고,
  `result` 변수(표/값), `fig`(plotly/matplotlib), `save_table(이름, df)` 로 결과를 돌려준다.
"""

from __future__ import annotations

import json
import os
import pickle
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .config import CHART_DIR

RUNNER = Path(__file__).resolve().parent / "sandbox_runner.py"
MAX_STDOUT_CHARS = 6000


@dataclass
class SandboxResult:
    ok: bool
    stdout: str = ""
    error: str = ""
    traceback: str = ""
    result_preview: str = ""
    result_kind: str = ""  # dataframe | series | scalar | json | none
    result_df: pd.DataFrame | None = None
    new_tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    charts: list[dict] = field(default_factory=list)  # {"kind": "plotly"|"image", "path": str, "title": str}
    elapsed: float = 0.0
    truncated: bool = False
    loaded: list[str] = field(default_factory=list)  # 이번 실행에서 불러온 차트 라이브러리

    def to_model_text(self) -> str:
        """LLM 에게 돌려줄 요약 문자열."""
        parts = []
        if not self.ok:
            parts.append(f"[실행 실패] {self.error}")
            if self.traceback:
                parts.append("```\n" + self.traceback[-2500:] + "\n```")
        if self.stdout:
            parts.append("[출력]\n" + self.stdout)
            if self.truncated:
                parts.append("… (출력이 길어 일부만 표시)")
        if self.result_preview:
            parts.append(f"[result ({self.result_kind})]\n" + self.result_preview)
        if self.new_tables:
            desc = ", ".join(f"{k} ({len(v):,}행×{v.shape[1]}열)" for k, v in self.new_tables.items())
            parts.append(f"[저장된 표] {desc}")
        if self.charts:
            parts.append("[생성된 차트] " + ", ".join(c.get("title") or Path(c["path"]).name for c in self.charts))
        parts.append(f"(실행 시간 {self.elapsed:.1f}초)")
        return "\n\n".join(parts)


def run_code(
    code: str,
    tables: dict[str, pd.DataFrame],
    timeout: int = 90,
    chart_dir: Path | str = CHART_DIR,
    keep_workdir: bool = False,
) -> SandboxResult:
    chart_dir = Path(chart_dir)
    chart_dir.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="jeju_sandbox_"))
    started = time.time()
    try:
        with open(workdir / "tables.pkl", "wb") as f:
            pickle.dump(tables, f, protocol=pickle.HIGHEST_PROTOCOL)
        (workdir / "code.py").write_text(code, encoding="utf-8")
        env = {
            "PATH": os.environ.get("PATH", ""),
            "MPLBACKEND": "Agg",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "JEJU_SANDBOX_TIMEOUT": str(timeout),
        }
        # 윈도우에서는 SYSTEMROOT 등이 없으면 파이썬이 뜨지 않는다.
        for key in ("SYSTEMROOT", "SystemRoot", "TEMP", "TMP", "HOME", "USERPROFILE", "LANG"):
            if key in os.environ:
                env[key] = os.environ[key]
        try:
            proc = subprocess.run(
                [sys.executable, "-I", str(RUNNER), str(workdir), str(chart_dir)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout + 5,
                env=env,
                cwd=str(workdir),
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                ok=False,
                error=f"제한 시간 {timeout}초를 넘겨 실행을 중단했습니다. 데이터를 먼저 집계하거나 범위를 줄여 다시 시도하세요.",
                elapsed=time.time() - started,
            )
        manifest_path = workdir / "manifest.json"
        if not manifest_path.exists():
            elapsed = time.time() - started
            if proc.returncode != 0 and elapsed >= timeout - 1:
                msg = f"제한 시간 {timeout}초를 넘겨 실행을 중단했습니다. 데이터를 먼저 집계하거나 범위를 줄여 다시 시도하세요."
            elif proc.returncode != 0:
                msg = "실행기가 비정상 종료했습니다(메모리 초과 또는 치명적 오류 가능)."
            else:
                msg = "실행기가 결과를 남기지 못했습니다."
            return SandboxResult(ok=False, error=msg, traceback=(proc.stderr or "")[-3000:], elapsed=elapsed)
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
        res = SandboxResult(
            ok=bool(manifest.get("ok")),
            stdout=manifest.get("stdout", ""),
            error=manifest.get("error", ""),
            traceback=manifest.get("traceback", ""),
            result_preview=manifest.get("result_preview", ""),
            result_kind=manifest.get("result_kind", "none"),
            charts=manifest.get("charts", []),
            elapsed=time.time() - started,
            loaded=list(manifest.get("loaded", [])),
        )
        if len(res.stdout) > MAX_STDOUT_CHARS:
            res.stdout = res.stdout[:MAX_STDOUT_CHARS]
            res.truncated = True
        if manifest.get("result_pickle"):
            try:
                res.result_df = pd.read_pickle(workdir / manifest["result_pickle"])
            except Exception:  # noqa: BLE001
                res.result_df = None
        for name, rel in (manifest.get("new_tables") or {}).items():
            try:
                res.new_tables[name] = pd.read_pickle(workdir / rel)
            except Exception:  # noqa: BLE001
                continue
        return res
    finally:
        if not keep_workdir:
            shutil.rmtree(workdir, ignore_errors=True)
