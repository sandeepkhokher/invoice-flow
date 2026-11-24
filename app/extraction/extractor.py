"""Extractors: a document in, an `ExtractedInvoice` out (spec FR-20, FR-22, §5.3).

Two implementations of one interface:
- `ClaudeExtractor` calls the Claude API (`client.beta.messages.parse` with the
  `ExtractedInvoice` schema, model `claude-opus-5-5`, explicit effort, PDF as a `document`
  block and images as `image` blocks, server-side refusal fallback, cached system prompt).
- `DemoExtractor` runs when `ANTHROPIC_API_KEY` is unset (walking skeleton, plan.md): it looks
  the file's SHA-256 up in the synthetic eval set's manifest (`make gen-invoices`) and returns its
  ground truth with high confidence. Any other file fails with a reason saying a key is needed.

Failures raise `ExtractionFailed` with a reason the clerk can read; reasons never contain
document content. Logs carry the invoice ID, model, token usage, latency and outcome only
(data-protection §5): never the document, the prompt or the response body.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import anthropic
from PIL import Image
from pydantic import ValidationError

from app.config import Settings
from app.extraction.prompt import SYSTEM_PROMPT, USER_INSTRUCTION
from app.extraction.schema import ExtractedInvoice
from app.intake.files import FileType

log = logging.getLogger("invoice_flow.extraction")

FALLBACK_BETA = "server-side-fallback-2026-07-01"
API_MAX_RETRIES = 3  # spec §5.3: 429 and 5xx retried by the SDK
SCHEMA_ATTEMPTS = 2  # schema-invalid output is retried once (spec §5.3)
NO_KEY_REASON = "No API key: set ANTHROPIC_API_KEY to extract real invoices"

IMAGE_MEDIA_TYPES = {
    FileType.PNG: "image/png",
    FileType.JPEG: "image/jpeg",
    FileType.WEBP: "image/webp",
}


class ExtractionFailed(Exception):
    """Extraction did not produce a usable result. `reason` is shown to the clerk."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Document:
    invoice_id: int
    data: bytes
    file_type: FileType
    sha256: str


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass(frozen=True)
class ExtractionResult:
    invoice: ExtractedInvoice
    extractor: str  # "claude" or "demo"
    model: str | None = None
    usage: Usage | None = None


class Extractor(Protocol):
    name: str

    def extract(self, doc: Document) -> ExtractionResult: ...


# --- Claude ---------------------------------------------------------------------------------


def _b64(data: bytes) -> str:
    return base64.standard_b64encode(data).decode("ascii")


def document_blocks(doc: Document) -> list[dict[str, Any]]:
    """The file as API content blocks: one `document` for a PDF, `image` blocks otherwise.

    TIFF is not an API image type, so each TIFF frame is sent as a PNG image.
    """
    if doc.file_type is FileType.PDF:
        return [
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": _b64(doc.data),
                },
            }
        ]
    if doc.file_type in IMAGE_MEDIA_TYPES:
        media_type = IMAGE_MEDIA_TYPES[doc.file_type]
        return [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": _b64(doc.data)},
            }
        ]
    blocks = []
    with Image.open(io.BytesIO(doc.data)) as img:
        for frame in range(getattr(img, "n_frames", 1)):
            img.seek(frame)
            out = io.BytesIO()
            img.convert("RGB").save(out, format="PNG")
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": _b64(out.getvalue()),
                    },
                }
            )
    return blocks


def _usage(resp: Any) -> Usage:
    u = getattr(resp, "usage", None)

    def n(name: str) -> int:
        return int(getattr(u, name, 0) or 0)

    return Usage(
        input_tokens=n("input_tokens"),
        output_tokens=n("output_tokens"),
        cache_read_input_tokens=n("cache_read_input_tokens"),
        cache_creation_input_tokens=n("cache_creation_input_tokens"),
    )


