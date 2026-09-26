"""Regex recognizers over normalised text -> validated Candidates.

Types: AADHAAR, PAN (refined to PAN_INDIVIDUAL/PAN_BUSINESS by validators), GSTIN, UPI_ID,
BANK_ACCOUNT (needs an IFSC or account cue within 80 chars), MOBILE_IN, EMAIL, SECRET.
"""
from __future__ import annotations

import math
import re
from collections import Counter

from .contracts import Candidate
from .validators import ifsc_valid, validate

UPI_PSPS = [
    "okaxis", "oksbi", "okhdfcbank", "okicici", "ybl", "ibl", "axl", "paytm", "upi", "apl",
    "ptyes", "ptsbi", "pthdfc", "ptaxis", "sbi", "hdfcbank", "icici", "axisbank", "kotak",
    "idfcbank", "idfcfirst", "yesbank", "freecharge", "jupiteraxis", "fbl", "waicici",
    "wahdfcbank", "waaxis", "wasbi", "rbl", "indus", "barodampay", "aubank", "federal", "pnb",
    "cnrb", "boi", "unionbank", "jio", "airtel", "abfspay", "ikwik", "timecosmos", "slice",
    "naviaxis", "superyes", "postbank", "dbs", "hsbc", "citi", "sc", "equitas", "kbl",
]

_AADHAAR = re.compile(r"(?<![\d+])\d{12}(?!\d)")
_AADHAAR_MASKED = re.compile(r"(?<![\w])[Xx*]{4}[\s-]?[Xx*]{4}[\s-]?(\d{4})(?!\d)")
_PAN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{5}\d{4}[A-Za-z](?![A-Za-z0-9])")
_GSTIN = re.compile(r"(?<![A-Za-z0-9])\d{2}[A-Za-z]{5}\d{4}[A-Za-z][1-9A-Za-z][Zz][0-9A-Za-z](?![A-Za-z0-9])")
_UPI = re.compile(
    # A trailing full stop ends the sentence ("pay ravi@oksbi."), but "@oksbi.com" is a domain.
    r"(?<![\w.\-@])([\w.\-]{2,256})@(" + "|".join(UPI_PSPS) + r")(?![\w\-@])(?!\.[\w\-])", re.IGNORECASE)
_EMAIL = re.compile(r"(?<![\w.+\-])[\w.+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}(?![\w\-])")
_IFSC = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{4}0[A-Za-z0-9]{6}(?![A-Za-z0-9])")
_BANK = re.compile(r"(?<![\d+])\d{9,18}(?!\d)")
_BANK_CUE = re.compile(r"a/c|acct|account|khata|खाता|ifsc|beneficiary", re.IGNORECASE)
_MOBILE = re.compile(r"(?<![\d+])(?:\+91[\s-]?|91[\s-]?|0)?([6-9]\d{9})(?!\d)")
_AWS = re.compile(r"(?<![A-Z0-9])AKIA[0-9A-Z]{16}(?![A-Z0-9])")
_GITHUB = re.compile(r"(?<![A-Za-z0-9_])ghp_[A-Za-z0-9]{36}(?![A-Za-z0-9])")
_PEM = re.compile(r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----")
_KV_SECRET = re.compile(
    r"(?i)(?:key|secret|token|password|passwd|pwd)[\w\-]*[\"']?\s*[:=]\s*[\"']?([^\s\"',;]{20,})")

BANK_WINDOW = 80
ENTROPY_MIN = 3.5


def shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in Counter(s).values())


def _cand(t: str, start: int, end: int, norm: str, **kw) -> Candidate:
    return Candidate(type=t, start=start, end=end, normalised=norm, validator_pass=False, **kw)


def recognize(text: str) -> list[Candidate]:
    """Run every recognizer on normalised text; returns validated, de-duplicated candidates."""
    out: list[Candidate] = []

    for m in _AADHAAR.finditer(text):
        out.append(_cand("AADHAAR", m.start(), m.end(), m.group()))
    for m in _AADHAAR_MASKED.finditer(text):
        out.append(_cand("AADHAAR", m.start(), m.end(), "XXXXXXXX" + m.group(1), masked_in_source=True))

    gstin_spans = []
    for m in _GSTIN.finditer(text):
        gstin_spans.append((m.start(), m.end()))
        out.append(_cand("GSTIN", m.start(), m.end(), m.group().upper()))
    for m in _PAN.finditer(text):
        out.append(_cand("PAN", m.start(), m.end(), m.group().upper()))

    upi_spans = []
    for m in _UPI.finditer(text):
        upi_spans.append((m.start(), m.end()))
        out.append(_cand("UPI_ID", m.start(), m.end(), m.group().lower()))
    for m in _EMAIL.finditer(text):
        if any(s <= m.start() < e for s, e in upi_spans):
            continue
        out.append(_cand("EMAIL", m.start(), m.end(), m.group().lower()))

    out.extend(_bank_accounts(text))

    for m in _MOBILE.finditer(text):
        out.append(_cand("MOBILE_IN", m.start(), m.end(), m.group(1)))

    for rx, kind in ((_AWS, "aws_access_key"), (_GITHUB, "github_token")):
        for m in rx.finditer(text):
            out.append(_cand("SECRET", m.start(), m.end(), m.group(), validator_notes={"kind": kind}))
    for m in _PEM.finditer(text):
        out.append(_cand("SECRET", m.start(), m.end(), m.group(), validator_notes={"kind": "private_key"}))
    for m in _KV_SECRET.finditer(text):
        val = m.group(1)
        ent = shannon_entropy(val)
        if ent >= ENTROPY_MIN:
            out.append(_cand("SECRET", m.start(1), m.end(1), val,
                             validator_notes={"kind": "high_entropy", "entropy": round(ent, 2)}))

    validated = [validate(c) for c in out]
    return _dedupe(validated)


_ANY_NUM = re.compile(r"\d{4,}")


def _bank_accounts(text: str) -> list[Candidate]:
    """9-18 digit runs that are either the first number after an account cue (within 40 chars)
    or the number closest to a valid IFSC (within 80 chars)."""
    nums = list(_BANK.finditer(text))
    picked: dict[tuple[int, int], dict] = {}
    for cue in _BANK_CUE.finditer(text):
        nxt = _ANY_NUM.search(text, cue.end())
        if nxt and nxt.start() - cue.end() <= 40:
            for m in nums:
                if m.start() == nxt.start():
                    picked.setdefault((m.start(), m.end()), {})["account_cue"] = True
    for ifsc in _IFSC.finditer(text):
        if not ifsc_valid(ifsc.group()):
            continue
        near = [m for m in nums if min(abs(m.end() - ifsc.start()), abs(ifsc.end() - m.start())) <= BANK_WINDOW]
        if near:
            m = min(near, key=lambda m: min(abs(m.end() - ifsc.start()), abs(ifsc.end() - m.start())))
            picked.setdefault((m.start(), m.end()), {})["ifsc_nearby"] = True
    return [_cand("BANK_ACCOUNT", s, e, text[s:e], validator_notes=notes)
            for (s, e), notes in picked.items()]


def _dedupe(cands: list[Candidate]) -> list[Candidate]:
    """Drop exact duplicates and same-type overlaps (e.g. an AWS key also caught by key=...)."""
    cands = sorted(cands, key=lambda c: (c.start, -(c.end - c.start)))
    kept: list[Candidate] = []
    for c in cands:
        dup = False
        for k in kept:
            overlap = c.start < k.end and k.start < c.end
            if overlap and (c.type == k.type or {c.type, k.type} <= {"SECRET"}):
                dup = True
                break
        if not dup:
            kept.append(c)
    return kept
