"""Seeded topic graph for Cascade Forge Works: plants, lines, equipment, parts, facts.

Facts are the ground truth that documents, golden questions and fault F5 refer to.
Every document id, part number and value comes from here, so cross references
stay consistent across the corpus.
"""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field
from typing import Any

from synth.common import rng

PLANTS = [
    ("P1", "Tacoma Works"),
    ("P2", "Spokane Works"),
    ("P3", "Boise Works"),
]
LINE_NAMES = ["Hot Forge", "Ring Rolling", "Machining", "Heat Treat"]
EQUIPMENT_TYPES: list[tuple[str, str, list[tuple[str, str, float, float, int]]]] = [
    # code, name, [(fact key, unit, low, high, decimals)]
    (
        "HP",
        "hydraulic forging press",
        [
            ("main ram bolt torque", "N·m", 900, 1600, 0),
            ("system pressure limit", "bar", 280, 350, 0),
            ("hydraulic oil change interval", "hours", 1500, 3000, 0),
        ],
    ),
    (
        "IH",
        "induction billet heater",
        [
            ("target billet temperature", "°C", 1150, 1260, 0),
            ("coil cooling water flow minimum", "L/min", 40, 90, 0),
            ("coil inspection interval", "hours", 200, 600, 0),
        ],
    ),
    (
        "RR",
        "radial ring rolling mill",
        [
            ("mandrel clamp bolt torque", "N·m", 300, 650, 0),
            ("main roll bearing lubrication interval", "hours", 80, 240, 0),
            ("maximum ring outer diameter", "mm", 900, 1800, 0),
        ],
    ),
    (
        "CN",
        "CNC turning center",
        [
            ("chuck jaw bolt torque", "N·m", 70, 140, 0),
            ("spindle warm-up time", "minutes", 10, 30, 0),
            ("way lube refill interval", "hours", 40, 120, 0),
        ],
    ),
    (
        "HT",
        "heat treat furnace",
        [
            ("austenitizing setpoint", "°C", 840, 900, 0),
            ("thermocouple calibration interval", "days", 30, 120, 0),
            ("quench delay maximum", "seconds", 8, 25, 0),
        ],
    ),
    (
        "SB",
        "shot blast cabinet",
        [
            ("blast wheel liner bolt torque", "N·m", 45, 110, 0),
            ("shot media top-up interval", "hours", 16, 48, 0),
            ("dust collector pressure drop alarm", "Pa", 1200, 2200, 0),
        ],
    ),
]
PART_FAMILIES = [
    ("flange blank", "4140"),
    ("crankshaft blank", "4340"),
    ("connecting rod", "1045"),
    ("gear blank", "8620"),
    ("bearing ring", "52100"),
    ("valve body", "F316"),
    ("axle hub", "4140"),
    ("track link", "15B37"),
]
PART_FACTS = [
    ("hardness range", "HRC", 22, 44),
    ("bore diameter tolerance", "mm", 0.02, 0.10),
    ("maximum forging weight", "kg", 4, 120),
]
LUBE_PREFIX = "CFW-LUB"


@dataclass
class Fact:
    id: str
    subject: str  # equipment id or part number
    subject_name: str
    key: str
    value: str
    unit: str

    def sentence(self) -> str:
        unit = f" {self.unit}" if self.unit else ""
        return f"The {self.key} for {self.subject_name} ({self.subject}) is {self.value}{unit}."


@dataclass
class Equipment:
    id: str
    type_code: str
    type_name: str
    plant: str
    line: str
    name: str
    facts: list[Fact] = field(default_factory=list)


@dataclass
class Part:
    number: str
    name: str
    material: str
    plant: str
    facts: list[Fact] = field(default_factory=list)


@dataclass
class Line:
    id: str
    plant: str
    name: str
    equipment: list[str]


