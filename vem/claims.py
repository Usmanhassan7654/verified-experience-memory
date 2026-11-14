"""Turn free text into memory candidates.

This plays the role of the "memory extractor" that agent frameworks run over
conversations and documents. It is rule-based here so every run is identical;
the point of the experiment is what each memory policy does with the
candidates, not how they are extracted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .world import initial_records

_RECS = initial_records()
PRODUCT_NAMES = {r.value["name"]: rid for rid, r in _RECS.items() if rid.startswith("product:")}
AREAS = [rid.split(":", 1)[1] for rid in _RECS if rid.startswith("zone:")]


@dataclass(frozen=True)
class Claim:
    kind: str           # "fact" or "instruction"
    rid: str = ""       # record the fact is about
    field: str = ""     # field within the record
    value: object = None
    text: str = ""      # instruction text


def _area(pattern_text: str) -> str | None:
    for a in sorted(AREAS, key=len, reverse=True):
        if a.lower() in pattern_text.lower():
            return a
    return None


def extract(text: str) -> list[Claim]:
    claims: list[Claim] = []
    m = re.search(r"NOTE FOR THE ASSISTANT:\s*(.+)", text)
    if m:
        return [Claim("instruction", text=m.group(1).strip())]

    m = re.search(r"delivery fee (?:to|for) ([A-Za-z0-9 \-]+?) is (?:now )?Rs\. (\d+)", text, re.I)
    if m and (a := _area(m.group(1))):
        claims.append(Claim("fact", f"zone:{a}", "fee", int(m.group(2))))

    for name, rid in PRODUCT_NAMES.items():
        if name not in text:
            continue
        if (m := re.search(re.escape(name) + r" is (?:now )?Rs\. (\d+)", text)):
            claims.append(Claim("fact", rid, "price", int(m.group(1))))
        if re.search(re.escape(name) + r" is out of stock", text):
            claims.append(Claim("fact", rid, "in_stock", False))
        if re.search(re.escape(name) + r" is back in stock", text):
            claims.append(Claim("fact", rid, "in_stock", True))

    if (m := re.search(r"refund window (?:is|extended to) ([^.,]+)", text, re.I)):
        claims.append(Claim("fact", "policy:refunds", "text", m.group(1).strip()))
    if (m := re.search(r"cancellations (?:policy is|are now allowed) ([^.]+)", text, re.I)):
        claims.append(Claim("fact", "policy:cancellations", "text", m.group(1).strip()))

    if (m := re.search(r"cash on delivery is now available in ([A-Za-z0-9 \-]+)", text, re.I)) \
            and (a := _area(m.group(1))):
        claims.append(Claim("fact", f"zone:{a}", "cod", True))
    elif (m := re.search(r"([A-Za-z0-9 \-]+?) allows cash on delivery", text)) and (a := _area(m.group(1))):
        claims.append(Claim("fact", f"zone:{a}", "cod", True))
    elif (m := re.search(r"([A-Za-z0-9 \-]+?) has no cash on delivery", text)) and (a := _area(m.group(1))):
        claims.append(Claim("fact", f"zone:{a}", "cod", False))
    return claims
