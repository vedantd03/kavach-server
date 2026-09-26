"""Policy: tiers, bulk rule, markings, risk and suggested actions. All deterministic.

The policy is loaded from YAML text (no file I/O in the pipeline). The served policy text
may carry the context lexicon under a top-level `lexicon:` key (see `combine_policy_text`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from .contracts import DocView

DEFAULT_ITEM = {"category": "personal", "tier": "internal", "weight": 0.3}


@dataclass
class Policy:
    version: str
    tiers: list[str]
    thresholds: dict[str, float]
    items: dict[str, dict[str, Any]]
    documents: dict[str, dict[str, Any]]
    markings: dict[str, str]
    exposure: dict[str, float]
    state: dict[str, float]
    volume: dict[str, float]
    risk_bands: dict[str, float]
    actions: dict[str, list[str]]
    lexicon: dict[str, Any] = field(default_factory=dict)
    yaml_text: str = ""

    # ------------------------------------------------------------ tiers
    def rank(self, tier: Optional[str]) -> int:
        """Higher number = more sensitive. Unknown/None = -1."""
        if tier not in self.tiers:
            return -1
        return len(self.tiers) - 1 - self.tiers.index(tier)

    def highest(self, *tiers: Optional[str]) -> Optional[str]:
        valid = [t for t in tiers if t in self.tiers]
        return max(valid, key=self.rank) if valid else None

    def item(self, pii_type: Optional[str]) -> dict[str, Any]:
        return self.items.get(pii_type or "", DEFAULT_ITEM)

    def category(self, pii_type: Optional[str]) -> str:
        return self.item(pii_type)["category"]

    def is_bulk(self, pii_type: str, count_in_file: int) -> bool:
        th = self.item(pii_type).get("bulk_threshold")
        return th is not None and count_in_file > th

    def item_tier(self, pii_type: str, count_in_file: int) -> tuple[str, str]:
        it = self.item(pii_type)
        th = it.get("bulk_threshold")
        if th is not None and count_in_file > th:
            return it["bulk_tier"], (f"{pii_type} bulk tier ({count_in_file} in file > "
                                     f"bulk threshold {th})")
        suffix = f", bulk threshold {th}" if th is not None else ""
        return it["tier"], f"{pii_type} item tier ({count_in_file} in file{suffix})"

    def doc_tier(self, doc_view: Optional[DocView]) -> tuple[Optional[str], str]:
        if doc_view is None or doc_view.doc_type not in self.documents:
            return None, ""
        t = self.documents[doc_view.doc_type]["tier"]
        return t, f"document type {doc_view.doc_type} -> {t}"

    def marking_tier(self, text: str) -> tuple[Optional[str], Optional[str]]:
        """Highest tier raised by a marking found (case-sensitive) in `text`."""
        best: tuple[Optional[str], Optional[str]] = (None, None)
        for marking, tier in self.markings.items():
            if marking in text and self.rank(tier) > self.rank(best[0]):
                best = (tier, marking)
        return best

    def final_tier(self, base: tuple[str, str], *raises: tuple[Optional[str], str]) -> tuple[str, str]:
        """Highest of the base tier and any raises. Raises never lower the tier."""
        tier, reason = base
        for t, why in raises:
            if t and self.rank(t) > self.rank(tier):
                tier, reason = t, f"{reason}; raised to {t} by {why}"
        return tier, reason

    # ------------------------------------------------------------ risk
    def risk(self, pii_type: Optional[str], folder_class: str, masked_in_source: bool,
             bulk: bool) -> tuple[float, str]:
        weight = float(self.item(pii_type).get("weight", DEFAULT_ITEM["weight"]))
        exposure = float(self.exposure.get(folder_class, self.exposure.get("other", 0.6)))
        state = float(self.state["masked_in_source" if masked_in_source else "unmasked"])
        volume = float(self.volume.get("bulk_multiplier", 1.0)) if bulk else 1.0
        score = round(min(100.0, 100.0 * weight * exposure * state * volume), 1)
        return score, self.band(score)

    def band(self, score: float) -> str:
        if score >= self.risk_bands["high"]:
            return "high"
        if score >= self.risk_bands["medium"]:
            return "medium"
        return "low"

    def suggested_actions(self, tier: str) -> list[str]:
        return list(self.actions.get(tier, []))


def _read(path_or_text: str | Path) -> str:
    if isinstance(path_or_text, Path):
        return path_or_text.read_text(encoding="utf-8")
    s = str(path_or_text)
    if "\n" not in s and s.endswith((".yaml", ".yml")) and Path(s).exists():
        return Path(s).read_text(encoding="utf-8")
    return s


def load_lexicon(path_or_text: str | Path) -> dict[str, Any]:
    return yaml.safe_load(_read(path_or_text)) or {}


def load_policy(path_or_text: str | Path, lexicon: Optional[dict[str, Any] | str | Path] = None) -> Policy:
    text = _read(path_or_text)
    raw = yaml.safe_load(text)
    lex = raw.get("lexicon") or {}
    if lexicon is not None:
        lex = lexicon if isinstance(lexicon, dict) else load_lexicon(lexicon)
    return Policy(
        version=str(raw["version"]),
        tiers=list(raw["tiers"]),
        thresholds={k: float(v) for k, v in raw["thresholds"].items()},
        items=raw.get("items", {}),
        documents=raw.get("documents", {}) or {},
        markings=raw.get("markings_raise_to", {}) or {},
        exposure=raw.get("exposure", {}),
        state=raw.get("state", {"unmasked": 1.0, "masked_in_source": 0.2}),
        volume=raw.get("volume", {"bulk_multiplier": 1.0}),
        risk_bands=raw.get("risk_bands", {"high": 70, "medium": 40}),
        actions={k: list(v or []) for k, v in (raw.get("actions") or {}).items()},
        lexicon=lex,
        yaml_text=text,
    )


def combine_policy_text(policy_text: str, lexicon_text: str) -> str:
    """Policy YAML plus the lexicon nested under `lexicon:` — what GET /policy serves."""
    indented = "\n".join(("  " + ln) if ln.strip() else ln for ln in lexicon_text.splitlines())
    return policy_text.rstrip() + "\n\nlexicon:\n" + indented + "\n"