@dataclass
class TopicGraph:
    plants: list[dict[str, str]]
    lines: list[Line]
    equipment: list[Equipment]
    parts: list[Part]

    def facts(self) -> list[Fact]:
        out: list[Fact] = []
        for e in self.equipment:
            out.extend(e.facts)
        for p in self.parts:
            out.extend(p.facts)
        return out

    def equipment_by_id(self) -> dict[str, Equipment]:
        return {e.id: e for e in self.equipment}

    def to_json(self) -> dict[str, Any]:
        return {
            "plants": self.plants,
            "lines": [asdict(x) for x in self.lines],
            "equipment": [asdict(x) for x in self.equipment],
            "parts": [asdict(x) for x in self.parts],
        }


def _fmt(r: random.Random, low: float, high: float, decimals: int) -> str:
    if decimals == 0:
        step = 5 if high - low > 50 else 1
        return str(int(r.randrange(int(low), int(high) + 1, step)))
    return f"{r.uniform(low, high):.{decimals}f}"


def build_topic_graph(seed: int) -> TopicGraph:
    r = rng(seed, "topics")
    plants = [{"id": pid, "name": name} for pid, name in PLANTS]
    lines: list[Line] = []
    equipment: list[Equipment] = []
    for pid, _ in PLANTS:
        for li, lname in enumerate(LINE_NAMES, start=1):
            line_id = f"{pid}-L{li}"
            types = r.sample(EQUIPMENT_TYPES, 3)
            eq_ids: list[str] = []
            for n, (code, tname, specs) in enumerate(types, start=1):
                eid = f"EQ-{line_id}-{code}{n:02d}"
                eq = Equipment(eid, code, tname, pid, line_id, f"{lname} {tname} #{n}")
                for key, unit, lo, hi, dec in specs:
                    eq.facts.append(
                        Fact(f"{eid}:{key}", eid, eq.name, key, _fmt(r, lo, hi, dec), unit)
                    )
                lube = f"{LUBE_PREFIX}-{r.randrange(100, 999)}"
                eq.facts.append(
                    Fact(f"{eid}:lubricant", eid, eq.name, "lubricant part number", lube, "")
                )
                equipment.append(eq)
                eq_ids.append(eid)
            lines.append(Line(line_id, pid, lname, eq_ids))
    parts: list[Part] = []
    used: set[str] = set()
    for i in range(40):
        fam, material = PART_FAMILIES[i % len(PART_FAMILIES)]
        while True:
            number = f"CFW-{r.randrange(1000, 9999)}-{r.randrange(10, 99)}"
            if number not in used:
                used.add(number)
                break
        plant = PLANTS[i % len(PLANTS)][0]
        part = Part(number, fam, material, plant)
        for key, unit, lo, hi in PART_FACTS:
            if key == "hardness range":
                a = r.randrange(int(lo), int(hi) - 4)
                value = f"{a}-{a + r.randrange(3, 7)}"
            elif key == "bore diameter tolerance":
                value = f"±{r.uniform(lo, hi):.2f}"
            else:
                value = f"{r.uniform(lo, hi):.1f}"
            part.facts.append(Fact(f"{number}:{key}", number, f"{fam} {number}", key, value, unit))
        parts.append(part)
    return TopicGraph(plants, lines, equipment, parts)


def changed_value(r: random.Random, fact: Fact) -> str:
    """A plausible revised value for F5 (torque specs, intervals, part numbers...)."""
    v = fact.value
    if fact.key == "lubricant part number":
        return f"{LUBE_PREFIX}-{r.randrange(100, 999)}"
    if fact.key == "hardness range":
        a = int(v.split("-")[0]) + r.choice([-3, -2, 2, 3])
        return f"{a}-{a + r.randrange(3, 7)}"
    if v.startswith("±"):
        return f"±{float(v[1:]) * r.choice([0.5, 0.75, 1.5]):.2f}"
    num = float(v)
    factor = r.choice([0.8, 0.85, 1.15, 1.2, 1.25])
    if "." in v:
        return f"{num * factor:.1f}"
    new = int(round(num * factor / 5) * 5) if num > 50 else round(num * factor)
    return str(new if new != int(num) else int(num) + 5)
