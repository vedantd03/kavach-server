"""Privacy invariants. Filled in once the corpus (T5) and server (T6) exist."""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LABELS = ROOT / "corpus" / "labels.jsonl"


def _raw_values() -> list[str]:
    if not LABELS.exists():
        pytest.skip("corpus/labels.jsonl not generated yet (T5)")
    return [json.loads(l)["value"] for l in LABELS.read_text(encoding="utf-8").splitlines() if l.strip()]


def test_privacy_placeholder():
    _raw_values()
