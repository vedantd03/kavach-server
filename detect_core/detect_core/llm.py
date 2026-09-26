"""Gemini client: key-pool rotation, JSON-schema output, disk cache, one trace per call.

`google.genai` is imported lazily so detect_core works without the [llm] extra.
Keys are never logged; traces carry `key_index`. The LLM only ever receives masked snippets.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from pydantic import BaseModel, ValidationError

from .contracts import DocView, OcrResult, VerifyItem, VerifyVerdict

log = logging.getLogger("detect_core.llm")

MAX_ITEMS_PER_CALL = 15
COOLDOWN_SEC = 60.0
BACKOFFS = (2.0, 4.0, 8.0)
MAX_ATTEMPTS = 6
DEFAULT_MODEL = "gemini-3.5-flash-lite"

TraceFn = Callable[[dict[str, Any]], None]


class LLMUnavailable(RuntimeError):
    pass


# ---------------------------------------------------------------- key pool
def keys_from_env() -> list[str]:
    raw = os.environ.get("GEMINI_API_KEYS", "")
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        i = 1
        while os.environ.get(f"GEMINI_API_KEY_{i}"):
            keys.append(os.environ[f"GEMINI_API_KEY_{i}"].strip())
            i += 1
    if not keys and os.environ.get("GEMINI_API_KEY"):
        keys.append(os.environ["GEMINI_API_KEY"].strip())
    return keys


class KeyPool:
    """Round-robin over keys; a rate-limited key cools down for COOLDOWN_SEC."""

    def __init__(self, keys: list[str], cooldown: float = COOLDOWN_SEC,
                 clock: Callable[[], float] = time.monotonic):
        self._keys = list(keys)
        self._cool_until = [0.0] * len(keys)
        self._next = 0
        self._cooldown = cooldown
        self._clock = clock
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls) -> "KeyPool":
        return cls(keys_from_env())

    def __len__(self) -> int:
        return len(self._keys)

    def acquire(self, force: bool = False) -> Optional[tuple[int, str]]:
        """Next available (index, key). None if all are cooling (unless force: then the key
        whose cooldown ends first)."""
        with self._lock:
            n = len(self._keys)
            if n == 0:
                return None
            now = self._clock()
            for step in range(n):
                i = (self._next + step) % n
                if self._cool_until[i] <= now:
                    self._next = (i + 1) % n
                    return i, self._keys[i]
            if force:
                i = min(range(n), key=lambda j: self._cool_until[j])
                self._next = (i + 1) % n
                return i, self._keys[i]
            return None

    def cool(self, index: int) -> None:
        with self._lock:
            self._cool_until[index] = self._clock() + self._cooldown


# ---------------------------------------------------------------- response schemas
class _Verdict(BaseModel):
    candidate_id: str
    is_sensitive: bool
    type: str
    category: str
    individual_or_business: str
    confidence: float
    reason: str


class _VerifyOut(BaseModel):
    verdicts: list[_Verdict]


class _DocOut(BaseModel):
    doc_type: str
    markings: list[str]
    suggested_tier: str
    confidence: float
    reason: str


class _OcrOut(BaseModel):
    text: str
    confidence: float


for _m in (_Verdict, _VerifyOut, _DocOut, _OcrOut):
    _m.model_rebuild()

DOC_TYPES = [
    "customer_export", "kyc_document", "invoice", "vendor_bill", "purchase_order", "payroll",
    "board_minutes", "chat_export", "support_tickets", "email", "config_file", "contract",
    "resume", "memo", "report", "other",
]

_TIER_DEFS = """Sensitivity tiers (highest first):
- restricted: bulk personal identifiers, secrets/credentials, KYC documents, board papers, payroll.
- confidential: an individual's identifiers (Aadhaar, individual PAN, bank account, UPI ID).
- internal: business identifiers (GSTIN, company PAN), work contact details.
- public: nothing sensitive."""


def _verify_system(policy: Any = None) -> str:
    types = ", ".join(sorted(policy.items)) if policy is not None else (
        "AADHAAR, PAN_INDIVIDUAL, PAN_BUSINESS, GSTIN, UPI_ID, BANK_ACCOUNT, MOBILE_IN, EMAIL, SECRET")
    return f"""You are a data-protection reviewer for an Indian company. You judge candidate
identifiers found in files on employee laptops. Item types: {types}.
{_TIER_DEFS}

