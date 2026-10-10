"""Named, parameterized KQL queries (spec 5.1.10). Agents never send free-form KQL.

Each queries/<name>.kql file starts with header comments:

    // description: ...
    // params: name:type=default, ...

Types: int(lo..hi), enum(a,b,c), hex32. Values are validated before they are substituted,
so a caller cannot inject KQL through a parameter.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

QUERIES_DIR = Path(
    os.environ.get("KQL_QUERIES_DIR", Path(__file__).resolve().parents[2] / "queries")
)
PARAM = re.compile(r"(\w+):(int\((\d+)\.\.(\d+)\)|enum\(([^)]*)\)|hex32)(?:=([^,\s]+))?")


class QueryError(ValueError):
    pass


@dataclass
class Param:
    name: str
    kind: str
    lo: int = 0
    hi: int = 0
    choices: tuple[str, ...] = ()
    default: str | None = None

    def validate(self, value: Any) -> str:
        if value is None:
            if self.default is None:
                raise QueryError(f"{self.name} is required")
            value = self.default
        if self.kind == "int":
            try:
                v = int(value)
            except (TypeError, ValueError) as e:
                raise QueryError(f"{self.name} must be an integer") from e
            if not self.lo <= v <= self.hi:
                raise QueryError(f"{self.name} must be in {self.lo}..{self.hi}")
            return str(v)
        if self.kind == "enum":
            if str(value) not in self.choices:
                raise QueryError(f"{self.name} must be one of {list(self.choices)}")
            return str(value)
        if not re.fullmatch(r"[0-9a-f]{32}", str(value)):
            raise QueryError(f"{self.name} must be a 32-character hex trace id")
        return str(value)


@dataclass
class NamedQuery:
    name: str
    description: str
    params: list[Param] = field(default_factory=list)
    body: str = ""

    def render(self, args: dict[str, Any]) -> str:
        unknown = set(args) - {p.name for p in self.params}
        if unknown:
            raise QueryError(f"unknown parameters {sorted(unknown)}")
        values = {p.name: p.validate(args.get(p.name)) for p in self.params}
        return self.body.format(**values)


def parse(path: Path) -> NamedQuery:
    text = path.read_text()
    desc, params, body = "", [], []
    for line in text.splitlines():
        if line.startswith("// description:"):
            desc = line.split(":", 1)[1].strip()
        elif line.startswith("// params:"):
            for m in PARAM.finditer(line.split(":", 1)[1]):
                name, typ, lo, hi, choices, default = m.groups()
                if typ.startswith("int"):
                    params.append(Param(name, "int", int(lo), int(hi), default=default))
                elif typ.startswith("enum"):
                    params.append(
                        Param(name, "enum", choices=tuple(choices.split(",")), default=default)
                    )
                else:
                    params.append(Param(name, "hex32", default=default))
        else:
            body.append(line)
    return NamedQuery(path.stem, desc, params, "\n".join(body).strip())


def load_all(directory: Path = QUERIES_DIR) -> dict[str, NamedQuery]:
    return {q.name: q for q in (parse(p) for p in sorted(directory.glob("*.kql")))}
