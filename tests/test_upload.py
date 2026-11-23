"""SC-10 Upload and SC-3 Invoice list (S7; FR-10, FR-13; data-protection §4).

Files in, invoices out in state `received`: per-file results, SHA-256 recorded, files stored
under generated names outside served directories, exact-duplicate `block` within an entity,
an extraction job queued per invoice, all scoped to the user's entities.
"""

from __future__ import annotations

import hashlib
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.accounts.models import GstRegistration
from app.audit.chain import verify_chain
from app.audit.models import AuditEvent
from app.domain.actor import Role
from app.domain.flags import Flag
from app.domain.models import Invoice
from app.domain.states import InvoiceState
from app.intake.store import FileStore, UnsafeStoreLocation
from app.jobs import EXTRACT, Job, JobStatus
from app.main import STATIC_DIR
from app.tax.gstin import check_character
from tests.conftest import add_user, csrf_of, grants, signed_in
from tests.intake import samples

PDF = "application/pdf"


def gstin_for(state: str, pan: str) -> str:
    first14 = f"{state}{pan}1Z"
    return first14 + check_character(first14)


GSTIN_A = gstin_for("27", "ZXCPA0001A")
GSTIN_B = gstin_for("29", "ZXCPB0002B")


@pytest.fixture
def registrations(app, entity_rows):
    with app.state.sessions() as s:
        s.add_all(
            [
                GstRegistration(entity_id=1, gstin=GSTIN_A, state_code="27", address="Pune"),
                GstRegistration(entity_id=2, gstin=GSTIN_B, state_code="29", address="Bengaluru"),
            ]
        )
        s.commit()


@pytest.fixture
def clerk_ab(app, world, registrations):
    """A clerk on both entities (for the picker and auto-assignment)."""
    add_user(app.state.sessions, "clerk@ab.example", {1: grants(Role.CLERK), 2: grants(Role.CLERK)})
    return signed_in(app, "clerk@ab.example")


def upload(client: TestClient, files, entity: str = "auto", *, htmx: bool = False):
    headers = {"X-CSRF-Token": csrf_of(client, "/upload")}
    if htmx:
        headers["HX-Request"] = "true"
    parts = [("files", (name, data, ctype)) for name, data, ctype in files]
    return client.post("/upload", data={"entity": entity}, files=parts, headers=headers)


def invoices(app) -> list[Invoice]:
    with app.state.sessions() as s:
        return list(s.scalars(select(Invoice).order_by(Invoice.id)))


def result_rows(html: str) -> list[str]:
    body = re.search(r"<tbody>(.*?)</tbody>", html, re.S).group(1)
    rows = re.findall(r"<tr>(.*?)</tr>", body, re.S)
    rows = [re.sub(r'<span aria-hidden="true">[^<]*</span>', " ", r) for r in rows]  # icons
    return [" ".join(re.sub(r"<[^>]+>", " ", r).split()) for r in rows]


# --- SC-10 page and who sees it ------------------------------------------------------------------


def test_clerk_sees_the_upload_page_with_drop_zone_and_one_primary_action(app, world):
    page = signed_in(app, "clerk@a.example").get("/upload")
    assert page.status_code == 200
    html = page.text
    assert 'class="dropzone"' in html and 'type="file" name="files" multiple' in html
    assert 'enctype="multipart/form-data"' in html and 'hx-post="/upload"' in html
    assert html.count("btn-primary") == 1
    assert 'name="entity"' in html and "Entity One Pvt Ltd" in html
    assert 'href="/static/css/invoice-flow.css"' in html
    assert "/static/js/upload.js" in html


@pytest.mark.parametrize("who", ["approver", "accountant", "admin", "auditor"])
def test_other_roles_cannot_upload(app, world, who):
    client = signed_in(app, f"{who}@a.example")
    assert client.get("/upload").status_code == 403
    resp = upload(client, [("a.pdf", samples.pdf(), PDF)])
    assert resp.status_code == 403
    assert invoices(app) == []


def test_upload_needs_a_csrf_token(app, world):
    client = signed_in(app, "clerk@a.example")
    resp = client.post("/upload", files=[("files", ("a.pdf", samples.pdf(), PDF))])
    assert resp.status_code == 403
    assert invoices(app) == []


