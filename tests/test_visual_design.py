"""The visual design applied to the app (ticket #36, S5.1; visual-design.md, ux-standards).

Static assets are self-hosted and public; templates use only the stylesheet's classes, with no
inline styles, raw hex colours or third-party URLs; money, dates, GSTINs and invoice states
render one way; each S5 screen has at most one primary action.
"""

import base64
import hashlib
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.templating import TEMPLATES, format_date, format_money
from app.domain.money import Money
from app.domain.states import InvoiceState
from app.main import STATIC_DIR
from tests.conftest import signed_in

REPO = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = REPO / "app" / "templates"
TEMPLATES_FILES = sorted(TEMPLATE_DIR.rglob("*.html"))
CSS = STATIC_DIR / "css" / "invoice-flow.css"
STYLEGUIDE = REPO / "doc" / "design" / "styleguide.html"
FONTS = [
    "AtkinsonHyperlegibleNext-Regular.woff2",
    "AtkinsonHyperlegibleNext-Bold.woff2",
    "AtkinsonHyperlegibleMono-Regular.woff2",
    "AtkinsonHyperlegibleMono-Bold.woff2",
]
HTMX = "js/htmx-2.0.4.min.js"
# A hex colour, not a URL fragment such as href="#add-entity" (preceded by a quote).
HEX_COLOUR = re.compile(
    r"""(?<![\w"'/&])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b(?!-)"""
)
EXTERNAL_URL = re.compile(r"""(?:https?:)?//[a-z0-9.-]+\.[a-z]{2,}""", re.I)


def _css_without_root(css: str) -> str:
    return re.sub(r":root\s*\{.*?\}", "", css, count=1, flags=re.S)


# --- static assets -----------------------------------------------------------------------


def test_stylesheet_moved_into_the_app():
    assert CSS.is_file()
    assert not (REPO / "doc" / "design" / "invoice-flow.css").exists()


@pytest.mark.parametrize(
    "path,content_type",
    [
        ("css/invoice-flow.css", "text/css"),
        (HTMX, "javascript"),
        ("js/shell.js", "javascript"),
        *[(f"fonts/{f}", "font/woff2") for f in FONTS],
    ],
)
def test_static_assets_are_served_without_sign_in(client, path, content_type):
    resp = client.get(f"/static/{path}")
    assert resp.status_code == 200
    assert content_type in resp.headers["content-type"]


def test_fonts_are_woff2_400_and_700_with_their_ofl_licence():
    for name in FONTS:
        assert (STATIC_DIR / "fonts" / name).read_bytes()[:4] == b"wOF2", name
    for family in ("AtkinsonHyperlegibleNext", "AtkinsonHyperlegibleMono"):
        licence = (STATIC_DIR / "fonts" / f"OFL-{family}.txt").read_text()
        assert "SIL Open Font License, Version 1.1" in licence
    css = CSS.read_text()
    for family in ("Atkinson Hyperlegible Next", "Atkinson Hyperlegible Mono"):
        for weight in ("400", "700"):
            assert re.search(
                rf'@font-face \{{ font-family: "{family}"; font-style: normal; '
                rf"font-weight: {weight};",
                css,
            ), (family, weight)
    for url in re.findall(r'url\("([^"]+)"\)', css):
        assert (CSS.parent / url).resolve().is_file(), url


def test_htmx_is_the_pinned_2_0_4_build_with_a_matching_integrity_hash():
    js = (STATIC_DIR / HTMX).read_bytes()
    assert b'version:"2.0.4"' in js
    digest = base64.b64encode(hashlib.sha384(js).digest()).decode()
    base = (TEMPLATE_DIR / "base.html").read_text()
    assert f'<script src="/static/{HTMX}" integrity="sha384-{digest}"></script>' in base


def test_static_directory_holds_only_public_asset_types():
    # Never a web-served home for data (data-protection §4): only these file types.
    allowed = {".css", ".woff2", ".js", ".txt"}
    files = [p for p in STATIC_DIR.rglob("*") if p.is_file()]
    assert files
    assert {p.suffix for p in files} <= allowed, sorted(str(p) for p in files)


@pytest.mark.parametrize(
    "path",
    [
        "/static/",
        "/static/css/",
        "/static/../main.py",
        "/static/%2e%2e/main.py",
        "/static/..%2fmain.py",
        "/static/css/../../templates/base.html",
        "/static/missing.css",
    ],
)
def test_static_mount_serves_nothing_else(client, path):
    resp = client.get(path)
    assert resp.status_code == 404, path
    assert "create_app" not in resp.text and "{% block" not in resp.text


