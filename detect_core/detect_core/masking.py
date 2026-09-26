"""Masking and hashing. Nothing here returns or logs a raw value except the caller's input."""
from __future__ import annotations

import hashlib
import hmac
import os
import re
from typing import Iterable, Optional

from .contracts import Candidate

SNIPPET_RADIUS = 75
_DIGIT_RUN = re.compile(r"\d{4,}")
_EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")
_HANDLE = re.compile(r"[\w.\-]+@[\w\-]+")


def mask_value(pii_type: str, value: str) -> str:
    """Keep the last 4 characters; everything before becomes X."""
    if len(value) <= 4:
        return "X" * len(value)
    return "X" * (len(value) - 4) + value[-4:]


def _salt() -> bytes:
    salt = os.environ.get("ORG_SALT")
    if not salt:
        raise RuntimeError("ORG_SALT is not set")
    return salt.encode("utf-8")


def value_hash(normalised_value: str) -> str:
    return hmac.new(_salt(), normalised_value.encode("utf-8"), hashlib.sha256).hexdigest()


def candidate_token(c: Candidate) -> str:
    check = "pass" if c.validator_pass else "fail"
    extra = " masked_in_source=true" if c.masked_in_source else ""
    return f"[CANDIDATE type={c.type} len={len(c.normalised)} checksum={check}{extra}]"


def mask_text(text: str) -> str:
    """Mask emails/UPI handles and every >=4-digit run in free text."""
    text = _EMAIL.sub("[EMAIL]", text)
    text = _HANDLE.sub("[HANDLE]", text)
    return _DIGIT_RUN.sub(lambda m: "X" * len(m.group()), text)


def mask_snippet(text: str, cand: Candidate, others: Optional[Iterable[Candidate]] = None,
                 radius: int = SNIPPET_RADIUS) -> str:
    """+-radius window around `cand` (normalised-text offsets) with the candidate replaced by its
    token, other candidates replaced by [TYPE], emails -> [EMAIL], other >=4-digit runs -> X."""
    lo = max(0, cand.start - radius)
    hi = min(len(text), cand.end + radius)
    spans = [(cand.start, cand.end, candidate_token(cand))]
    for o in others or []:
        if o is cand or (o.start == cand.start and o.end == cand.end):
            continue
        if o.end <= lo or o.start >= hi:
            continue
        if o.start < cand.end and cand.start < o.end:
            continue  # overlaps the target; the token covers it
        spans.append((max(o.start, lo), min(o.end, hi), f"[{o.type}]"))
    spans.sort()

    parts: list[str] = []
    pos = lo
    for s, e, rep in spans:
        if s < pos:
            continue
        parts.append(mask_text(text[pos:s]))
        parts.append(rep)
        pos = e
    parts.append(mask_text(text[pos:hi]))
    snippet = "".join(parts).replace("\n", " ")
    return re.sub(r"\s{2,}", " ", snippet).strip()