def test_picker_offers_auto_assign_only_with_several_entities(app, clerk_ab):
    html = clerk_ab.get("/upload").text
    assert '<option value="auto" selected>' in html
    assert "Entity One Pvt Ltd" in html and "Entity Two Pvt Ltd" in html
    single = signed_in(app, "clerk@a.example").get("/upload").text
    assert 'value="auto"' not in single


# --- files in, invoices out ----------------------------------------------------------------------


def test_many_files_become_received_invoices_with_hash_job_and_audit(app, world):
    client = signed_in(app, "clerk@a.example")
    pdf, png = samples.pdf(2), samples.image("PNG")
    resp = upload(client, [("INV-42.pdf", pdf, PDF), ("scan.png", png, "image/png")], "1")
    assert resp.status_code == 200
    rows = result_rows(resp.text)
    assert rows[0].startswith("INV-42.pdf Accepted Received Invoice 1 for Entity One Pvt Ltd")
    assert rows[1].startswith("scan.png Accepted Received Invoice 2 for Entity One Pvt Ltd")
    assert "2 of 2 files accepted." in resp.text

    first, second = invoices(app)
    assert first.state is InvoiceState.RECEIVED and second.state is InvoiceState.RECEIVED
    assert first.source == "upload" and first.entity_id == 1
    assert first.file_hash == hashlib.sha256(pdf).hexdigest()
    assert (first.file_type, first.file_size, first.page_count) == ("pdf", len(pdf), 2)
    assert first.file_name == "INV-42.pdf" and second.file_type == "png"
    assert first.uploaded_by == world["clerk"]

    with app.state.sessions() as s:
        queued = list(s.scalars(select(Job).order_by(Job.id)))
        events = list(s.scalars(select(AuditEvent).where(AuditEvent.action == "invoice.received")))
        assert verify_chain(s).ok
    assert [(j.kind, j.invoice_id, j.status) for j in queued] == [
        (EXTRACT, 1, JobStatus.QUEUED),
        (EXTRACT, 2, JobStatus.QUEUED),
    ]
    assert len(events) == 2
    assert events[0].actor == f"user:{world['clerk']}"
    assert '"file_sha256":"' + first.file_hash + '"' in events[0].data_json


def test_rejected_files_get_a_reason_each_and_create_nothing(app, world):
    client = signed_in(app, "clerk@a.example")
    files = [
        ("good.pdf", samples.pdf(), PDF),
        ("notes.pdf", b"just some text, named .pdf", PDF),
        ("bundle.zip", samples.zip_archive(), "application/zip"),
        ("locked.pdf", samples.pdf(password="pw"), PDF),
        ("long.pdf", samples.pdf(51), PDF),
        ("empty.pdf", b"", PDF),
    ]
    resp = upload(client, files, "1")
    assert resp.status_code == 200
    assert result_rows(resp.text) == [
        "good.pdf Accepted Received Invoice 1 for Entity One Pvt Ltd",
        "notes.pdf Rejected Not an allowed file type. Upload a PDF, PNG, JPEG, TIFF or WEBP file.",
        "bundle.zip Rejected Not an allowed file type. Upload a PDF, PNG, JPEG, TIFF or WEBP file.",
        "locked.pdf Rejected The PDF is encrypted or password-protected. Upload an unlocked copy.",
        "long.pdf Rejected The file has 51 pages; the limit is 50. Split it.",
        "empty.pdf Rejected The file is empty.",
    ]
    assert "1 of 6 files accepted." in resp.text
    assert len(invoices(app)) == 1
    stored = [p for p in app.state.settings.files_dir.iterdir()]
    assert len(stored) == 1  # rejected files are not kept


def test_file_over_20_mb_is_rejected(app, world):
    app.state.settings.max_upload_bytes = 1024  # same check, smaller number
    client = signed_in(app, "clerk@a.example")
    resp = upload(client, [("big.pdf", samples.pdf() + b"%" * 2048, PDF)], "1")
    assert "big.pdf Rejected The file is larger than" in result_rows(resp.text)[0]
    assert invoices(app) == []


