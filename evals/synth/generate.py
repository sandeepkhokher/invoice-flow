"""Write the eval set: one document + one ground-truth JSON per invoice, plus manifest.json.

Usage: ``uv run python -m evals.synth [--out evals/data] [--seed N]`` (or ``make gen-invoices``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path

from evals.synth.build import DEFAULT_SEED, DUPLICATE_SEVERITY, build
from evals.synth.layouts import LAYOUTS
from evals.synth.model import Invoice, Party
from evals.synth.render import scan_params, to_pdf, to_scanned

DEFAULT_OUT = Path("evals/data")
EXTENSIONS = {"pdf_digital": "pdf", "pdf_scanned": "pdf", "png": "png", "jpeg": "jpg"}
OWNED_GLOBS = ("inv-*.pdf", "inv-*.png", "inv-*.jpg", "inv-*.json", "manifest.json")


def _render(inv: Invoice, rng: random.Random) -> bytes:
    cv = LAYOUTS[inv.layout].render(inv)
    inv.pages = len(cv.pages)
    if inv.fmt == "pdf_digital":
        return to_pdf(cv)
    inv.scan = scan_params(rng)
    return to_scanned(cv, inv.fmt, inv.scan, rng)


def expected_flags(inv: Invoice) -> list[dict]:
    flags = []
    if inv.seeded_error:
        flags.append(
            {"rule": inv.error_detail["rule"], "severity": inv.error_detail["expected_severity"]}
        )
    if inv.duplicate_kind:
        rule, severity = DUPLICATE_SEVERITY[inv.duplicate_kind]
        flags.append({"rule": rule, "severity": severity, "of": inv.duplicate_of})
    return flags


def generate(out: Path = DEFAULT_OUT, seed: int = DEFAULT_SEED) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    for pattern in OWNED_GLOBS:  # only files this generator owns
        for old in out.glob(pattern):
            old.unlink()

    invoices, regs = build(seed)
    rng = random.Random(seed + 1)  # separate stream for rendering noise
    by_id: dict[str, Invoice] = {}
    for inv in invoices:
        inv.file = f"{inv.id}.{EXTENSIONS[inv.fmt]}"
        if inv.duplicate_kind == "exact":
            orig = by_id[inv.duplicate_of]
            data = (out / orig.file).read_bytes()
            inv.pages, inv.scan = orig.pages, orig.scan
        else:
            data = _render(inv, rng)
        (out / inv.file).write_bytes(data)
        inv.sha256 = hashlib.sha256(data).hexdigest()
        (out / f"{inv.id}.json").write_text(
            json.dumps(inv.ground_truth(), indent=2, ensure_ascii=False) + "\n"
        )
        by_id[inv.id] = inv

    manifest = _manifest(invoices, regs, seed)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def _manifest(invoices: list[Invoice], regs: list[Party], seed: int) -> dict:
    clean = [i.id for i in invoices if not i.seeded_error and not i.duplicate_kind]
    entities: dict[str, dict] = {}
    for r in regs:
        e = entities.setdefault(r.entity, {"key": r.entity, "name": r.name, "registrations": []})
        e["registrations"].append({"gstin": r.gstin, "state_code": r.state_code})
    return {
        "generator": "evals.synth",
        "seed": seed,
        "synthetic": True,
        "count": len(invoices),
        "notes": (
            "Entirely synthetic. Ground truth is what is printed on the document. "
            "'clean' = no seeded error and not a seeded duplicate. Ingest in id order: "
            "every duplicate's original has a lower id. Rule ids are provisional names "
            "for the checks in gst-invoice-rules and FR-30..32."
        ),
        "entities": list(entities.values()),
        "subsets": {
            "clean": clean,
            "duplicates": [i.id for i in invoices if i.duplicate_kind],
            "seeded_errors": [i.id for i in invoices if i.seeded_error],
        },
        "invoices": [
            {
                "id": i.id,
                "file": i.file,
                "ground_truth": f"{i.id}.json",
                "sha256": i.sha256,
                "format": i.fmt,
                "layout": i.layout,
                "pages": i.pages,
                "supply_type": i.supply_type,
                "buyer_entity": i.buyer.entity,
                "clean": i.id in clean,
                "seeded_error": i.seeded_error,
                "duplicate_of": i.duplicate_of,
                "duplicate_kind": i.duplicate_kind,
                "expected_flags": expected_flags(i),
            }
            for i in invoices
        ],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Generate the synthetic invoice eval set.")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = ap.parse_args(argv)
    m = generate(args.out, args.seed)
    s = m["subsets"]
    print(
        f"wrote {m['count']} invoices to {args.out}/ (seed {m['seed']}): "
        f"{len(s['clean'])} clean, {len(s['duplicates'])} duplicates, "
        f"{len(s['seeded_errors'])} seeded errors"
    )
    return 0
