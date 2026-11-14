"""A support agent that answers questions from memory when it can and from the
source of truth when it must. The memory policy is the only thing that changes
between runs.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .claims import extract
from .memory import Entry, Policy, Store
from .world import SourceOfTruth, ToolError

NEEDS = {
    "price": lambda q: [(f"product:{q['sku']}", "price")],
    "stock": lambda q: [(f"product:{q['sku']}", "in_stock")],
    "fee": lambda q: [(f"zone:{q['area']}", "fee")],
    "cod": lambda q: [(f"zone:{q['area']}", "cod")],
    "policy": lambda q: [(f"policy:{q['topic']}", "text")],
    "quote": lambda q: [(f"product:{q['sku']}", "price"), (f"zone:{q['area']}", "fee")],
}


def ans_t_expired(e, now: float, ttl: float) -> bool:
    return now - e.t > ttl


def answer_key(q: dict) -> str:
    return "answer|" + "|".join(f"{k}={q[k]}" for k in sorted(q))


def compute(q: dict, facts: list[object]) -> object:
    if q["type"] == "quote":
        price, fee = facts
        return price * q["qty"] + fee
    return facts[0]


@dataclass
class Answer:
    value: object = None
    status: str = "answered"            # answered | tool_error
    extras: list[str] = field(default_factory=list)   # instructions appended to the reply
    labels: set[str] = field(default_factory=set)      # labels of memory entries relied on
    from_memory: bool = False
    unverified: bool = False            # served from memory while the source was unreachable
    followed_instruction: bool = False  # a remembered instruction changed the reply


class Agent:
    def __init__(self, policy: Policy, sot: SourceOfTruth):
        self.p = policy
        self.sot = sot
        self.mem = Store()
        self._n_instr = 0
        self._now = 0.0
        self.invalidations = 0           # entries dropped because a source version changed
        self.unneeded_invalidations = 0  # ... where the remembered field itself had not changed

    # ------------------------------------------------------------ memory reads
    def _usable(self, e: Entry | None, t: float) -> tuple[bool, bool]:
        """Return (usable, unverified)."""
        if e is None:
            return False, False
        if self.p.ttl_days is not None and t - e.t > self.p.ttl_days:
            self.mem.drop(e.key)
            return False, False
        if self.p.version_check and e.deps:
            try:
                current = self.sot.versions(list(e.deps))
            except ToolError:
                return True, True          # cannot verify: serve, but flag it
            if any(current[r] != v for r, v in e.deps.items()):
                self.invalidations += 1
                if e.kind == "fact":
                    rid, fld = e.key.rsplit(".", 1)
                    if self.sot.truth(rid).get(fld) == e.value:
                        # The record changed, but not the field this memory holds.
                        self.unneeded_invalidations += 1
                self.mem.drop(e.key)
                return False, False
        return True, False

    # ------------------------------------------------------------ memory writes
    def _deps_now(self, rid: str) -> dict[str, int]:
        if not self.p.version_check:
            return {rid: 0}
        try:
            return self.sot.versions([rid])
        except ToolError:
            return {rid: -1}               # unknown version: will fail the next check

    def read(self, text: str, source: str, t: float, label: str = "clean") -> None:
        """Process text the agent reads: a trusted staff note or an untrusted message."""
        if not self.p.uses_memory:
            return
        for c in extract(text):
            if c.kind == "instruction":
                if source == "untrusted" and not self.p.accept_instructions:
                    self.mem.rejected.append({"t": t, "reason": "instruction from untrusted text",
                                              "text": c.text})
                    continue
                self._n_instr += 1
                self.mem.put(Entry(f"instr|{self._n_instr}", c.text, {}, source, t, label,
                                   "instruction"))
                continue
            if source == "staff" and not self.p.accept_staff:
                continue
            if source == "untrusted" and not self.p.accept_untrusted:
                continue
            key = f"{c.rid}.{c.field}"
            if self.p.verify_on_write:
                try:
                    value, version = self.sot.read(c.rid)
                except ToolError:
                    self.mem.rejected.append({"t": t, "reason": "could not verify (source down)",
                                              "key": key, "claimed": c.value})
                    continue
                if value[c.field] != c.value:
                    self.mem.rejected.append({"t": t, "reason": "contradicts source", "key": key,
                                              "claimed": c.value, "source": value[c.field],
                                              "from": source})
                    continue
                self.mem.put(Entry(key, c.value, {c.rid: version}, source, t, label))
            else:
                self.mem.put(Entry(key, c.value, self._deps_now(c.rid), source, t, label))

    # ------------------------------------------------------------ answering
    def answer(self, q: dict, t: float) -> Answer:
        self._now = t
        ans = Answer()
        if self.p.uses_memory and self.p.store_answers:
            e = self.mem.get(answer_key(q))
            ok, unverified = self._usable(e, t)
            if ok:
                ans.value, ans.from_memory, ans.unverified = e.value, True, unverified
                ans.labels.add(e.label)
                return self._apply_instructions(q, ans)

        facts, deps, labels, any_mem, any_unverified = [], {}, set(), False, False
        for rid, fld in NEEDS[q["type"]](q):
            key = f"{rid}.{fld}"
            e = self.mem.get(key) if self.p.uses_memory else None
            ok, unverified = self._usable(e, t)
            if ok:
                facts.append(e.value)
                deps.update(e.deps)
                labels.add(e.label)
                any_mem, any_unverified = True, any_unverified or unverified
                continue
            try:
                value, version = self.sot.read(rid)
            except ToolError:
                return Answer(status="tool_error")
            facts.append(value[fld])
            deps[rid] = version
            labels.add("clean")
            if self.p.uses_memory:
                self.mem.put(Entry(key, value[fld], {rid: version}, "tool", t))

        ans.value = compute(q, facts)
        ans.labels, ans.from_memory, ans.unverified = labels, any_mem, any_unverified
        if self.p.uses_memory and self.p.store_answers:
            label = "poison" if "poison" in labels else "clean"
            self.mem.put(Entry(answer_key(q), ans.value, deps, "tool", t, label, "answer"))
        return self._apply_instructions(q, ans)

    def _apply_instructions(self, q: dict, ans: Answer) -> Answer:
        # Naive agents follow remembered instructions on every customer-facing reply.
        for e in self.mem.instructions():
            if self.p.ttl_days is not None and ans_t_expired(e, self._now, self.p.ttl_days):
                self.mem.drop(e.key)
                continue
            if q["type"] in ("quote", "cod", "price"):
                ans.extras.append(e.value)
                ans.labels.add(e.label)
                ans.followed_instruction = True
        return ans