# --- templates: one stylesheet, no inline styles, no third parties -----------------------


def test_base_links_the_one_stylesheet_and_no_third_party_host():
    base = (TEMPLATE_DIR / "base.html").read_text()
    assert '<link rel="stylesheet" href="/static/css/invoice-flow.css">' in base
    assert "unpkg" not in base
    assert '"includeIndicatorStyles":false' in base  # htmx injects no <style> either


@pytest.mark.parametrize("path", TEMPLATES_FILES, ids=lambda p: p.name)
def test_template_has_no_inline_style_hex_colour_or_external_url(path):
    text = path.read_text()
    assert "<style" not in text.lower()
    assert not re.search(r"\sstyle\s*=", text, re.I)
    assert not HEX_COLOUR.search(text), HEX_COLOUR.search(text).group(0)
    assert not EXTERNAL_URL.search(text), EXTERNAL_URL.search(text).group(0)


def test_every_class_in_the_templates_is_defined_in_the_stylesheet():
    css = CSS.read_text()
    defined = set(re.findall(r"\.([a-zA-Z][\w-]*)", re.sub(r"url\([^)]*\)", "", css)))
    used = set()
    for path in TEMPLATES_FILES:
        text = re.sub(r"\{%.*?%\}|\{\{.*?\}\}", " ", path.read_text())  # drop Jinja
        for attr in re.findall(r'class="([^"]*)"', text):
            used |= set(attr.split())
    # Built dynamically in macros: stamp-lg and notice-ok/notice-error.
    used |= {"stamp-lg", "notice-ok", "notice-error"}
    assert used - defined == set()


def test_stylesheet_uses_tokens_and_no_third_party_host():
    css = CSS.read_text()
    assert not HEX_COLOUR.search(_css_without_root(css))
    assert not EXTERNAL_URL.search(re.sub(r"/\*.*?\*/", "", css, flags=re.S))
    assert "@import" not in css


def test_style_guide_uses_the_app_stylesheet_and_no_third_party_host():
    html = STYLEGUIDE.read_text()
    assert '<link rel="stylesheet" href="../../app/static/css/invoice-flow.css">' in html
    assert (STYLEGUIDE.parent / "../../app/static/css/invoice-flow.css").resolve() == CSS
    assert not EXTERNAL_URL.search(html)


# --- rendered S5 screens --------------------------------------------------------------------

S5_SCREENS = {
    # path: (role that sees it, number of primary buttons)
    "/": ("clerk", 0),  # SC-2; "Open the oldest task" arrives with counts in S15
    "/account": ("clerk", 1),  # SC-6
    "/admin/entities": ("admin", 1),  # SC-40
    "/admin/entities/1": ("admin", 1),  # SC-40 entity
    "/admin/users": ("admin", 1),  # SC-41
    "/admin/users/1": ("admin", 1),  # SC-41 user
}


def _rendered(app, world):
    pages = {"/login": TestClient(app).get("/login").text}
    for path, (who, _) in S5_SCREENS.items():
        resp = signed_in(app, f"{who}@a.example").get(path)
        assert resp.status_code == 200, path
        pages[path] = resp.text
    return pages


def test_s5_screens_render_with_the_stylesheet_and_one_primary_action(app, world):
    pages = _rendered(app, world)
    expected = {"/login": 1} | {p: n for p, (_, n) in S5_SCREENS.items()}
    for path, html in pages.items():
        assert 'href="/static/css/invoice-flow.css"' in html, path
        assert html.count("btn-primary") == expected[path], path
        assert not re.search(r"\sstyle\s*=|<style", html), path
        for url in re.findall(r'(?:src|href|action)="([^"]*)"', html):
            assert not EXTERNAL_URL.match(url), (path, url)


def test_admin_entities_has_a_single_add_entity_button_and_it_is_primary(app, world):
    html = signed_in(app, "admin@a.example").get("/admin/entities").text
    buttons = re.findall(
        r'<(?:a|button)\b[^>]*class="([^"]*\bbtn\b[^"]*)"[^>]*>\s*Add entity\s*<', html
    )
    assert buttons == ["btn btn-primary"]


