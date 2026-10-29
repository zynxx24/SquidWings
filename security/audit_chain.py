"""
security.audit_chain — Tamper-evident, append-only audit log.

Every security-relevant event (tool execution, policy denial, secret access,
rate-limit trip, prompt-shield block) is appended as a hash-chained record:

    record_hash = sha256(prev_hash || canonical_json(payload))

Breaking the chain anywhere invalidates every subsequent record, so silent
deletion or edits are detectable. This is the same construction used by
transparency logs, minus the Merkle tree (unnecessary at this scale).

The log is line-delimited JSON (JSONL) so it can be tailed, rotated and
parsed without loading the whole file.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

GENESIS = "0" * 64


def _canonical(payload: Mapping[str, Any]) -> bytes:
    """Deterministic JSON encoding — key order and spacing must be stable."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _hash(prev: str, payload: Mapping[str, Any]) -> str:
    h = hashlib.sha256()
    h.update(prev.encode("ascii"))
    h.update(_canonical(payload))
    return h.hexdigest()


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """One chained audit entry."""

    seq: int
    ts: float
    event: str
    actor: str
    data: Mapping[str, Any]
    prev: str
    hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "ts": self.ts,
            "event": self.event,
            "actor": self.actor,
            "data": dict(self.data),
            "prev": self.prev,
            "hash": self.hash,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> AuditRecord:
        return cls(
            seq=int(raw["seq"]),
            ts=float(raw["ts"]),
            event=str(raw["event"]),
            actor=str(raw.get("actor", "")),
            data=raw.get("data") or {},
            prev=str(raw.get("prev", GENESIS)),
            hash=str(raw.get("hash", "")),
        )


@dataclass
class ChainStatus:
    """Outcome of verifying a chain."""

    valid: bool
    records: int = 0
    broken_at: int | None = None
    reason: str = ""

    def __bool__(self) -> bool:
        return self.valid


class AuditChain:
    """Append-only, hash-chained audit log.

    Args:
        path: JSONL file path.
        fsync: Flush to disk on every append (slower, durable).
        redact: Optional callable applied to `data` before hashing so secrets
            never enter the log.
    """

    def __init__(
        self,
        path: str | Path = "audit.jsonl",
        fsync: bool = False,
        redact: Any = None,
    ) -> None:
        self.path = Path(path)
        self.fsync = fsync
        self._redact = redact
        self._lock = threading.Lock()
        self._seq = 0
        self._last = GENESIS
        self._load_tail()

    # ── internal ────────────────────────────────────────────────────────
    def _load_tail(self) -> None:
        """Read the last record to continue the chain across restarts."""
        if not self.path.exists():
            return
        last_line = ""
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    last_line = line
        if not last_line:
            return
        try:
            raw = json.loads(last_line)
            self._seq = int(raw["seq"])
            self._last = str(raw["hash"])
        except (ValueError, KeyError):
            # Corrupt tail: do not silently continue a broken chain.
            self._seq = 0
            self._last = GENESIS

    def _prepare(self, data: Mapping[str, Any]) -> dict[str, Any]:
        if self._redact is not None:
            try:
                result = self._redact(dict(data))
                return dict(result) if isinstance(result, Mapping) else dict(data)
            except Exception:  # pragma: no cover - redactor must never break audit
                return dict(data)
        return dict(data)

    # ── writing ─────────────────────────────────────────────────────────
    def append(self, event: str, actor: str = "system", **data: Any) -> AuditRecord:
        """Append one event and return the sealed record."""
        payload = self._prepare(data)
        with self._lock:
            seq = self._seq + 1
            ts = time.time()
            body = {"seq": seq, "ts": ts, "event": event, "actor": actor, "data": payload}
            digest = _hash(self._last, body)
            record = AuditRecord(
                seq=seq,
                ts=ts,
                event=event,
                actor=actor,
                data=payload,
                prev=self._last,
                hash=digest,
            )
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
                if self.fsync:
                    fh.flush()
                    os.fsync(fh.fileno())
            self._seq = seq
            self._last = digest
            return record

    # ── reading ─────────────────────────────────────────────────────────
    def __iter__(self) -> Iterator[AuditRecord]:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield AuditRecord.from_dict(json.loads(line))
                except (ValueError, KeyError):
                    continue

    def tail(self, n: int = 20) -> list[AuditRecord]:
        from collections import deque

        return list(deque(self, maxlen=n))

    # ── verification ────────────────────────────────────────────────────
    def verify(self) -> ChainStatus:
        """Recompute every hash and check linkage."""
        expected_prev = GENESIS
        expected_seq = 1
        count = 0
        for record in self:
            count += 1
            if record.seq != expected_seq:
                return ChainStatus(
                    False, count, record.seq, f"sequence gap: expected {expected_seq}"
                )
            if record.prev != expected_prev:
                return ChainStatus(False, count, record.seq, "prev-hash mismatch")
            body = {
                "seq": record.seq,
                "ts": record.ts,
                "event": record.event,
                "actor": record.actor,
                "data": dict(record.data),
            }
            if _hash(record.prev, body) != record.hash:
                return ChainStatus(False, count, record.seq, "record hash mismatch")
            expected_prev = record.hash
            expected_seq += 1
        return ChainStatus(True, count, None, "chain intact")

    def __len__(self) -> int:
        return self._seq

    def __repr__(self) -> str:
        return f"AuditChain(path={self.path}, records={self._seq})"


_chain: AuditChain | None = None
_chain_lock = threading.Lock()


def get_chain() -> AuditChain:
    """Return the process-wide audit chain (redacted, non-fsync)."""
    global _chain
    if _chain is None:
        with _chain_lock:
            if _chain is None:
                from security.redactor import redact

                _chain = AuditChain(
                    os.getenv("SQUIDWINGS_AUDIT_LOG", "audit.jsonl"),
                    redact=redact,
                )
    return _chain


def audit(event: str, actor: str = "system", **data: Any) -> AuditRecord:
    """Module-level convenience for appending to the shared chain."""
    return get_chain().append(event, actor=actor, **data)