def test_no_files_chosen_is_an_error_next_to_the_form(app, world):
    client = signed_in(app, "clerk@a.example")
    resp = client.post(
        "/upload", data={"entity": "1", "csrf_token": csrf_of(client, "/upload")}, files={}
    )
    assert resp.status_code == 422
    assert "Choose at least one file." in resp.text


def test_too_many_files_at_once_is_an_error(app, world):
    app.state.settings.max_files_per_upload = 2
    client = signed_in(app, "clerk@a.example")
    resp = upload(client, [(f"{n}.pdf", samples.pdf(), PDF) for n in range(3)], "1")
    assert resp.status_code == 422
    assert "upload at most 2 at a time" in resp.text
    assert invoices(app) == []


def test_htmx_upload_returns_only_the_results(app, world):
    client = signed_in(app, "clerk@a.example")
    resp = upload(client, [("a.pdf", samples.pdf(), PDF)], "1", htmx=True)
    assert resp.status_code == 200
    assert '<nav class="sidenav"' not in resp.text and "<html" not in resp.text
    assert "a.pdf" in resp.text and "Accepted" in resp.text


def test_file_names_are_shown_escaped_and_without_paths(app, world):
    client = signed_in(app, "clerk@a.example")
    resp = upload(client, [("C:\\scans\\..\\<script>x.pdf", samples.pdf(), PDF)], "1")
    assert "&lt;script&gt;x.pdf" in resp.text and "<script>x" not in resp.text
    assert invoices(app)[0].file_name == "<script>x.pdf"


# --- entity: chosen or auto ----------------------------------------------------------------------


def test_a_clerk_of_one_entity_gets_it_on_auto(app, world):
    client = signed_in(app, "clerk@a.example")
    upload(client, [("a.png", samples.image("PNG"), "image/png")], "auto")
    assert [i.entity_id for i in invoices(app)] == [1]


def test_auto_assign_reads_the_buyer_gstin_from_the_pdf_text(app, clerk_ab):
    to_b = samples.text_pdf("Supplier GSTIN 24AAACZ1234Q1Z9", f"Bill to GSTIN {GSTIN_B}")
    grouped_a = samples.text_pdf(
        f"Buyer: {GSTIN_A[:2]} {GSTIN_A[2:12]} {GSTIN_A[12:14]} {GSTIN_A[14]}"
    )
    resp = upload(clerk_ab, [("b.pdf", to_b, PDF), ("a.pdf", grouped_a, PDF)], "auto")
    rows = result_rows(resp.text)
    assert "for Entity Two Pvt Ltd" in rows[0] and "for Entity One Pvt Ltd" in rows[1]
    assert [i.entity_id for i in invoices(app)] == [2, 1]


def test_auto_assign_rejects_what_it_cannot_place(app, clerk_ab):
    both = samples.text_pdf(f"{GSTIN_A} to {GSTIN_B}")  # ambiguous
    resp = upload(
        clerk_ab,
        [("scan.png", samples.image("PNG"), "image/png"), ("both.pdf", both, PDF)],
        "auto",
    )
    reason = (
        "Could not tell which entity this invoice is for. Choose the entity and upload it again."
    )
    assert result_rows(resp.text) == [f"scan.png Rejected {reason}", f"both.pdf Rejected {reason}"]
    assert invoices(app) == []


def test_auto_assign_never_picks_an_entity_where_the_user_is_not_a_clerk(app, world, registrations):
    add_user(
        app.state.sessions, "mixed@ab.example", {1: grants(Role.CLERK), 2: grants(Role.APPROVER)}
    )
    client = signed_in(app, "mixed@ab.example")
    upload(client, [("b.pdf", samples.text_pdf(f"Bill to {GSTIN_B}"), PDF)], "auto")
    assert [i.entity_id for i in invoices(app)] == [1]  # the clerk's only entity


def test_choosing_an_entity_without_a_clerk_grant_is_not_found(app, world):
    client = signed_in(app, "clerk@a.example")
    assert upload(client, [("a.pdf", samples.pdf(), PDF)], "2").status_code == 404
    assert upload(client, [("a.pdf", samples.pdf(), PDF)], "999").status_code == 404
    assert upload(client, [("a.pdf", samples.pdf(), PDF)], "x").status_code == 404
    assert invoices(app) == []


