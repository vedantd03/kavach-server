"""Detection pipeline: normalise -> recognize -> validate -> score -> route -> tier -> Finding.

No file I/O: the policy (with lexicon) is passed in. Raw values live only in memory here;
Findings carry masked_value + value_hash.
"""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import Callable, Literal, Optional

from .context import score
from .contracts import (
    Candidate, Chunk, DetectRequest, DetectResponse, DocView, FileMeta, Finding, VerifyItem,
    VerifyVerdict, finding_id_for, utc_now,
)
from .masking import candidate_token, mask_snippet, mask_text, mask_value, value_hash
from .normalise import normalise, to_original_span
from .policy import Policy
from .recognizers import recognize

Mode = Literal["server", "local", "rules_only"]
Verifier = Callable[[list[VerifyItem]], list[VerifyVerdict]]
DocClassifier = Callable[[str, str], DocView]

VERIFY_BATCH = 15
DOC_HEAD_CHARS = 1500
VERDICT_MIN_CONF = 0.5
# Tie-break when candidates of different types overlap (higher wins).
TYPE_PRIORITY = {"SECRET": 9, "AADHAAR": 8, "GSTIN": 7, "PAN_INDIVIDUAL": 6, "PAN_BUSINESS": 6,
                 "BANK_ACCOUNT": 5, "UPI_ID": 4, "MOBILE_IN": 3, "EMAIL": 2}
DOC_WEIGHT = {"restricted": 1.0, "confidential": 0.6, "internal": 0.3, "public": 0.0}


@dataclass
class _Scored:
    cand: Candidate
    chunk: Chunk
    chunk_index: int
    norm: str
    omap: list[int]
    score: float
    reason: str
    all_cands: list[Candidate] = field(default_factory=list)


@dataclass
class _Accepted:
    s: _Scored
    confidence: float
    decided_by: str
    reason: str
    holder: Optional[str] = None


