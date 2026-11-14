"""The source of truth: a small business whose records change over time.

Every record has a version number that increases on every change. The
`SourceOfTruth` tool wraps it the way a real database or API would: a full
read costs 1 unit per record, a version check costs 0.1 per record, and both
fail during an outage.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

READ_COST = 1.0
VERSION_COST = 0.1


class ToolError(RuntimeError):
    pass


@dataclass
class Record:
    value: dict
    version: int = 1
    history: list[dict] = field(default_factory=list)  # earlier values, oldest first


def initial_records() -> dict[str, Record]:
    products = {
        "P01": ("Fresh Milk 1L", 230), "P02": ("Plain Yogurt 500g", 180),
        "P03": ("Desi Ghee 1kg", 2450), "P04": ("White Butter 500g", 950),
        "P05": ("Paneer 250g", 390), "P06": ("Greek Yogurt 400g", 420),
        "P07": ("Sweet Lassi 1L", 240), "P08": ("Fresh Cream 200ml", 260),
    }
    zones = {
        "Clifton": (150, True), "DHA Phase 6": (150, True), "Gulshan-e-Iqbal": (200, True),
        "Malir": (250, True), "Korangi": (220, False), "Bahria Town": (350, False),
    }
    policies = {"refunds": "24 hours", "cancellations": "before the order cutoff",
                "subscription-pause": "14 days per month"}
    recs: dict[str, Record] = {}
    for sku, (name, price) in products.items():
        recs[f"product:{sku}"] = Record({"name": name, "price": price, "in_stock": True})
    for area, (fee, cod) in zones.items():
        recs[f"zone:{area}"] = Record({"fee": fee, "cod": cod})
    for topic, text in policies.items():
        recs[f"policy:{topic}"] = Record({"text": text})
    return recs


class SourceOfTruth:
    def __init__(self, records: dict[str, Record] | None = None):
        self.records = records or initial_records()
        self.down = False
        self.cost = 0.0
        self.calls = 0

    # -- changes (applied by the scenario, never by the agent)
    def update(self, rid: str, **changes) -> None:
        rec = self.records[rid]
        rec.history.append(copy.deepcopy(rec.value))
        rec.value.update(changes)
        rec.version += 1

    # -- tool surface used by agents
    def read(self, rid: str) -> tuple[dict, int]:
        if self.down:
            raise ToolError("source of truth unavailable")
        self.cost += READ_COST
        self.calls += 1
        rec = self.records[rid]
        return copy.deepcopy(rec.value), rec.version

    def versions(self, rids: list[str]) -> dict[str, int]:
        if self.down:
            raise ToolError("source of truth unavailable")
        self.cost += VERSION_COST * len(rids)
        self.calls += 1
        return {r: self.records[r].version for r in rids}

    # -- used by the evaluator only (no cost)
    def truth(self, rid: str) -> dict:
        return copy.deepcopy(self.records[rid].value)

    def past_values(self, rid: str) -> list[dict]:
        return copy.deepcopy(self.records[rid].history)
