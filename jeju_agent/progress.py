"""명령줄 진행 표시.

모델이 생각하는 동안이나 도구가 실행되는 동안 화면이 멈춘 것처럼 보이지 않도록,
한 줄을 1초마다 다시 그려 경과 시간을 보여준다. (예: "  | 생각하는 중… 12초")
터미널이 아닐 때(파일로 저장·파이프)는 움직이는 표시 대신 한 번만 적는다.
"""

from __future__ import annotations

import sys
import threading
import time
from typing import TextIO

FRAMES = "|/-\\"


class ConsoleProgress:
    def __init__(self, quiet: bool = False, stream: TextIO | None = None, interval: float = 1.0, animate: bool | None = None):
        self.quiet = quiet
        self.stream = stream or sys.stdout
        self.interval = interval
        if animate is None:
            isatty = getattr(self.stream, "isatty", None)
            animate = bool(isatty and isatty())
        self.animate = animate
        self._lock = threading.RLock()
        self._label: str | None = None
        self._since = 0.0
        self._frame = 0
        self._line_open = False  # 진행 표시 줄이 화면에 떠 있음
        self._midline = False  # 마지막 출력이 줄바꿈 없이 끝남 (스트리밍 답변 중)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ 출력
    def _write(self, text: str) -> None:
        try:
            self.stream.write(text)
            self.stream.flush()
        except (UnicodeEncodeError, ValueError, OSError):
            pass

    def _clear(self) -> None:
        if self._line_open:
            self._write("\r" + " " * 70 + "\r")
            self._line_open = False

    def _render(self) -> None:
        if self._label is None:
            return
        secs = int(time.monotonic() - self._since)
        frame = FRAMES[self._frame % len(FRAMES)]
        self._frame += 1
        self._write(f"\r  {frame} {self._label} {secs}초   ")
        self._line_open = True

    def _ensure_thread(self) -> None:
        if self.animate and self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="console-progress", daemon=True)
            self._thread.start()

    def _loop(self) -> None:
        while not self._stop_event.wait(self.interval):
            with self._lock:
                if self._label is not None:
                    self._render()

    # ------------------------------------------------------------------ 제어
    def start(self, label: str) -> None:
        with self._lock:
            self._clear()
            if self._midline:
                self._write("\n")
                self._midline = False
            self._label = label
            self._since = time.monotonic()
            if self.animate:
                self._render()
            else:
                self._write(f"  … {label}\n")
        self._ensure_thread()

    def stop(self) -> None:
        with self._lock:
            self._clear()
            self._label = None

    def print(self, text: str) -> None:
        """진행 표시를 지우고 한 줄 이상을 출력한다."""
        with self._lock:
            self._clear()
            self._label = None
            if self._midline:  # 스트리밍 중이던 답 줄을 끝낸다
                self._write("\n")
            self._write(text if text.endswith("\n") else text + "\n")
            self._midline = False

    def close(self) -> None:
        self.stop()
        self._stop_event.set()

    # ------------------------------------------------------------------ 에이전트 이벤트
    def __call__(self, ev) -> None:
        etype = getattr(ev, "type", "")
        data = getattr(ev, "data", {}) or {}
        if etype == "status":
            self.start(str(data.get("message") or "생각하는 중…"))
        elif etype == "text":
            if self.quiet:
                return
            delta = str(data.get("delta", ""))
            with self._lock:
                if self._label is not None or self._line_open:
                    self._clear()
                    self._label = None
                self._write(delta)
                self._midline = bool(delta) and not delta.endswith("\n")
        elif etype == "tool_start":
            self.print(f"\n▶ 단계 {data.get('step')}: {data.get('summary')}")
            self.start("실행 중…")
        elif etype == "tool_end":
            icon = "✔" if data.get("ok") else "✖"
            payload = data.get("payload") or {}
            kind = data.get("kind")
            extra = ""
            if kind == "chart" and payload.get("chart") is not None:
                extra = f" → 차트 {payload['chart'].id}: {payload['chart'].path}"
            elif kind == "report" and payload.get("report") is not None:
                extra = f" → {payload['report'].html_path}"
            elif not data.get("ok"):
                extra = " → " + str(data.get("result_text", ""))[:160].replace("\n", " ")
            self.print(f"  {icon} {data.get('elapsed', 0)}초{extra}")
        elif etype == "error":
            self.print(f"\n[오류] {data.get('message')}")
        elif etype == "final":
            self.stop()
