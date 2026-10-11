"""Deterministic config patches (ADR 0006).

Faults are injected at runtime, so the repository's rag.yaml often already holds the good
value. A fix PR therefore sets the keys the specialist named, pins them with a comment that
references the incident, and increments `config_revision`, which forces a redeploy that
replaces the runtime state. The YAML is edited line by line so comments and order survive.
"""

from __future__ import annotations

import json
import re
from typing import Any

ALLOWED_FILE = "apps/patient-rag/config/rag.yaml"


class PatchError(ValueError):
    pass


def _render(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(value) if re.search(r"[:#\s]", str(value)) else str(value)


def patch_rag_yaml(text: str, changes: dict[str, Any], incident_id: str) -> str:
    lines = text.splitlines()
    keys = {m.group(1): i for i, line in enumerate(lines) if (m := re.match(r"^([a-z_]+):", line))}
    for key, value in changes.items():
        if key not in keys or key == "config_revision":
            raise PatchError(f"unknown or protected key: {key}")
        i = keys[key]
        comment = f"# pinned by Driftline {incident_id}"
        # align comments, but always leave a space: YAML needs whitespace before "#"
        lines[i] = f"{key}: {_render(value)} ".ljust(28) + comment
    if "config_revision" in keys:
        i = keys["config_revision"]
        current = int(re.findall(r"\d+", lines[i])[0])
        lines[i] = re.sub(r"\d+", str(current + 1), lines[i], count=1)
    else:
        lines.insert(0, "config_revision: 1          # bumped by every fix PR to force a redeploy")
    return "\n".join(lines) + "\n"