# --- exact duplicates (FR-13) --------------------------------------------------------------------


def test_same_file_in_the_same_entity_raises_an_exact_duplicate_block(app, clerk_ab):
    pdf = samples.pdf()
    upload(clerk_ab, [("first.pdf", pdf, PDF)], "1")
    resp = upload(clerk_ab, [("again.pdf", pdf, PDF), ("other.pdf", pdf, PDF)], "1")
    rows = result_rows(resp.text)
    assert rows[0].endswith("Blocked: exact duplicate of invoice 1")
    assert rows[1].endswith("Blocked: exact duplicate of invoice 1")

    # The same file for another entity is not a duplicate within that entity.
    resp = upload(clerk_ab, [("for-b.pdf", pdf, PDF)], "2")
    assert "Blocked" not in result_rows(resp.text)[0]

    with app.state.sessions() as s:
        flags = list(s.scalars(select(Flag).order_by(Flag.id)))
        raised = list(s.scalars(select(AuditEvent).where(AuditEvent.action == "flag.raised")))
        assert verify_chain(s).ok
    assert [(f.invoice_id, f.related_invoice_id, f.entity_id) for f in flags] == [
        (2, 1, 1),
        (3, 1, 1),
    ]
    assert {(f.rule_id, f.severity, f.status) for f in flags} == {
        ("DUP-EXACT-FILE", "block", "open")
    }
    assert "invoice 1" in flags[0].message and "Discard" in flags[0].message
    assert [e.invoice_id for e in raised] == [2, 3]
    # Duplicates are still received and queued: the clerk decides in review (S11).
    assert {i.state for i in invoices(app)} == {InvoiceState.RECEIVED}


# --- file store (data-protection §4) -------------------------------------------------------------


def test_files_are_stored_outside_served_directories_under_generated_names(app, world):
    client = signed_in(app, "clerk@a.example")
    pdf = samples.pdf()
    upload(client, [("Shree Ganesh INV-42.pdf", pdf, PDF)], "1")
    (invoice,) = invoices(app)
    store: FileStore = app.state.file_store
    path = store.path(invoice.stored_name)
    assert path.read_bytes() == pdf
    assert path.parent == app.state.settings.files_dir.resolve()
    assert re.fullmatch(r"[0-9a-f]{32}\.pdf", invoice.stored_name)
    assert "Ganesh" not in invoice.stored_name
    assert STATIC_DIR not in path.parents
    assert path.stat().st_mode & 0o077 == 0  # owner-only
    # No route serves it, signed in or not.
    for url in (f"/static/{invoice.stored_name}", f"/files/{invoice.stored_name}"):
        assert client.get(url).status_code == 404


def test_the_file_store_refuses_a_served_directory(tmp_path):
    with pytest.raises(UnsafeStoreLocation):
        FileStore(STATIC_DIR / "files", served_dirs=(STATIC_DIR,))
    with pytest.raises(UnsafeStoreLocation):
        FileStore(STATIC_DIR, served_dirs=(STATIC_DIR,))
    FileStore(tmp_path / "files", served_dirs=(STATIC_DIR,))


def test_the_store_only_resolves_generated_names(app):
    store: FileStore = app.state.file_store
    for bad in ("../invoice_flow.db", "x.pdf", "a" * 32 + ".exe", "/etc/passwd"):
        with pytest.raises(ValueError):
            store.path(bad)


def test_an_upload_survives_a_restart_with_its_job_queued(app, world, settings):
    """The invoice, its file and its job are committed before the clerk sees "Accepted"."""
    client = signed_in(app, "clerk@a.example")
    upload(client, [("a.pdf", samples.pdf(), PDF)], "1")
    app.state.engine.dispose()

    from app.main import create_app

    restarted = create_app(settings)
    with restarted.state.sessions() as s:
        invoice = s.scalars(select(Invoice)).one()
        job = s.scalars(select(Job)).one()
    assert invoice.state is InvoiceState.RECEIVED
    assert (job.invoice_id, job.status) == (invoice.id, JobStatus.QUEUED)
    assert restarted.state.file_store.path(invoice.stored_name).exists()


