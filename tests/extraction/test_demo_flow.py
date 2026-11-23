"""Walking skeleton step 3 (plan.md): the flow works with no API key.

Real synthetic invoices from S4's generator, one clean digital PDF per layout, go through upload,
the demo extractor (SHA-256 lookup in the manifest), S2 validation, S3 duplicate checks and the
confidence checks, including the text layer of each real layout.
"""

from __future__ import annotations

import hashlib
import json
import random
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import seed_demo
from app.accounts.models import Entity, GstRegistration
from app.domain.flags import Flag
from app.domain.models import Invoice
from app.domain.states import InvoiceState as S
from app.extraction.extractor import DemoExtractor
from app.extraction.models import InvoiceField
from evals.synth.build import DEFAULT_SEED, build
from evals.synth.generate import _render
from tests.conftest import login
from tests.test_upload import upload

TODAY = date(2026, 11, 24)


@pytest.fixture(scope="module")
def eval_set(tmp_path_factory):
    """One clean digital PDF per layout, with ground truth and a manifest, like gen-invoices."""
    out = tmp_path_factory.mktemp("evalset")
    invoices, regs = build(DEFAULT_SEED)
    rng = random.Random(DEFAULT_SEED + 1)
    chosen = {}
    for inv in invoices:
        if inv.fmt == "pdf_digital" and not inv.seeded_error and not inv.duplicate_kind:
            chosen.setdefault(inv.layout, inv)
    entries = []
    for inv in chosen.values():
        data = _render(inv, rng)
        inv.file = f"{inv.id}.pdf"
        inv.sha256 = hashlib.sha256(data).hexdigest()
        (out / inv.file).write_bytes(data)
        (out / f"{inv.id}.json").write_text(json.dumps(inv.ground_truth()))
        entries.append({"id": inv.id, "ground_truth": f"{inv.id}.json", "sha256": inv.sha256})
    entities = {}
    for r in regs:
        e = entities.setdefault(r.entity, {"key": r.entity, "name": r.name, "registrations": []})
        e["registrations"].append({"gstin": r.gstin, "state_code": r.state_code})
    manifest = out / "manifest.json"
    manifest.write_text(json.dumps({"entities": list(entities.values()), "invoices": entries}))
    return manifest, list(chosen.values())


def test_seed_demo_registers_the_eval_set_entities(app, eval_set):
    manifest, _ = eval_set
    extra = seed_demo.eval_set_entities(manifest)
    assert len(extra) == 3 and all(e["pan"].startswith("ZX") for e in extra)
    assert seed_demo.eval_set_entities(manifest.parent / "missing.json") == []
    with app.state.sessions() as db:
        seed_demo.seed(db, None, extra)
        db.commit()
        assert len(db.scalars(select(Entity)).all()) == 2 + 3
        gstins = set(db.scalars(select(GstRegistration.gstin)))
    manifest_gstins = {
        r["gstin"] for e in json.loads(manifest.read_text())["entities"] for r in e["registrations"]
    }
    assert manifest_gstins <= gstins


def test_clean_synthetic_invoices_go_touch_free_with_no_api_key(app, eval_set):
    manifest, invoices = eval_set
    assert len(invoices) == 5  # every layout
    with app.state.sessions() as db:
        seed_demo.seed(db, "a demo password for tests", seed_demo.eval_set_entities(manifest))
        db.commit()
    app.state.extraction.extractor = DemoExtractor(manifest)
    app.state.extraction.today = lambda: TODAY
    clerk = signed_in_demo(app)
    files = [
        (inv.file, (manifest.parent / inv.file).read_bytes(), "application/pdf") for inv in invoices
    ]
    assert upload(clerk, files).status_code == 200
    while app.state.worker.run_once():
        pass

    with app.state.sessions() as s:
        rows = s.scalars(select(Invoice).order_by(Invoice.id)).all()
        assert len(rows) == 5
        for row, inv in zip(rows, invoices, strict=True):
            low = s.scalars(
                select(InvoiceField).where(
                    InvoiceField.invoice_id == row.id, InvoiceField.confidence != "high"
                )
            ).all()
            open_flags = s.scalars(
                select(Flag).where(Flag.invoice_id == row.id, Flag.severity != "info")
            ).all()
            assert [(f.name, f.failed_checks) for f in low] == [], inv.layout
            assert [f.rule_id for f in open_flags] == [], inv.layout
            assert row.state is S.READY_FOR_APPROVAL and row.auto_reviewed, inv.layout
            assert row.invoice_number == inv.number
            assert row.supplier_gstin == inv.supplier.gstin


def signed_in_demo(app) -> TestClient:
    """seed-demo's clerk holds the clerk role on every demo entity, eval-set ones included."""
    client = TestClient(app)
    assert login(client, "clerk@demo.example", "a demo password for tests").status_code == 303
    return client
