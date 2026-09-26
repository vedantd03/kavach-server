"""Deterministic validators: Verhoeff (Aadhaar), GSTIN mod-36, PAN holder type, IFSC format."""
from __future__ import annotations

import re
from typing import Optional

from .contracts import Candidate

# ---------------------------------------------------------------- Verhoeff
_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6], [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4], [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2], [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]
_INV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]


def verhoeff_valid(num: str) -> bool:
    if not num.isdigit():
        return False
    c = 0
    for i, ch in enumerate(reversed(num)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0


def verhoeff_check_digit(num: str) -> str:
    c = 0
    for i, ch in enumerate(reversed(num)):
        c = _D[c][_P[(i + 1) % 8][int(ch)]]
    return str(_INV[c])


def aadhaar_valid(num: str) -> bool:
    return len(num) == 12 and num.isdigit() and num[0] in "23456789" and verhoeff_valid(num)


# ---------------------------------------------------------------- PAN
_PAN_RE = re.compile(r"^[A-Z]{5}\d{4}[A-Z]$")
_PAN_BUSINESS_CHARS = set("CHFATBLJG")


def pan_holder_type(pan: str) -> Optional[str]:
    """4th char P -> PAN_INDIVIDUAL; C,H,F,A,T,B,L,J,G -> PAN_BUSINESS; else None (invalid)."""
    pan = pan.upper()
    if not _PAN_RE.match(pan):
        return None
    if pan[3] == "P":
        return "PAN_INDIVIDUAL"
    if pan[3] in _PAN_BUSINESS_CHARS:
        return "PAN_BUSINESS"
    return None


# ---------------------------------------------------------------- GSTIN
_GST_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_GSTIN_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


def gstin_check_char(first14: str) -> str:
    total = 0
    for i, ch in enumerate(first14.upper()):
        product = _GST_CHARS.index(ch) * (2 if i % 2 else 1)
        total += product // 36 + product % 36
    return _GST_CHARS[(36 - total % 36) % 36]


def gstin_valid(gstin: str) -> bool:
    g = gstin.upper()
    if not _GSTIN_RE.match(g):
        return False
    if pan_holder_type(g[2:12]) is None:
        return False
    return gstin_check_char(g[:14]) == g[14]


# ---------------------------------------------------------------- IFSC
_IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")


def ifsc_valid(code: str) -> bool:
    return bool(_IFSC_RE.match(code.upper()))


# ---------------------------------------------------------------- dispatcher
def validate(c: Candidate) -> Candidate:
    """Fill validator_pass / validator_notes and refine PAN -> PAN_INDIVIDUAL|PAN_BUSINESS."""
    notes = dict(c.validator_notes)
    t = c.type
    ok = True
    if t == "AADHAAR":
        if c.masked_in_source:
            ok, notes["check"] = True, "masked_in_source"
        else:
            ok = aadhaar_valid(c.normalised)
            notes["check"] = "verhoeff_pass" if ok else "verhoeff_fail"
    elif t == "PAN":
        holder = pan_holder_type(c.normalised)
        ok = holder is not None
        notes["check"] = "pan_type_ok" if ok else "pan_type_invalid"
        if holder:
            t = holder
            notes["holder"] = "individual" if holder == "PAN_INDIVIDUAL" else "business"
    elif t == "GSTIN":
        ok = gstin_valid(c.normalised)
        notes["check"] = "gstin_mod36_pass" if ok else "gstin_mod36_fail"
        notes["holder"] = "business"
    elif t == "MOBILE_IN":
        ok = len(c.normalised) == 10 and c.normalised[0] in "6789"
        notes["check"] = "format_ok" if ok else "format_fail"
    elif t == "BANK_ACCOUNT":
        ok = 9 <= len(c.normalised) <= 18 and c.normalised.isdigit()
        notes["check"] = "format_ok" if ok else "format_fail"
    else:  # EMAIL, UPI_ID, SECRET: the recognizer pattern is the check
        notes.setdefault("check", "pattern")
    return c.model_copy(update={"type": t, "validator_pass": ok, "validator_notes": notes})
