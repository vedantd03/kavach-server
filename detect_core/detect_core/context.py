"""Context scorer: validator prior + lexicon cues (English/Hindi/Hinglish) + column header."""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Optional

from .contracts import Candidate

PRIOR_PASS = 0.55
PRIOR_FAIL = 0.05
PRIOR_SECRET = 0.9
CUE_FIRST = 0.30
CUE_EXTRA = 0.10
CUE_CAP = 0.50
NEG_CUE = -0.45
HEADER = 0.35
WINDOW = 60


def lexicon_key(pii_type: str) -> str:
    return "PAN" if pii_type.startswith("PAN") else pii_type


@lru_cache(maxsize=4096)
def _cue_re(cue: str) -> re.Pattern[str]:
    c = re.escape(cue.lower())
    if cue.isascii():
        return re.compile(rf"(?<![a-z]){c}(?![a-z])")
    return re.compile(c)


def _found(cues: list[str], text: str) -> list[str]:
    low = text.lower()
    return [c for c in cues if _cue_re(c).search(low)]


def cues_for(lexicon: dict[str, Any], pii_type: str) -> tuple[list[str], list[str]]:
    key = lexicon_key(pii_type)
    entry = lexicon.get(key) or {}
    pos = list(entry.get("positive") or [])
    neg = list(entry.get("negative") or [])
    if key in (lexicon.get("numeric_types") or []):
        for c in lexicon.get("_numeric_negative") or []:
            if c not in neg:
                neg.append(c)
    return pos, neg


def header_signal(lexicon: dict[str, Any], pii_type: str, header: Optional[str]) -> int:
    """+1 header names this type, -1 header names a look-alike (invoice/order...), 0 otherwise."""
    if not header:
        return 0
    headers = lexicon.get("headers") or {}
    if _found(list(headers.get(lexicon_key(pii_type)) or []), header):
        return 1
    if _found(list(headers.get("negative") or []), header):
        return -1
    return 0


def score(cand: Candidate, norm_text: str, column_header: Optional[str] = None,
          doc_type_hint: Optional[str] = None, lexicon: Optional[dict[str, Any]] = None
          ) -> tuple[float, str]:
    """Return (score in [0,1], human reason). The reason never contains candidate digits."""
    lexicon = lexicon or {}
    if cand.type == "SECRET":
        s, parts = PRIOR_SECRET, [f"secret pattern ({cand.validator_notes.get('kind', 'pattern')}) {PRIOR_SECRET:.2f}"]
    elif cand.validator_pass:
        s, parts = PRIOR_PASS, [f"{cand.validator_notes.get('check', 'validator')} {PRIOR_PASS:.2f}"]
    else:
        s, parts = PRIOR_FAIL, [f"{cand.validator_notes.get('check', 'validator fail')} {PRIOR_FAIL:.2f}"]

    lo, hi = max(0, cand.start - WINDOW), min(len(norm_text), cand.end + WINDOW)
    window = norm_text[lo:cand.start] + " " + norm_text[cand.end:hi]
    pos, neg = cues_for(lexicon, cand.type)
    pos_hits = _found(pos, window)
    # Prefer the most specific cue in the reason ("mera aadhar" over "aadhar").
    pos_hits.sort(key=len, reverse=True)
    if pos_hits:
        bonus = min(CUE_CAP, CUE_FIRST + CUE_EXTRA * (len(pos_hits) - 1))
        s += bonus
        parts.append(f"cue '{pos_hits[0]}' +{bonus:.2f}")
    neg_hits = _found(neg, window)
    if neg_hits:
        s += NEG_CUE
        parts.append(f"negative cue '{neg_hits[0]}' {NEG_CUE:.2f}")

    h = header_signal(lexicon, cand.type, column_header)
    if h:
        s += HEADER * h
        parts.append(f"column header {'matches' if h > 0 else 'contradicts'} {HEADER * h:+.2f}")

    s = max(0.0, min(1.0, s))
    return round(s, 3), "; ".join(parts) + f" -> {s:.2f}"