def _log_call(
    invoice_id: int, model: str, outcome: str, started: float, usage: Usage | None = None
) -> None:
    u = usage or Usage()
    log.info(
        "extraction call invoice=%s model=%s outcome=%s latency_ms=%d input_tokens=%d "
        "output_tokens=%d cache_read_tokens=%d cache_write_tokens=%d",
        invoice_id,
        model,
        outcome,
        round((time.monotonic() - started) * 1000),
        u.input_tokens,
        u.output_tokens,
        u.cache_read_input_tokens,
        u.cache_creation_input_tokens,
    )


def _api_error_reason(exc: Exception) -> str:
    if isinstance(exc, anthropic.AuthenticationError):
        return "The Claude API rejected the API key. Ask an admin to check ANTHROPIC_API_KEY."
    if isinstance(exc, anthropic.PermissionDeniedError):
        return "The API key is not allowed to use the extraction model."
    if isinstance(exc, anthropic.RateLimitError):
        return "The Claude API rate limit was reached. Retry the extraction later."
    if isinstance(exc, anthropic.APIStatusError):
        return f"The Claude API returned an error (HTTP {exc.status_code}). Retry the extraction."
    if isinstance(exc, anthropic.APIConnectionError):
        return "Could not reach the Claude API. Retry the extraction later."
    return "The Claude API call failed. Retry the extraction."


