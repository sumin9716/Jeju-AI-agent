"""최종 제출용 zip 패키징.

챌린지 요구 산출물(실행 소스 코드 / 실행환경·매뉴얼 / 데이터 활용 명세 / 결과 설명자료)을
비밀정보(.env)·산출물(outputs/)·사용자 다운로드 데이터(data/raw)·캐시를 제외하고 묶는다.

실행: python scripts/make_submission.py [--out dist] [--include-outputs]
"""

from __future__ import annotations

import argparse
import fnmatch
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from jeju_agent import __version__  # noqa: E402

INCLUDE = [
    "app.py", "README.md", "LICENSE", "pyproject.toml", "requirements.txt", "requirements-dev.txt", ".env.example",
    "setup.bat", "run.bat", "setup.sh", "run.sh",
    ".streamlit/config.toml", ".github/workflows/ci.yml",
    "jeju_agent/**", "scripts/**", "tests/**", "scenarios/**", "docs/**",
    "data/catalog/**", "data/samples/**", "data/raw/.gitkeep", "data/cache/.gitkeep",
    "outputs/reports/.gitkeep", "outputs/charts/.gitkeep", "outputs/traces/.gitkeep",
]
EXCLUDE = ["**/__pycache__/**", "**/*.pyc", "**/.pytest_cache/**", ".env", "**/.DS_Store", "**/*.egg-info/**"]

MANIFEST = """# 제출물 구성 (SUBMISSION_MANIFEST)

| 요구 산출물 | 위치 |
|---|---|
| 실행 소스 코드 (Agent 실행 코드, Prompt, Tool/API 연동, 데이터 처리) | `app.py`, `jeju_agent/` (`agent.py`, `prompts/system.md`, `tools/`, `datahub/`, `sandbox*.py`, `charts.py`, `report.py`, `cli.py`) |
| 사용 모델·라이브러리 정보 | `README.md`(에이전트 동작 원리), `requirements.txt`, `pyproject.toml`, `docs/DATA_SPEC.md`(오픈소스 목록) |
| 실행환경·매뉴얼 (README, 설치·실행 절차, 의존성, 환경변수 예시, 사용자 매뉴얼, 오류·제약사항) | `README.md`, `setup.bat`/`run.bat`/`setup.sh`/`run.sh`, `.env.example`, `docs/USER_MANUAL.md` |
| 데이터 활용 명세 (필수 데이터 사용 내역, 출처·이용조건, 전처리·분석 흐름, 외부 데이터·오픈소스) | `docs/DATA_SPEC.md`, `jeju_agent/datahub/registry.yaml`, `data/catalog/` |
| 결과 설명자료 (문제 정의, Agent 구조, 주요 기능, 실행 결과, 검증 방법·한계, 센터 활용 방안) | `docs/RESULT_REPORT.md` (+ `docs/PLAN.md` 기획서) |
| 재현 (시나리오·테스트·샘플) | `scenarios/`, `tests/`, `data/samples/`, `scripts/make_samples.py` |

생성 일시: {now} · 버전 v{version}
"""


def matches_any(rel: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(rel, pat) for pat in patterns)


def collect(include_outputs: bool) -> list[Path]:
    files: list[Path] = []
    patterns = list(INCLUDE) + (["outputs/**"] if include_outputs else [])
    for pat in patterns:
        if "*" in pat:
            base = pat.split("*")[0].rstrip("/")
            base_dir = ROOT / base if base else ROOT
            for p in base_dir.rglob("*"):
                rel = p.relative_to(ROOT).as_posix()
                if p.is_file() and fnmatch.fnmatch(rel, pat.replace("**", "*")) and not matches_any(rel, EXCLUDE):
                    files.append(p)
        else:
            p = ROOT / pat
            if p.is_file():
                files.append(p)
    return sorted(set(files))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="dist")
    ap.add_argument("--include-outputs", action="store_true", help="outputs/ 의 보고서·차트·기록도 포함")
    args = ap.parse_args()
    out_dir = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    zip_path = out_dir / f"jeju-data-navigator_v{__version__}_{stamp}.zip"
    files = collect(args.include_outputs)
    if any(f.name == ".env" for f in files):
        raise SystemExit(".env 가 포함되려 합니다. 중단합니다.")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(f, f"jeju-data-navigator/{f.relative_to(ROOT).as_posix()}")
        zf.writestr("jeju-data-navigator/SUBMISSION_MANIFEST.md", MANIFEST.format(now=datetime.now().isoformat(timespec="minutes"), version=__version__))
    size_mb = zip_path.stat().st_size / 1024 / 1024
    print(f"{zip_path} ({size_mb:.1f} MB, 파일 {len(files) + 1}개)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