Each item gives a candidate token like [CANDIDATE type=AADHAAR len=12 checksum=pass] and a masked
snippet around it. Snippets are masked: other long digit runs are X, emails are [EMAIL], other
detected identifiers appear as [TYPE]. You never see the candidate's digits; do not ask for them.
Text may be English, Hindi (Devanagari) or Hinglish (e.g. "mera aadhar no hai", "PAN bhej diya").

For every item decide:
- is_sensitive: true if the candidate really is the stated identifier type belonging to a person or
  business; false if it is a look-alike (invoice/order/UTR/transaction/reference/tracking/PO number,
  phone of a company helpline, sample/dummy value).
- type: the identifier type you believe it is (one of the item types, or NONE).
- category: personal | business | secret.
- individual_or_business: whose data it is: individual | business | unknown.
- confidence: 0..1.
- reason: one short sentence citing the context cue. Never include digits.
Return JSON {{"verdicts": [...]}} with one verdict per candidate_id, in order."""


def _doc_system(policy: Any = None) -> str:
    return f"""You classify a document from its first characters (masked: long digit runs are X,
emails are [EMAIL], identifiers are [TYPE] tokens) and its filename.
Allowed doc_type values: {", ".join(DOC_TYPES)}.
{_TIER_DEFS}
markings: any protective markings visible in the text, verbatim (e.g. "STRICTLY CONFIDENTIAL",
"CONFIDENTIAL", "INTERNAL USE ONLY"); empty list if none.
suggested_tier: one of restricted, confidential, internal, public.
Return JSON with doc_type, markings, suggested_tier, confidence (0..1), reason (one sentence, no digits)."""


_OCR_PROMPT = """Transcribe all text in this image exactly as printed, line by line.
Preserve every digit, letter and Devanagari character; keep digit grouping and line breaks.
Do not summarise, translate or correct. Return JSON {"text": "...", "confidence": 0..1} where
confidence is your own estimate of transcription accuracy."""


def _token_type(token: str) -> str:
    m = re.search(r"type=([A-Z_]+)", token)
    return m.group(1) if m else "UNKNOWN"


def unavailable_verdict(item: VerifyItem, category: str = "personal") -> VerifyVerdict:
    return VerifyVerdict(candidate_id=item.candidate_id, is_sensitive=False,
                         type=_token_type(item.candidate_token), category=category,  # type: ignore[arg-type]
                         individual_or_business="unknown", confidence=0.0, reason="llm_unavailable")


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


# ---------------------------------------------------------------- client
class GeminiClient:
    def __init__(self, pool: Optional[KeyPool] = None, model: Optional[str] = None,
                 cache_dir: Optional[str | Path] = None, trace: Optional[TraceFn] = None,
                 policy: Any = None, sleep: Callable[[float], None] = time.sleep):
        self.pool = pool or KeyPool.from_env()
        self.model = model or os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)
        cache_root = cache_dir or os.environ.get("LLM_CACHE_DIR") or ".cache"
        self.cache_dir = Path(cache_root) / "llm"
        self.trace = trace
        self.policy = policy
        self._sleep = sleep
        self._clients: dict[int, Any] = {}
        self.use_cache = True

    # ------------------------------------------------------------ plumbing
    def _client(self, index: int, key: str) -> Any:
        if index not in self._clients:
            from google import genai  # lazy
            self._clients[index] = genai.Client(api_key=key)
        return self._clients[index]

    def _cache_get(self, key: str) -> Optional[dict]:
        p = self.cache_dir / f"{key}.json"
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
        return None

    def _cache_put(self, key: str, data: dict) -> None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            (self.cache_dir / f"{key}.json").write_text(json.dumps(data), encoding="utf-8")
        except OSError:
            log.warning("llm cache write failed")

    def _emit(self, **row: Any) -> None:
        if self.trace:
            try:
                self.trace(row)
            except Exception:  # a trace failure must never break detection
                log.exception("trace callback failed")

    @staticmethod
    def _is_rate_limit(exc: Exception) -> bool:
        code = getattr(exc, "code", None)
        status = str(getattr(exc, "status", "") or "")
        return code == 429 or "RESOURCE_EXHAUSTED" in status or "RESOURCE_EXHAUSTED" in type(exc).__name__

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        code = getattr(exc, "code", None)
        if isinstance(code, int):
            return code in (408, 429, 500, 502, 503, 504)
        return True  # network errors etc.

    def _generate(self, endpoint: str, contents: list[Any], system: Optional[str],
                  schema: type[BaseModel], items: int, device_id: Optional[str],
                  cache_key: Optional[str] = None,
                  decisions: Optional[Callable[[BaseModel], dict]] = None) -> BaseModel:
        if not self.use_cache:
            cache_key = None
        if cache_key:
            hit = self._cache_get(cache_key)
            if hit is not None:
                try:
                    parsed = schema.model_validate(hit)
                    self._emit(endpoint=endpoint, model=self.model, key_index=None, latency_ms=0,
                               tokens_in=0, tokens_out=0, items=items, status="cached", attempts=0,
                               decisions=decisions(parsed) if decisions else {}, device_id=device_id)
                    return parsed
                except ValidationError:
                    pass

        from google.genai import types  # lazy

        config = types.GenerateContentConfig(
            system_instruction=system, response_mime_type="application/json",
            response_schema=schema, temperature=0.0,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
        backoffs = list(BACKOFFS)
        attempts = 0
        last_idx: Optional[int] = None
        t0 = time.monotonic()
        while attempts < MAX_ATTEMPTS:
            got = self.pool.acquire()
            if got is None:
                if not backoffs:
                    break
                self._sleep(backoffs.pop(0))
                got = self.pool.acquire(force=True)
                if got is None:
                    break
            idx, key = got
            last_idx = idx
            attempts += 1
            try:
                resp = self._client(idx, key).models.generate_content(
                    model=self.model, contents=contents, config=config)
                parsed = resp.parsed if isinstance(resp.parsed, schema) else schema.model_validate_json(resp.text)
                usage = getattr(resp, "usage_metadata", None)
                self._emit(endpoint=endpoint, model=self.model, key_index=idx,
                           latency_ms=int((time.monotonic() - t0) * 1000),
                           tokens_in=getattr(usage, "prompt_token_count", None) or 0,
                           tokens_out=getattr(usage, "candidates_token_count", None) or 0,
                           items=items, status="ok", attempts=attempts,
                           decisions=decisions(parsed) if decisions else {}, device_id=device_id)
                if cache_key:
                    self._cache_put(cache_key, parsed.model_dump())
                return parsed
            except Exception as exc:  # noqa: BLE001 — classify and rotate
                # Log only the exception type and code: messages can echo request data.
                code = getattr(exc, "code", None)
                log.warning("llm %s failed on key_index=%s: %s code=%s", endpoint, idx,
                            type(exc).__name__, code)
                if self._is_rate_limit(exc):
                    self.pool.cool(idx)
                    continue
                if isinstance(exc, (ValidationError, ValueError)) or self._is_retryable(exc):
                    continue
                break
        self._emit(endpoint=endpoint, model=self.model, key_index=last_idx,
                   latency_ms=int((time.monotonic() - t0) * 1000), tokens_in=0, tokens_out=0,
                   items=items, status="error", attempts=max(attempts, 1), decisions={},
                   device_id=device_id)
        raise LLMUnavailable(f"{endpoint}: all attempts failed")

    def _key(self, *parts: str) -> str:
        return hashlib.sha256("\x1f".join((self.model,) + parts).encode("utf-8")).hexdigest()

    # ------------------------------------------------------------ public API
    def verify_batch(self, items: list[VerifyItem], device_id: Optional[str] = None) -> list[VerifyVerdict]:
        out: list[VerifyVerdict] = []
        for i in range(0, len(items), MAX_ITEMS_PER_CALL):
            out.extend(self._verify_chunk(items[i:i + MAX_ITEMS_PER_CALL], device_id))
        return out

    def _category_for(self, t: str) -> str:
        if self.policy is not None:
            return self.policy.category(t)
        return "secret" if t == "SECRET" else "personal"

    def _verify_chunk(self, items: list[VerifyItem], device_id: Optional[str]) -> list[VerifyVerdict]:
        system = _verify_system(self.policy)
        payload = json.dumps([it.model_dump() for it in items], ensure_ascii=False)
        prompt = f"Judge these candidates:\n{payload}"
        try:
            res = self._generate("verify", [prompt], system, _VerifyOut, len(items), device_id,
                                 cache_key=self._key("verify", system, prompt),
                                 decisions=lambda r: {
                                     "sensitive": sum(v.is_sensitive for v in r.verdicts),
                                     "not_sensitive": sum(not v.is_sensitive for v in r.verdicts)})
        except LLMUnavailable:
            return [unavailable_verdict(it, self._category_for(_token_type(it.candidate_token))) for it in items]
        by_id = {v.candidate_id: v for v in res.verdicts}  # type: ignore[attr-defined]
        verdicts = []
        for it in items:
            v = by_id.get(it.candidate_id)
            if v is None:
                verdicts.append(unavailable_verdict(it, self._category_for(_token_type(it.candidate_token))))
                continue
            cat = v.category if v.category in ("personal", "business", "secret") else self._category_for(v.type)
            holder = v.individual_or_business if v.individual_or_business in ("individual", "business") else "unknown"
            verdicts.append(VerifyVerdict(
                candidate_id=it.candidate_id, is_sensitive=v.is_sensitive, type=v.type,
                category=cat, individual_or_business=holder,  # type: ignore[arg-type]
                confidence=_clamp01(v.confidence), reason=v.reason[:300]))
        return verdicts

    def classify_document(self, masked_head: str, filename: str,
                          device_id: Optional[str] = None) -> DocView:
        system = _doc_system(self.policy)
        prompt = f"Filename: {filename}\n---\n{masked_head[:1500]}"
        try:
            r = self._generate("classify", [prompt], system, _DocOut, 1, device_id,
                               cache_key=self._key("classify", system, prompt),
                               decisions=lambda r: {"doc_type": r.doc_type})
        except LLMUnavailable:
            return DocView(doc_type="unknown", markings=[], suggested_tier=None, confidence=0.0,
                           reason="llm_unavailable")
        tier = r.suggested_tier if r.suggested_tier in ("restricted", "confidential", "internal", "public") else None  # type: ignore[attr-defined]
        doc_type = r.doc_type if r.doc_type in DOC_TYPES else "other"  # type: ignore[attr-defined]
        return DocView(doc_type=doc_type, markings=list(r.markings)[:10], suggested_tier=tier,  # type: ignore[attr-defined]
                       confidence=_clamp01(r.confidence), reason=r.reason[:300])  # type: ignore[attr-defined]

    def ocr(self, image_bytes: bytes, mime: str, device_id: Optional[str] = None) -> OcrResult:
        """Plain-text transcription. Never cached. Raises LLMUnavailable on final failure."""
        from google.genai import types  # lazy
        part = types.Part.from_bytes(data=image_bytes, mime_type=mime)
        r = self._generate("ocr", [part, _OCR_PROMPT], None, _OcrOut, 1, device_id,
                           decisions=lambda r: {"chars": len(r.text)})
        return OcrResult(text=r.text, confidence=_clamp01(r.confidence), pages=1)  # type: ignore[attr-defined]


# ---------------------------------------------------------------- module-level helpers
_default: Optional[GeminiClient] = None


def default_client() -> GeminiClient:
    global _default
    if _default is None:
        _default = GeminiClient()
    return _default


def verify_batch(items: list[VerifyItem]) -> list[VerifyVerdict]:
    return default_client().verify_batch(items)


def classify_document(masked_head: str, filename: str) -> DocView:
    return default_client().classify_document(masked_head, filename)


def ocr(image_bytes: bytes, mime: str) -> OcrResult:
    return default_client().ocr(image_bytes, mime)


def _selftest() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    logging.basicConfig(level=logging.INFO)
    rows: list[dict] = []
    client = GeminiClient(trace=rows.append)
    client.use_cache = False
    print(f"model={client.model} keys={len(client.pool)}")
    items = [
        VerifyItem(candidate_id="c1", candidate_token="[CANDIDATE type=AADHAAR len=12 checksum=pass]",
                   snippet_masked="[12/09/26, 10:14] Ravi: bhai mera aadhar no hai [CANDIDATE type=AADHAAR len=12 checksum=pass] please KYC update kar do"),
        VerifyItem(candidate_id="c2", candidate_token="[CANDIDATE type=AADHAAR len=12 checksum=pass]",
                   snippet_masked="TAX INVOICE Invoice No: [CANDIDATE type=AADHAAR len=12 checksum=pass] Date: XX/XX/XXXX Amount payable Rs XXXX.00"),
        VerifyItem(candidate_id="c3", candidate_token="[CANDIDATE type=PAN_BUSINESS len=10 checksum=pass]",
                   snippet_masked="Acme Traders Pvt Ltd, GSTIN [GSTIN], company PAN [CANDIDATE type=PAN_BUSINESS len=10 checksum=pass], Bengaluru"),
    ]
    for it in items:  # one call each so key rotation is visible
        for v in client._verify_chunk([it], "selftest"):
            print(f"{v.candidate_id}: sensitive={v.is_sensitive} type={v.type} holder={v.individual_or_business} "
                  f"conf={v.confidence:.2f} reason={v.reason}")
    print("key_index per call:", [r.get("key_index") for r in rows], "status:", [r.get("status") for r in rows])


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
    else:
        print("usage: python -m detect_core.llm --selftest")
