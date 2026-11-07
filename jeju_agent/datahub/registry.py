"""레지스트리(YAML) 로드와 조회."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..config import REGISTRY_FILE


@dataclass
class ColumnSpec:
    name: str
    dtype: str = "str"
    desc: str = ""


@dataclass
class DatasetSpec:
    id: str
    name: str
    catalog_no: int | None = None
    category: str = ""
    provider: str = ""
    source_url: str = ""
    access: str = "file"
    api_id: str = ""
    api_params: dict = field(default_factory=dict)
    file_glob: str = ""
    sample_file: str = ""
    update: str = ""
    license: str = ""
    schema_verified: bool = False
    columns: list[ColumnSpec] = field(default_factory=list)
    rename: dict = field(default_factory=dict)
    notes: str = ""

    @property
    def file_patterns(self) -> list[str]:
        return [p.strip() for p in self.file_glob.split(";") if p.strip()]

    def column_table(self) -> str:
        if not self.columns:
            return "(컬럼 정보 없음)"
        lines = ["| 컬럼 | 형식 | 설명 |", "|---|---|---|"]
        for c in self.columns:
            lines.append(f"| {c.name} | {c.dtype} | {c.desc} |")
        return "\n".join(lines)

    def summary(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "catalog_no": self.catalog_no,
            "category": self.category,
            "access": self.access,
            "update": self.update,
            "license": self.license,
            "columns": [c.name for c in self.columns],
            "notes": self.notes,
        }


class Registry:
    def __init__(self, specs: list[DatasetSpec]):
        self.specs = specs
        self._by_id = {s.id: s for s in specs}
        self._by_no = {s.catalog_no: s for s in specs if s.catalog_no is not None}

    def __iter__(self):
        return iter(self.specs)

    def __len__(self) -> int:
        return len(self.specs)

    def ids(self) -> list[str]:
        return [s.id for s in self.specs]

    def get(self, dataset_id: str) -> DatasetSpec | None:
        return self._by_id.get(dataset_id)

    def by_catalog_no(self, no: int) -> DatasetSpec | None:
        return self._by_no.get(int(no))

    def find(self, key: str) -> DatasetSpec | None:
        """id, 카탈로그 번호, 이름(부분 일치) 순으로 찾는다."""
        key = str(key).strip()
        if key in self._by_id:
            return self._by_id[key]
        if key.isdigit() and int(key) in self._by_no:
            return self._by_no[int(key)]
        lowered = key.lower()
        for s in self.specs:
            if lowered and lowered in s.name.lower():
                return s
        return None


def load_registry(path: Path | str = REGISTRY_FILE) -> Registry:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    specs: list[DatasetSpec] = []
    for item in raw.get("datasets", []):
        cols = [ColumnSpec(**c) if isinstance(c, dict) else ColumnSpec(name=str(c)) for c in item.get("columns", [])]
        item = dict(item)
        item["columns"] = cols
        item["api_params"] = item.get("api_params") or {}
        item["rename"] = item.get("rename") or {}
        specs.append(DatasetSpec(**item))
    return Registry(specs)
