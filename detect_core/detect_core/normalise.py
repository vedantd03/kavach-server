"""Text normalisation with an offset map back to the original string.

- Devanagari digits (U+0966..U+096F) -> ASCII digits.
- Digit groups split by a single space, hyphen or newline are rejoined
  ("2341 2341 2346" -> "234123412346"), greedily while the joined run stays <= 12 digits
  and every piece is >= 2 digits. A column of full-length numbers is never merged.
"""
from __future__ import annotations

import re

_DEVANAGARI = {chr(0x0966 + i): str(i) for i in range(10)}
_SEPS = {" ", "-", "\n"}
_MAX_JOIN = 12
_MIN_PIECE = 2
_RUNS = re.compile(r"\d+")


def normalise(text: str) -> tuple[str, list[int]]:
    """Return (norm_text, offset_map) where offset_map[i] is the original index of norm_text[i].

    offset_map has one extra trailing entry equal to len(text) so end offsets map cleanly.
    """
    ascii_text = "".join(_DEVANAGARI.get(ch, ch) for ch in text)
    drop: set[int] = set()

    runs = [(m.start(), m.end()) for m in _RUNS.finditer(ascii_text)]
    i = 0
    while i < len(runs):
        s, e = runs[i]
        total = e - s
        j = i
        while j + 1 < len(runs):
            ns, ne = runs[j + 1]
            cur_s, cur_e = runs[j]
            gap_ok = ns - cur_e == 1 and ascii_text[cur_e] in _SEPS
            pieces_ok = (cur_e - cur_s) >= _MIN_PIECE and (ne - ns) >= _MIN_PIECE
            if gap_ok and pieces_ok and total + (ne - ns) <= _MAX_JOIN:
                drop.add(cur_e)
                total += ne - ns
                j += 1
            else:
                break
        i = j + 1

    out: list[str] = []
    omap: list[int] = []
    for idx, ch in enumerate(ascii_text):
        if idx in drop:
            continue
        out.append(ch)
        omap.append(idx)
    omap.append(len(text))
    return "".join(out), omap


def to_original_span(offset_map: list[int], start: int, end: int) -> tuple[int, int]:
    """Map a [start, end) span in normalised text back to the original text."""
    if end <= start:
        return offset_map[start], offset_map[start]
    return offset_map[start], offset_map[end - 1] + 1