def test_a_failed_save_loses_only_that_file_and_says_so(app, world, monkeypatch):
    import app.intake.service as service

    real_enqueue = service.jobs.enqueue
    calls = []

    def enqueue_fails_once(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("disk full")
        return real_enqueue(*args, **kwargs)

    monkeypatch.setattr(service.jobs, "enqueue", enqueue_fails_once)
    client = signed_in(app, "clerk@a.example")
    files = [(f"{n}.pdf", samples.pdf(n + 1), PDF) for n in range(3)]
    resp = upload(client, files, "1")
    assert resp.status_code == 200
    rows = result_rows(resp.text)
    assert rows[0].startswith("0.pdf Accepted") and rows[2].startswith("2.pdf Accepted")
    assert rows[1].startswith("1.pdf Rejected The file could not be saved.")
    saved = invoices(app)
    assert [i.file_name for i in saved] == ["0.pdf", "2.pdf"]
    with app.state.sessions() as s:
        assert [j.invoice_id for j in s.scalars(select(Job))] == [i.id for i in saved]
        assert verify_chain(s).ok
    stored = sorted(p.name for p in app.state.settings.files_dir.iterdir())
    assert stored == sorted(i.stored_name for i in saved)  # the failed file was removed


# --- SC-3 Invoice list ---------------------------------------------------------------------------


def test_invoice_list_shows_received_invoices_with_state_flags_and_age(app, world):
    client = signed_in(app, "clerk@a.example")
    pdf = samples.pdf()
    upload(client, [("first.pdf", pdf, PDF), ("copy.pdf", pdf, PDF)], "1")
    page = client.get("/invoices")
    assert page.status_code == 200
    html = page.text
    for header in ("Supplier", "Invoice no.", "Date", "Total", "Entity", "State", "Flags", "Age"):
        assert f">{header}</th>" in html
    rows = result_rows(html)
    assert rows[0].startswith("Not read yet copy.pdf")  # newest first
    assert "Received" in rows[0] and "1 blocked" in rows[0] and "just now" in rows[0]
    assert rows[1].startswith("Not read yet first.pdf") and "None" in rows[1]
    assert 'data-state="received"' in html
    assert html.count("btn-primary") == 1 and 'href="/upload"' in html


@pytest.mark.parametrize("who", ["approver", "accountant", "admin", "auditor"])
def test_every_role_on_the_entity_sees_the_list_without_upload(app, world, who):
    signed_in(app, "clerk@a.example")
    upload(signed_in(app, "clerk@a.example"), [("a.pdf", samples.pdf(), PDF)], "1")
    html = signed_in(app, f"{who}@a.example").get("/invoices").text
    assert "a.pdf" in html
    assert 'href="/upload"' not in html and "btn-primary" not in html


def test_the_list_never_shows_another_entitys_invoices(app, world):
    upload(signed_in(app, "clerk@a.example"), [("entity-a-secret.pdf", samples.pdf(), PDF)], "1")
    html = signed_in(app, "clerk@b.example").get("/invoices").text
    assert "entity-a-secret.pdf" not in html
    assert "No invoices yet" in html


def test_the_switcher_narrows_the_list(app, clerk_ab):
    upload(clerk_ab, [("for-a.pdf", samples.pdf(), PDF)], "1")
    upload(clerk_ab, [("for-b.png", samples.image("PNG"), "image/png")], "2")
    html = clerk_ab.get("/invoices").text
    assert "for-a.pdf" in html and "for-b.png" in html
    token = csrf_of(clerk_ab, "/invoices")
    clerk_ab.post("/switch-entity", data={"entity_id": "2", "csrf_token": token, "next": "/"})
    html = clerk_ab.get("/invoices").text
    assert "for-b.png" in html and "for-a.pdf" not in html


def test_empty_list_says_what_will_appear(app, world):
    html = signed_in(app, "clerk@a.example").get("/invoices").text
    assert "No invoices yet" in html and "Upload invoices" in html


def test_menu_shows_upload_and_invoices_to_clerks(app, world):
    html = signed_in(app, "clerk@a.example").get("/").text
    assert 'href="/invoices"' in html and 'href="/upload"' in html
    approver = signed_in(app, "approver@a.example").get("/").text
    assert 'href="/invoices"' in approver and 'href="/upload"' not in approver
