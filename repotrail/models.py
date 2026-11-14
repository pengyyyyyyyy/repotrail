from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


@dataclass
class Symbol:
    id: str
    file: str
    module: str
    name: str
    kind: str
    start: int
    end: int
    signature: str
    doc: str = ""


@dataclass
class Edge:
    source: str
    target: str
    file: str
    line: int
    expression: str


@dataclass
class Flow:
    id: str
    title: str
    entry: str
    reason: str


@dataclass
class RepositoryIndex:
    root: str
    name: str
    revision: str | None
    digest: str
    files: dict[str, str]
    symbols: dict[str, Symbol]
    edges: list[Edge]
    flows: list[Flow]
    readme: str
    warnings: list[str] = field(default_factory=list)
    dirty: bool = False

    def overview(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for symbol in self.symbols.values():
            counts[symbol.file] = counts.get(symbol.file, 0) + 1
        incoming: dict[str, int] = {}
        for edge in self.edges:
            file = self.symbols[edge.target].file
            incoming[file] = incoming.get(file, 0) + 1
        modules = sorted(counts, key=lambda file: (
            bool(set(Path(file).parts) & {"tests", "test", "examples", "docs"}),
            -incoming.get(file, 0), file))[:12]
        return {
            "name": self.name,
            "revision": self.revision,
            "dirty": self.dirty,
            "digest": self.digest,
            "file_count": len(self.files),
            "symbol_count": len(self.symbols),
            "edge_count": len(self.edges),
            "readme": self.readme[:3000],
            "flows": [asdict(flow) for flow in self.flows],
            "modules": [{"file": file, "symbols": counts[file],
                         "references": incoming.get(file, 0)} for file in modules],
            "warnings": self.warnings,
        }


class DraftStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol_id: str = Field(min_length=1, max_length=400)
    title: str = Field(min_length=1, max_length=120)
    explanation: str = Field(min_length=1, max_length=1600)


class DraftTrail(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=2000)
    steps: list[DraftStep] = Field(min_length=1, max_length=12)
    gaps: list[str] = Field(default_factory=list, max_length=12)

