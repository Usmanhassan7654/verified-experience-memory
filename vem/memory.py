"""Memory policies compared in the experiment.

All policies share one store format. A memory entry records what was
remembered, where it came from, and which source-record versions it depends
on. The policies differ only in three decisions:

  write gate  - which candidates may be written (tool results, trusted staff
                notes, untrusted messages, instructions)
  write check - whether an incoming claim is verified against the source first
  read check  - what must hold before a remembered value may be used
                (nothing, age limit, or matching source versions)
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Entry:
    key: str
    value: object
    deps: dict[str, int]          # record id -> version this entry was derived from
    source: str                   # "tool" | "staff" | "untrusted"
    t: float                      # day written
    label: str = "clean"          # ground-truth tag for scoring only: clean | poison | benign
    kind: str = "fact"            # fact | answer | instruction


@dataclass
class Policy:
    name: str
    description: str
    store_answers: bool = True            # cache whole answers (e.g. quotes), not only facts
    accept_staff: bool = True             # store claims from trusted staff notes
    accept_untrusted: bool = False        # store facts from untrusted messages
    accept_instructions: bool = False     # store instructions found in messages
    verify_on_write: bool = False         # check a claim against the source before storing it
    ttl_days: float | None = None         # expire entries older than this
    version_check: bool = False           # on read, compare entry deps to current versions
    uses_memory: bool = True


POLICIES = [
    Policy("no_memory", "Always ask the source of truth. The accuracy and cost reference.",
           uses_memory=False, store_answers=False, accept_staff=False),
    Policy("naive", "Remember every answer and every fact or instruction it reads. "
           "Typical auto-extracted memory.", accept_untrusted=True, accept_instructions=True),
    Policy("ttl_7d", "Naive, but entries expire after 7 days. A common quick fix.",
           accept_untrusted=True, accept_instructions=True, ttl_days=7),
    Policy("trust_gated", "Only tool results and trusted staff notes are remembered. "
           "No invalidation.", accept_untrusted=False),
    Policy("version_pinned", "Remember everything, but each entry is pinned to source versions "
           "and re-checked before use.", accept_untrusted=True, accept_instructions=True,
           version_check=True),
    Policy("verified", "Trust-gated writes, untrusted facts verified against the source before "
           "storing, instructions from untrusted text never stored, version check on every read.",
           accept_untrusted=True, verify_on_write=True, version_check=True),
]


# Ablations: the verified policy with one safeguard removed at a time.
ABLATIONS = [
    Policy("verified_without_write_check", "Verified, but untrusted facts are stored without "
           "checking them against the source.", accept_untrusted=True, version_check=True),
    Policy("verified_without_read_check", "Verified, but no version check when reading.",
           accept_untrusted=True, verify_on_write=True),
    Policy("verified_without_instruction_block", "Verified, but instructions found in "
           "untrusted text are stored.", accept_untrusted=True, verify_on_write=True,
           version_check=True, accept_instructions=True),
]


@dataclass
class Store:
    entries: dict[str, Entry] = field(default_factory=dict)
    rejected: list[dict] = field(default_factory=list)   # audit log of refused writes

    def put(self, e: Entry) -> None:
        self.entries[e.key] = e

    def get(self, key: str) -> Entry | None:
        return self.entries.get(key)

    def drop(self, key: str) -> None:
        self.entries.pop(key, None)

    def instructions(self) -> list[Entry]:
        return [e for e in self.entries.values() if e.kind == "instruction"]