def test_shell_uses_the_stylesheet_layout_classes(app, world):
    html = signed_in(app, "admin@a.example").get("/").text
    for marker in ('class="shell"', 'class="topbar"', 'class="sidenav"', 'class="brand"'):
        assert marker in html
    assert 'id="status" class="toast" role="status" aria-live="polite"' in html
    assert 'class="worklist"' in signed_in(app, "clerk@a.example").get("/").text


def test_login_page_is_styled_without_the_shell(client):
    html = client.get("/login").text
    assert '<main id="main" class="signin">' in html
    assert 'class="btn btn-primary">Sign in</button>' in html


# --- formatting: money, dates, GSTINs, states (ux-standards) ------------------------------


@pytest.mark.parametrize(
    "value,text",
    [
        (Money.of("123456"), "₹1,23,456.00"),
        (Money.of("1234567.5"), "₹12,34,567.50"),
        (Money.of("999"), "₹999.00"),
        (Money.of("1000"), "₹1,000.00"),
        (Money.of("100000"), "₹1,00,000.00"),
        (Money.of("123456789.01"), "₹12,34,56,789.01"),
        (Money.zero(), "₹0.00"),
        (Money.of("0.05"), "₹0.05"),
        (-Money.of("118000"), "-₹1,18,000.00"),
        (Decimal("472500"), "₹4,72,500.00"),
        (Decimal("0.005"), "₹0.01"),
        (None, ""),
    ],
)
def test_money_filter_uses_rupee_sign_and_indian_grouping(value, text):
    assert format_money(value) == text
    assert TEMPLATES.env.from_string("{{ v|money }}").render(v=value) == text


def test_money_filter_shows_other_currencies_by_code():
    assert format_money(Decimal("1234"), "USD") == "USD 1,234.00"
    assert format_money(Money.of("1234567.89"), "EUR") == "EUR 1,234,567.89"


@pytest.mark.parametrize("bad", [1234.5, 1234, "1234"])
def test_money_filter_refuses_floats_and_untyped_values(bad):
    with pytest.raises(TypeError):
        format_money(bad)


def test_date_filter_is_unambiguous():
    assert format_date(date(2026, 11, 20)) == "20-Nov-2026"
    assert format_date(datetime(2026, 1, 5, 9, 30)) == "05-Jan-2026"
    assert TEMPLATES.env.from_string("{{ d|date }}").render(d=date(2026, 11, 20)) == "20-Nov-2026"


def test_gstin_renders_grouped_in_mono():
    tpl = TEMPLATES.env.from_string('{% from "_macros.html" import gstin %}{{ gstin(g) }}')
    assert tpl.render(g="27AAPFU0939F1ZV") == '<span class="gstin">27 AAPFU0939F 1Z V</span>'
    assert ".mono, .gstin, .invno { font-family: var(--font-mono); }" in CSS.read_text()


def test_admin_entities_shows_gstins_grouped_in_mono(app, world):
    from tests.test_admin_entities import PAN_ONE, gstin, post

    admin = signed_in(app, "admin@a.example")
    value = gstin("29", PAN_ONE)
    resp = post(admin, "/admin/entities/1/registrations", {"gstin": value, "address": "Bengaluru"})
    assert resp.status_code == 303
    grouped = f"{value[:2]} {value[2:12]} {value[12:14]} {value[14]}"
    assert f'<span class="gstin">{grouped}</span>' in admin.get("/admin/entities").text


@pytest.mark.parametrize("state", list(InvoiceState))
def test_every_invoice_state_renders_as_a_stamp_with_its_word(state):
    tpl = TEMPLATES.env.from_string('{% from "_macros.html" import stamp %}{{ stamp(s) }}')
    html = tpl.render(s=state)
    match = re.fullmatch(r'<span class="stamp" data-state="([a-z_]+)">([A-Z][a-z ]+)</span>', html)
    assert match and match.group(1) == state.value
    assert match.group(2).lower().replace(" ", "_") == state.value


def test_large_stamp_and_detail():
    tpl = TEMPLATES.env.from_string(
        '{% from "_macros.html" import stamp %}{{ stamp("in_approval", large=true, detail=d) }}'
    )
    assert tpl.render(d="tier 1 of 2") == (
        '<span class="stamp stamp-lg" data-state="in_approval">In approval · tier 1 of 2</span>'
    )


def test_coloured_states_have_a_stamp_rule():
    css = CSS.read_text()
    for state in ("needs_review", "in_approval", "approved", "paid", "rejected", "discarded"):
        assert f'.stamp[data-state="{state}"]' in css
