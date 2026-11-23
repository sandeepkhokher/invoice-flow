"""S4: synthetic invoice generator and ground truth (spec §6, plan slice S4).

One test (or group) per acceptance criterion of issue #9.
"""

import hashlib
import json
import random
import re
from collections import Counter
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfReader

from evals.synth import catalog
from evals.synth.build import DEFAULT_SEED, renumber
from evals.synth.generate import generate, main
from evals.synth.gstin import (
    GSTIN_RE,
    SYNTHETIC_PAN_PREFIX,
    check_char,
    corrupt_gstin,
    is_valid_gstin,
    make_gstin,
)
from evals.synth.layouts import LAYOUTS, amount_in_words, group_indian
from evals.synth.model import edit_distance, financial_year, normalise_number

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    out = tmp_path_factory.mktemp("evalset")
    manifest = generate(out, DEFAULT_SEED)
    truths = {
        i["id"]: json.loads((out / i["ground_truth"]).read_text()) for i in manifest["invoices"]
    }
    return out, manifest, truths


def D(s: str) -> Decimal:
    return Decimal(s)


# ---------- AC1: make gen-invoices, ~60 invoices, fixed seed, reproducible ----------


def test_makefile_target_writes_to_evals_data():
    makefile = (ROOT / "Makefile").read_text()
    assert re.search(r"^gen-invoices:", makefile, re.M)
    assert "python -m evals.synth --out evals/data" in makefile