def _chunk_index(chunk: Chunk, fallback: int) -> int:
    try:
        return int(chunk.chunk_id.rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return fallback


def _location(s: _Scored) -> str:
    c = s.chunk
    orig_start, _ = to_original_span(s.omap, s.cand.start, s.cand.end)
    if c.row is not None and c.column is not None:
        line = c.text.count("\n", 0, orig_start)
        header = (c.column_header or "").replace(";", ",")
        return f"sheet={c.page or 1};row={c.row + line};column={c.column};header={header}"
    loc = f"chunk={s.chunk_index};char={orig_start}"
    if c.page is not None:
        loc = f"page={c.page};" + loc
    if c.ocr_confidence is not None:
        loc += ";ocr=1"
    return loc


def _resolve_overlaps(scored: list[_Scored]) -> list[_Scored]:
    """Among overlapping candidates of different types keep the best-scored one."""
    ranked = sorted(scored, key=lambda s: (s.score, TYPE_PRIORITY.get(s.cand.type, 0),
                                           s.cand.end - s.cand.start), reverse=True)
    kept: list[_Scored] = []
    for s in ranked:
        if any(s.cand.start < k.cand.end and k.cand.start < s.cand.end for k in kept):
            continue
        kept.append(s)
    return sorted(kept, key=lambda s: s.cand.start)


def _masked_head(chunks: list[tuple[Chunk, str, list[Candidate]]]) -> str:
    """First ~1,500 chars of the file with identifiers -> [TYPE], emails and digit runs masked."""
    parts: list[str] = []
    total = 0
    for _, norm, cands in chunks:
        pos, buf = 0, []
        for c in sorted(cands, key=lambda c: c.start):
            if c.start < pos:
                continue
            buf.append(mask_text(norm[pos:c.start]))
            buf.append(f"[{c.type}]")
            pos = c.end
        buf.append(mask_text(norm[pos:]))
        piece = "".join(buf)
        parts.append(piece)
        total += len(piece)
        if total >= DOC_HEAD_CHARS:
            break
    return "\n".join(parts)[:DOC_HEAD_CHARS]


def _default_holder(policy: Policy, pii_type: str, cand: Candidate) -> str:
    h = cand.validator_notes.get("holder")
    if h in ("individual", "business"):
        return h
    cat = policy.category(pii_type)
    return {"personal": "individual", "business": "business"}.get(cat, "unknown")


def detect(request: DetectRequest, policy: Policy, mode: Mode = "server",
           verifier: Optional[Verifier] = None, doc_classifier: Optional[DocClassifier] = None,
           verifier_label: Optional[str] = None) -> DetectResponse:
    t0 = time.monotonic()
    confirm = policy.thresholds["confirm"]
    drop = policy.thresholds["drop"]
    label = verifier_label or ("server" if mode == "server" else "local_model")
    stats = defaultdict(int)
    stats["files"] = len(request.files)
    stats["chunks"] = len(request.chunks)

    files: dict[str, FileMeta] = {f.file_path: f for f in request.files}
    by_file: dict[str, list[Chunk]] = defaultdict(list)
    for ch in request.chunks:
        if ch.file_path in files and files[ch.file_path].status == "ok":
            by_file[ch.file_path].append(ch)

    # ---- 1. per chunk: normalise, recognize, score, resolve overlaps
    file_scored: dict[str, list[_Scored]] = defaultdict(list)
    file_norm: dict[str, list[tuple[Chunk, str, list[Candidate]]]] = defaultdict(list)
    for path, chunks in by_file.items():
        for i, ch in enumerate(chunks):
            norm, omap = normalise(ch.text)
            cands = recognize(norm)
            file_norm[path].append((ch, norm, cands))
            stats["candidates"] += len(cands)
            idx = _chunk_index(ch, i)
            scored = []
            for c in cands:
                sc, why = score(c, norm, ch.column_header, None, policy.lexicon)
                scored.append(_Scored(c, ch, idx, norm, omap, sc, why, cands))
            file_scored[path].extend(_resolve_overlaps(scored))

    # ---- 2. per file document view (server mode only)
    doc_views: dict[str, Optional[DocView]] = {}
    for path in by_file:
        dv = None
        if mode == "server" and doc_classifier is not None:
            name = path.replace("\\", "/").rsplit("/", 1)[-1]
            dv = doc_classifier(_masked_head(file_norm[path]), name)
            stats["llm_calls"] += 1
        doc_views[path] = dv

    # ---- 3. route
    accepted: dict[str, list[_Accepted]] = defaultdict(list)
    uncertain: list[_Scored] = []
    for path, scored in file_scored.items():
        for s in scored:
            if mode == "rules_only":
                if s.cand.validator_pass:
                    accepted[path].append(_Accepted(s, s.score, "rules", s.reason))
                    stats["confirmed_by_rules"] += 1
                else:
                    stats["dropped"] += 1
            elif s.score >= confirm:
                accepted[path].append(_Accepted(s, s.score, "rules", s.reason))
                stats["confirmed_by_rules"] += 1
            elif s.score <= drop:
                stats["dropped"] += 1
            else:
                uncertain.append(s)

    if uncertain and verifier is None:
        stats["pending"] += len(uncertain)
    elif uncertain:
        items = []
        for n, s in enumerate(uncertain):
            dv = doc_views.get(s.chunk.file_path)
            items.append(VerifyItem(
                candidate_id=f"c{n}", candidate_token=candidate_token(s.cand),
                snippet_masked=mask_snippet(s.norm, s.cand, s.all_cands),
                doc_type_hint=dv.doc_type if dv else None, column_header=s.chunk.column_header))
        verdicts: dict[str, VerifyVerdict] = {}
        for i in range(0, len(items), VERIFY_BATCH):
            for v in verifier(items[i:i + VERIFY_BATCH]):
                verdicts[v.candidate_id] = v
            stats["llm_calls"] += 1
        for n, s in enumerate(uncertain):
            v = verdicts.get(f"c{n}")
            if v is None or v.reason == "llm_unavailable":
                stats["pending"] += 1
            elif v.is_sensitive and v.confidence >= VERDICT_MIN_CONF:
                accepted[s.chunk.file_path].append(_Accepted(
                    s, round(v.confidence, 3), label, f"{v.reason} (rules: {s.reason})",
                    holder=v.individual_or_business))
                stats["verified_by_server" if label == "server" else "verified_by_local_model"] += 1
            else:
                stats["dropped"] += 1

    # ---- 4. build findings
    now = utc_now()
    findings: list[Finding] = []
    for path, meta in files.items():
        acc = accepted.get(path, [])
        dv = doc_views.get(path)
        # dedupe by (type, value_hash), first location wins, keep max confidence
        uniq: dict[tuple[str, str], tuple[_Accepted, str]] = {}
        for a in acc:
            vh = value_hash(a.s.cand.normalised)
            key = (a.s.cand.type, vh)
            if key not in uniq:
                uniq[key] = (a, vh)
            elif a.confidence > uniq[key][0].confidence:
                uniq[key] = (replace(uniq[key][0], confidence=a.confidence), vh)
        counts: dict[str, int] = defaultdict(int)
        for (t, _vh) in uniq:
            counts[t] += 1

        full_text = "\n".join(ch.text for ch in by_file.get(path, []))
        mark_tier, marking = policy.marking_tier(full_text)
        if dv:
            for m in dv.markings:
                t = policy.markings.get(m.upper().strip())
                if t and policy.rank(t) > policy.rank(mark_tier):
                    mark_tier, marking = t, m.upper().strip()
        d_tier, d_reason = policy.doc_tier(dv)
        raises = [(d_tier, d_reason), (mark_tier, f"marking '{marking}'" if marking else "")]
        doc_type = dv.doc_type if dv else "unknown"
        llm_tier = dv.suggested_tier if dv else None

        file_items: list[Finding] = []
        for (t, vh), (a, _) in uniq.items():
            c = a.s.cand
            tier, tier_reason = policy.final_tier(policy.item_tier(t, counts[t]), *raises)
            bulk = policy.is_bulk(t, counts[t])
            risk, band = policy.risk(t, meta.folder_class, c.masked_in_source, bulk)
            file_items.append(Finding(
                finding_id=finding_id_for(request.device_id, meta.file_hash, "item", t, vh),
                device_id=request.device_id, scan_id=request.scan_id, file_path=path,
                file_hash=meta.file_hash, folder_class=meta.folder_class, file_type=meta.file_type,
                location=_location(a.s), finding_kind="item", category=policy.category(t),  # type: ignore[arg-type]
                pii_type=t, doc_type=doc_type, masked_value=mask_value(t, c.normalised),
                value_hash=vh, holder=(a.holder if a.holder in ("individual", "business")
                                       else _default_holder(policy, t, c)),  # type: ignore[arg-type]
                masked_in_source=c.masked_in_source, confidence=round(a.confidence, 3),
                decided_by=a.decided_by, reason=a.reason[:500], llm_suggested_tier=llm_tier,  # type: ignore[arg-type]
                sensitivity_tier=tier, tier_reason=tier_reason, policy_version=policy.version,  # type: ignore[arg-type]
                risk_score=risk, risk_band=band, detected_at=now))  # type: ignore[arg-type]
        findings.extend(file_items)

        # one document finding per file whose tier is above public
        item_top = policy.highest(*(f.sensitivity_tier for f in file_items))
        doc_top = policy.highest(item_top, d_tier, mark_tier)
        if doc_top and doc_top != "public":
            reasons = []
            if file_items:
                reasons.append(f"{len(file_items)} item finding(s), highest {item_top}")
            if d_tier:
                reasons.append(d_reason)
            if mark_tier:
                reasons.append(f"marking '{marking}' -> {mark_tier}")
            exposure = policy.exposure.get(meta.folder_class, policy.exposure.get("other", 0.6))
            doc_risk = max([f.risk_score for f in file_items] +
                           [round(min(100.0, 100.0 * DOC_WEIGHT.get(doc_top, 0.0) * exposure), 1)])
            if file_items:
                cats = {f.category for f in file_items}
                category = "secret" if "secret" in cats else ("personal" if "personal" in cats else "business")
            elif dv and dv.doc_type in policy.documents:
                category = policy.documents[dv.doc_type]["category"]
            else:
                category = "business"
            holders = {f.holder for f in file_items}
            findings.append(Finding(
                finding_id=finding_id_for(request.device_id, meta.file_hash, "document", None, None),
                device_id=request.device_id, scan_id=request.scan_id, file_path=path,
                file_hash=meta.file_hash, folder_class=meta.folder_class, file_type=meta.file_type,
                location="document", finding_kind="document", category=category,  # type: ignore[arg-type]
                pii_type=None, doc_type=doc_type, masked_value=None, value_hash=None,
                holder=holders.pop() if len(holders) == 1 else "unknown",  # type: ignore[arg-type]
                masked_in_source=bool(file_items) and all(f.masked_in_source for f in file_items),
                confidence=round(max([f.confidence for f in file_items] + [dv.confidence if dv else 0.0]), 3),
                decided_by="server" if (dv and not file_items) else
                           ("server" if any(f.decided_by == "server" for f in file_items) else
                            (file_items[0].decided_by if file_items else "rules")),  # type: ignore[arg-type]
                reason=("; ".join(reasons) + (f". LLM: {dv.reason}" if dv and dv.reason else ""))[:500],
                llm_suggested_tier=llm_tier, sensitivity_tier=doc_top,  # type: ignore[arg-type]
                tier_reason="file tier = highest of " + ", ".join(reasons),
                policy_version=policy.version, risk_score=doc_risk,
                risk_band=policy.band(doc_risk), detected_at=now))  # type: ignore[arg-type]

    stats["latency_ms"] = int((time.monotonic() - t0) * 1000)
    base = {k: 0 for k in ("files", "chunks", "candidates", "confirmed_by_rules",
                           "verified_by_server", "dropped", "pending", "llm_calls", "latency_ms")}
    base.update(stats)
    return DetectResponse(findings=findings, pending=base["pending"], stats=base)