class ClaudeExtractor:
    name = "claude"

    def __init__(
        self,
        client: anthropic.Anthropic,
        *,
        model: str = "claude-opus-5-5",
        effort: str = "medium",
        max_tokens: int = 16000,
    ) -> None:
        self.client = client
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens

    def request(self, doc: Document) -> dict[str, Any]:
        """The keyword arguments of the `messages.parse` call (no secrets; tests inspect it)."""
        return {
            "model": self.model,
            "max_tokens": self.max_tokens,
            # Static and first in the request, so it is cached across invoices.
            "system": [
                {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
            ],
            "messages": [
                {
                    "role": "user",
                    "content": [*document_blocks(doc), {"type": "text", "text": USER_INSTRUCTION}],
                }
            ],
            "output_format": ExtractedInvoice,
            "output_config": {"effort": self.effort},
            "betas": [FALLBACK_BETA],
            "fallbacks": "default",
        }

    def extract(self, doc: Document) -> ExtractionResult:
        kwargs = self.request(doc)
        for _attempt in range(SCHEMA_ATTEMPTS):
            started = time.monotonic()
            try:
                resp = self.client.beta.messages.parse(**kwargs)
            except ValidationError:
                _log_call(doc.invoice_id, self.model, "schema_invalid", started)
                continue
            except anthropic.AnthropicError as exc:
                _log_call(doc.invoice_id, self.model, f"api_error:{type(exc).__name__}", started)
                raise ExtractionFailed(_api_error_reason(exc)) from None
            usage = _usage(resp)
            if resp.stop_reason == "refusal":
                _log_call(doc.invoice_id, self.model, "refusal", started, usage)
                raise ExtractionFailed(
                    "The model declined to read this document. Enter the fields by hand."
                )
            parsed = resp.parsed_output
            if parsed is None:  # no JSON text, e.g. cut off at max_tokens
                _log_call(
                    doc.invoice_id, self.model, f"no_output:{resp.stop_reason}", started, usage
                )
                continue
            _log_call(doc.invoice_id, self.model, "ok", started, usage)
            return ExtractionResult(
                invoice=parsed, extractor=self.name, model=resp.model, usage=usage
            )
        raise ExtractionFailed(
            f"The model's answer did not match the invoice schema after {SCHEMA_ATTEMPTS} "
            "attempts. Retry the extraction or enter the fields by hand."
        )


# --- Demo (no API key) ----------------------------------------------------------------------


def _text(value: Any) -> dict[str, Any]:
    present = value not in (None, "")
    return {
        "value": value if present else None,
        "confidence": "high",
        "page": 1 if present else None,
    }


def ground_truth_to_extraction(gt: dict[str, Any]) -> ExtractedInvoice:
    """The eval set's ground truth (evals/synth/model.py) as a high-confidence extraction."""

    def party(p: dict[str, Any]) -> dict[str, Any]:
        return {k: _text(p.get(k)) for k in ("name", "address", "gstin")}

    lines = [
        {
            "description": _text(ln.get("description")),
            "hsn_sac": _text(ln.get("hsn")),
            **{
                k: _text(ln.get(k))
                for k in (
                    "quantity",
                    "unit_price",
                    "discount",
                    "taxable_value",
                    "rate",
                    "cgst",
                    "sgst",
                    "utgst",
                    "igst",
                )
            },
            "cess": _text(ln.get("cess")),
        }
        for ln in gt.get("line_items", [])
    ]
    ts = gt.get("tax_summary", {})
    bank = gt.get("bank") or {}
    has_qr = bool(gt.get("has_qr"))
    return ExtractedInvoice.model_validate(
        {
            "invoice_number": _text(gt.get("invoice_number")),
            "invoice_date": _text(gt.get("invoice_date")),
            "supplier": party(gt.get("supplier", {})),
            "buyer": party(gt.get("buyer", {})),
            "place_of_supply": _text((gt.get("place_of_supply") or {}).get("state_code")),
            "reverse_charge": {
                "value": bool(gt.get("reverse_charge")),
                "confidence": "high",
                "page": None,
            },
            "line_items": lines,
            "tax_summary": {
                "subtotal": _text(ts.get("taxable_total")),
                **{k: _text(ts.get(k)) for k in ("cgst", "sgst", "utgst", "igst", "cess")},
                "round_off": _text(ts.get("round_off")),
                "grand_total": _text(ts.get("grand_total")),
            },
            "bank": {
                k: _text(bank.get(k))
                for k in ("account_name", "account_number", "ifsc", "bank_name")
            },
            "irn": _text(gt.get("irn")),
            "has_signed_qr": {"value": has_qr, "confidence": "high", "page": 1 if has_qr else None},
        }
    )


class DemoExtractor:
    """Ground truth for the synthetic eval set's files; everything else fails (no API key)."""

    name = "demo"

    def __init__(self, manifest: Path) -> None:
        self.manifest = manifest

    def _ground_truth_path(self, sha256: str) -> Path | None:
        try:
            manifest = json.loads(self.manifest.read_text())
        except (OSError, ValueError):
            return None
        for entry in manifest.get("invoices", []):
            if entry.get("sha256") == sha256:
                return self.manifest.parent / entry["ground_truth"]
        return None

    def extract(self, doc: Document) -> ExtractionResult:
        started = time.monotonic()
        path = self._ground_truth_path(doc.sha256)
        if path is None:
            _log_call(doc.invoice_id, "demo", "unknown_file", started)
            raise ExtractionFailed(NO_KEY_REASON)
        try:
            invoice = ground_truth_to_extraction(json.loads(path.read_text()))
        except (OSError, ValueError):
            _log_call(doc.invoice_id, "demo", "bad_ground_truth", started)
            raise ExtractionFailed(NO_KEY_REASON) from None
        _log_call(doc.invoice_id, "demo", "ok", started)
        return ExtractionResult(invoice=invoice, extractor=self.name, model=None)


def make_extractor(settings: Settings) -> Extractor:
    """`ClaudeExtractor` when `ANTHROPIC_API_KEY` is set, else `DemoExtractor`."""
    if settings.anthropic_api_key is not None and settings.anthropic_api_key.get_secret_value():
        client = anthropic.Anthropic(
            api_key=settings.anthropic_api_key.get_secret_value(), max_retries=API_MAX_RETRIES
        )
        return ClaudeExtractor(
            client,
            model=settings.extraction_model,
            effort=settings.extraction_effort,
            max_tokens=settings.extraction_max_tokens,
        )
    return DemoExtractor(settings.demo_manifest)