def test_writes_about_60_invoices_each_with_ground_truth(dataset):
    out, manifest, truths = dataset
    assert 55 <= manifest["count"] <= 70
    assert manifest["seed"] == DEFAULT_SEED
    for inv in manifest["invoices"]:
        assert (out / inv["file"]).is_file()
        assert (out / inv["ground_truth"]).is_file()
        assert truths[inv["id"]]["file"] == inv["file"]
        data = (out / inv["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == inv["sha256"] == truths[inv["id"]]["sha256"]


def test_same_seed_is_byte_identical_and_other_seed_differs(dataset, tmp_path):
    out, manifest, _ = dataset
    again = generate(tmp_path / "again", DEFAULT_SEED)
    assert again == manifest
    for inv in manifest["invoices"]:
        for name in (inv["file"], inv["ground_truth"]):
            assert (tmp_path / "again" / name).read_bytes() == (out / name).read_bytes(), name
    other = generate(tmp_path / "other", DEFAULT_SEED + 1)
    assert [i["sha256"] for i in other["invoices"]] != [i["sha256"] for i in manifest["invoices"]]


def test_cli_main_writes_the_set_and_replaces_only_its_own_files(tmp_path, capsys):
    keep = tmp_path / "README.md"
    keep.write_text("not ours")
    stale = tmp_path / "inv-999.json"
    stale.write_text("{}")
    assert main(["--out", str(tmp_path), "--seed", "7"]) == 0
    assert "invoices to" in capsys.readouterr().out
    assert keep.exists() and not stale.exists()
    assert (tmp_path / "manifest.json").is_file()


# ---------- AC2: layouts, digital PDFs, multi-page, scanned-style images ----------


def test_at_least_five_layouts_are_used(dataset):
    _, manifest, _ = dataset
    used = {i["layout"] for i in manifest["invoices"]}
    assert len(LAYOUTS) >= 5
    assert used == set(LAYOUTS)


def test_formats_include_digital_pdf_scanned_pdf_and_images(dataset):
    out, manifest, _ = dataset
    formats = Counter(i["format"] for i in manifest["invoices"])
    assert formats["pdf_digital"] >= 15
    assert formats["pdf_scanned"] >= 3
    assert formats["png"] + formats["jpeg"] >= 10
    for inv in manifest["invoices"]:
        head = (out / inv["file"]).read_bytes()[:8]
        if inv["format"].startswith("pdf"):
            assert head.startswith(b"%PDF-")
        elif inv["format"] == "png":
            assert head.startswith(b"\x89PNG")
        else:
            assert head.startswith(b"\xff\xd8\xff")


def test_multi_page_pdfs_have_the_recorded_page_count(dataset):
    out, manifest, _ = dataset
    multi = [i for i in manifest["invoices"] if i["pages"] > 1]
    assert len(multi) >= 4
    for inv in manifest["invoices"]:
        if inv["format"].startswith("pdf"):
            assert len(PdfReader(out / inv["file"]).pages) == inv["pages"]


def test_digital_pdfs_have_a_text_layer_matching_ground_truth(dataset):
    out, manifest, truths = dataset
    for inv in manifest["invoices"]:
        if inv["format"] != "pdf_digital":
            continue
        gt = truths[inv["id"]]
        text = "\n".join(p.extract_text() for p in PdfReader(out / inv["file"]).pages)
        assert gt["invoice_number"] in text
        assert gt["supplier"]["gstin"] in text
        assert gt["buyer"]["gstin"] in text
        total = LAYOUTS[gt["layout"]].money(D(gt["tax_summary"]["grand_total"]))
        assert total in text, (inv["id"], total)
        for line in gt["line_items"]:
            assert line["hsn"] in text


def test_scanned_images_are_rotated_blurred_and_noisy(dataset):
    out, manifest, truths = dataset
    scanned = [i for i in manifest["invoices"] if i["format"] != "pdf_digital"]
    assert scanned
    for inv in scanned:
        scan = truths[inv["id"]]["scan"]
        assert 0.5 <= abs(scan["rotation_deg"]) <= 3.0
        assert scan["blur_radius"] > 0
        assert scan["noise"] > 0
        if inv["format"] in ("png", "jpeg"):
            with Image.open(out / inv["file"]) as img:
                assert img.size[0] > 1000
                # Noise means the "white" paper is not a flat colour.
                lo, hi = img.convert("L").crop((0, 0, 200, 200)).getextrema()
                assert hi - lo > 20


# ---------- AC3: intra/inter, rates, round-off, HSN, IRN and QR ----------


def test_mix_of_intra_and_inter_state_and_utgst(dataset):
    _, _, truths = dataset
    types = Counter(t["supply_type"] for t in truths.values())
    assert types["intra"] >= 10 and types["inter"] >= 10
    heads = {t["tax_heads_printed"] for t in truths.values()}
    assert {"cgst_sgst", "igst", "cgst_utgst"} <= heads


def test_several_rates_with_dated_rate_table(dataset):
    _, _, truths = dataset
    rates = {D(ln["rate"]) for t in truths.values() for ln in t["line_items"]}
    assert len(rates) >= 5
    for t in truths.values():
        d = t["invoice_date"]
        allowed = catalog.rates_on(date.fromisoformat(d))
        for ln in t["line_items"]:
            assert D(ln["rate"]) in allowed, (t["id"], ln["rate"], d)
    old_rate_invoices = [t for t in truths.values() if t["invoice_date"] < "2025-09-22"]
    assert old_rate_invoices, "need some invoices before the Sept-2025 rate change"


def test_round_off_lines_and_hsn_codes(dataset):
    _, _, truths = dataset
    with_round_off = [t for t in truths.values() if D(t["tax_summary"]["round_off"]) != 0]
    assert len(with_round_off) >= 10
    for t in truths.values():
        assert abs(D(t["tax_summary"]["round_off"])) <= D("0.50")
        for ln in t["line_items"]:
            assert re.fullmatch(r"\d{4,8}", ln["hsn"])
    sac = {ln["hsn"] for t in truths.values() for ln in t["line_items"] if ln["hsn"][:2] == "99"}
    assert sac, "services (SAC codes) should appear too"


def test_some_invoices_have_irn_and_qr(dataset):
    _, _, truths = dataset
    e_inv = [t for t in truths.values() if t["irn"]]
    assert 10 <= len(e_inv) < len(truths)
    for t in e_inv:
        assert re.fullmatch(r"[0-9a-f]{64}", t["irn"])
        assert t["has_qr"] and t["ack_no"]
        raw = f"{t['supplier']['gstin']}{t['financial_year']}INV{t['invoice_number']}"
        assert t["irn"] == hashlib.sha256(raw.encode()).hexdigest()
        qr = json.loads(t["qr_payload"])
        assert qr["Irn"] == t["irn"]
        assert qr["DocNo"] == t["invoice_number"]
        assert qr["TotInvVal"] == t["tax_summary"]["grand_total"]


def test_clean_invoices_reconcile_exactly(dataset):
    """Ground truth arithmetic (gst-invoice-rules §3, §5) holds for every non-error invoice."""
    _, manifest, truths = dataset
    for inv in manifest["invoices"]:
        t = truths[inv["id"]]
        if t["seeded_error"]:
            continue
        mode = t["tax_heads_printed"]
        assert (mode == "igst") == (t["supply_type"] == "inter")
        sub = Decimal(0)
        for ln in t["line_items"]:
            taxable = D(ln["quantity"]) * D(ln["unit_price"]) - D(ln["discount"])
            assert taxable == D(ln["taxable_value"])
            if mode == "igst":
                assert D(ln["igst"]) == (taxable * D(ln["rate"]) / 100).quantize(
                    D("0.01"), ROUND_HALF_UP
                )
                assert D(ln["cgst"]) == D(ln["sgst"]) == D(ln["utgst"]) == 0
            else:
                assert D(ln["igst"]) == 0
                half = (taxable * D(ln["rate"]) / 200).quantize(D("0.01"), ROUND_HALF_UP)
                assert D(ln["cgst"]) == D(ln["sgst"]) + D(ln["utgst"]) == half
            sub += taxable
        s = t["tax_summary"]
        assert sub == D(s["taxable_total"]) == D(t["named_fields"]["subtotal"])
        taxes = D(s["cgst"]) + D(s["sgst"]) + D(s["utgst"]) + D(s["igst"])
        assert sub + taxes + D(s["round_off"]) == D(s["grand_total"])
        assert t["named_fields"]["total"] == s["grand_total"]


def test_named_fields_cover_the_seven_intent_fields(dataset):
    _, _, truths = dataset
    for t in truths.values():
        nf = t["named_fields"]
        assert set(nf) == {
            "vendor",
            "supplier_gstin",
            "invoice_number",
            "invoice_date",
            "subtotal",
            "tax_amounts",
            "total",
        }
        assert nf["vendor"] == t["supplier"]["name"]
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", nf["invoice_date"])
        assert re.fullmatch(r"-?\d+\.\d{2}", nf["total"])
        assert len(t["invoice_number"]) <= 16
        assert re.fullmatch(r"[A-Za-z0-9/-]+", t["invoice_number"])


# ---------- AC4: seeded duplicates and seeded errors ----------


def test_at_least_15_duplicates_of_every_kind(dataset):
    _, manifest, truths = dataset
    dups = [i for i in manifest["invoices"] if i["duplicate_kind"]]
    assert len(dups) >= 15
    kinds = Counter(i["duplicate_kind"] for i in dups)
    assert set(kinds) == {"exact", "rescan", "renumbered", "same_amount_date", "cross_entity"}
    assert min(kinds.values()) >= 2
    ids = [i["id"] for i in manifest["invoices"]]
    for d in dups:
        orig = truths[d["duplicate_of"]]
        assert ids.index(orig["id"]) < ids.index(d["id"]), "original must be ingested first"
        assert orig["duplicate"] is None and orig["seeded_error"] is None
        assert truths[d["id"]]["duplicate"] == {"of": orig["id"], "kind": d["duplicate_kind"]}


def test_duplicate_kinds_have_the_seeded_relationship(dataset):
    out, manifest, truths = dataset
    for d in (i for i in manifest["invoices"] if i["duplicate_kind"]):
        dup, orig = truths[d["id"]], truths[d["duplicate_of"]]
        kind = d["duplicate_kind"]
        assert dup["supplier"]["gstin"] == orig["supplier"]["gstin"]
        same_bytes = (out / dup["file"]).read_bytes() == (out / orig["file"]).read_bytes()
        assert same_bytes == (kind == "exact")
        norm_eq = normalise_number(dup["invoice_number"]) == normalise_number(
            orig["invoice_number"]
        )
        if kind in ("exact", "rescan"):
            assert dup["invoice_number"] == orig["invoice_number"]
            assert dup["tax_summary"] == orig["tax_summary"]
        if kind == "rescan":
            assert dup["format"] != "pdf_digital"
        if kind == "renumbered":
            assert dup["invoice_number"] != orig["invoice_number"] and norm_eq
            assert dup["financial_year"] == orig["financial_year"]
        if kind == "same_amount_date":
            assert (
                edit_distance(
                    normalise_number(dup["invoice_number"]),
                    normalise_number(orig["invoice_number"]),
                )
                > 1
            )
            assert dup["invoice_date"] == orig["invoice_date"]
            assert dup["tax_summary"]["grand_total"] == orig["tax_summary"]["grand_total"]
        if kind == "cross_entity":
            assert dup["invoice_number"] == orig["invoice_number"]
            assert dup["buyer_entity"] != orig["buyer_entity"]


def test_renumbered_example_from_spec():
    assert renumber("INV-0042") == "INV/42"
    assert normalise_number("INV-0042") == normalise_number("INV/42")


def test_no_unintended_duplicates_among_non_duplicates(dataset):
    """Only seeded duplicates should trip FR-30/31, so the eval's catch rate is meaningful."""
    _, manifest, truths = dataset
    base = [truths[i["id"]] for i in manifest["invoices"] if not i["duplicate_kind"]]
    for n, a in enumerate(base):
        for b in base[n + 1 :]:
            if a["supplier"]["gstin"] != b["supplier"]["gstin"]:
                continue
            na, nb = normalise_number(a["invoice_number"]), normalise_number(b["invoice_number"])
            assert edit_distance(na, nb) > 1, (a["id"], b["id"])
            ta, tb = D(a["tax_summary"]["grand_total"]), D(b["tax_summary"]["grand_total"])
            days = abs(
                (date.fromisoformat(a["invoice_date"]) - date.fromisoformat(b["invoice_date"])).days
            )
            assert not (abs(ta - tb) <= max(ta, tb) / 100 and days <= 30), (a["id"], b["id"])


def test_seeded_errors_of_each_kind(dataset):
    _, manifest, truths = dataset
    errs = [truths[i["id"]] for i in manifest["invoices"] if i["seeded_error"]]
    kinds = Counter(t["seeded_error"]["kind"] for t in errs)
    assert set(kinds) == {"wrong_tax_head", "bad_gstin", "arithmetic_off"}
    assert min(kinds.values()) >= 2
    for t in errs:
        e = t["seeded_error"]
        assert e["expected_severity"] == "block"
        if e["kind"] == "bad_gstin":
            assert GSTIN_RE.match(t["supplier"]["gstin"])
            assert not is_valid_gstin(t["supplier"]["gstin"])
            assert is_valid_gstin(e["valid_gstin"])
        elif e["kind"] == "wrong_tax_head":
            s = t["tax_summary"]
            assert D(s["cgst"]) + D(s["sgst"]) + D(s["utgst"]) + D(s["igst"]) > 0, t["id"]
            wrong_inter = t["supply_type"] == "inter" and t["tax_heads_printed"] != "igst"
            wrong_intra = t["supply_type"] == "intra" and t["tax_heads_printed"] == "igst"
            assert wrong_inter or wrong_intra
        elif e["kind"] == "arithmetic_off":
            s = t["tax_summary"]
            computed = (
                D(s["taxable_total"])
                + D(s["cgst"])
                + D(s["sgst"])
                + D(s["utgst"])
                + D(s["igst"])
                + D(s["round_off"])
            )
            assert abs(D(s["grand_total"]) - computed) > D("1.00")  # over the ₹1 tolerance
            assert D(e["correct_grand_total"]) == computed


# ---------- AC5: manifest marks clean, duplicates, seeded errors ----------


def test_manifest_partitions_the_set(dataset):
    _, manifest, _ = dataset
    s = manifest["subsets"]
    all_ids = {i["id"] for i in manifest["invoices"]}
    clean, dups, errs = set(s["clean"]), set(s["duplicates"]), set(s["seeded_errors"])
    assert clean | dups | errs == all_ids
    assert not (clean & dups) and not (clean & errs) and not (dups & errs)
    assert len(clean) >= 30
    for inv in manifest["invoices"]:
        assert inv["clean"] == (inv["id"] in clean)
        flags = inv["expected_flags"]
        if inv["id"] in clean:
            assert flags == []
        else:
            assert flags and all(f["severity"] in ("block", "warn") for f in flags)
    near = [i for i in manifest["invoices"] if i["duplicate_kind"] == "same_amount_date"]
    assert all(i["expected_flags"][0]["severity"] == "warn" for i in near)


def test_manifest_lists_entities_and_every_buyer_is_ours(dataset):
    _, manifest, truths = dataset
    ours = {r["gstin"]: e["key"] for e in manifest["entities"] for r in e["registrations"]}
    assert len(manifest["entities"]) >= 2
    for t in truths.values():
        assert ours[t["buyer"]["gstin"]] == t["buyer_entity"]


# ---------- AC6: synthetic only ----------


def test_gstin_checksum_follows_the_skill():
    first14 = "27" + "ZXABC1234D" + "1Z"
    g = make_gstin("27", "ZXABC1234D")
    assert g[:14] == first14 and is_valid_gstin(g)
    # Hand-computed with the gst-invoice-rules §1 algorithm.
    assert check_char("29ZXQCK0001A1Z") == make_gstin("29", "ZXQCK0001A")[-1]
    rng = random.Random(1)
    for _ in range(50):
        assert not is_valid_gstin(corrupt_gstin(g, rng))


def test_only_synthetic_identifiers(dataset):
    _, manifest, truths = dataset
    for t in truths.values():
        assert t["synthetic"] is True
        for party in (t["supplier"], t["buyer"]):
            gstin = party["gstin"]
            assert GSTIN_RE.match(gstin)
            assert gstin[2:4] == SYNTHETIC_PAN_PREFIX, "PAN must carry the fictitious ZX prefix"
            assert gstin[:2] in catalog.STATES
        if not t["seeded_error"] or t["seeded_error"]["kind"] != "bad_gstin":
            assert is_valid_gstin(t["supplier"]["gstin"])
        assert is_valid_gstin(t["buyer"]["gstin"])
        assert t["bank"]["ifsc"][:4] in catalog.SYNTHETIC_IFSC_BANK
    assert manifest["synthetic"] is True


def test_every_page_carries_the_synthetic_notice(dataset):
    out, manifest, _ = dataset
    for inv in manifest["invoices"]:
        if inv["format"] == "pdf_digital":
            for page in PdfReader(out / inv["file"]).pages:
                assert "Synthetic sample" in page.extract_text()


# ---------- helpers ----------


def test_helpers():
    assert financial_year(date(2026, 3, 31)) == "2025-26"
    assert financial_year(date(2026, 4, 1)) == "2026-27"
    assert group_indian(D("12345678.9")) == "1,23,45,678.90"
    assert group_indian(D("-0.44")) == "-0.44"
    assert amount_in_words(D("160194.00")) == (
        "Rupees One Lakh Sixty Thousand One Hundred Ninety Four Only"
    )
    assert amount_in_words(D("1001.50")) == "Rupees One Thousand One and Fifty Paise Only"
    assert normalise_number("inv / 0042") == "INV-42"
