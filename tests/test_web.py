from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kassenbuch import db, queries
from kassenbuch.parser import parse_receipt
from kassenbuch.web.app import create_app

HTML = (Path(__file__).parent / "fixtures" / "receipt_anonymized.html").read_text()


@pytest.fixture
def conn():
    conn = db.connect()
    for account in ("anna", "bob"):
        db.ensure_account(conn, account, "de")
    parsed = parse_receipt(HTML)
    for n, (account, date) in enumerate([("anna", "2026-09-05T10:00:00"), ("anna", "2026-10-01T18:30:00"),
                                         ("bob", "2025-03-01T09:15:00")]):
        db.save_receipt(conn, account, {"id": f"R{n}", "date": date, "totalAmount": 105.78,
                                        "store": {"name": "Lidl Teststadt"}, "htmlPrintedReceipt": HTML}, parsed)
    return conn


@pytest.fixture
def client(conn, monkeypatch):
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    return TestClient(create_app())


def test_pages(client, conn):
    article = queries.products(conn, None)[0]["article_id"]
    for url in ["/", "/?period=all", "/?account=bob&period=2025", "/receipts", "/receipts/R0",
                "/products", "/products?q=artikel&sort=price", f"/products/{article}"]:
        response = client.get(url)
        assert response.status_code == 200, url
        assert "Content-Security-Policy" in response.headers


def test_account_filter(client):
    assert client.get("/receipts?period=all").text.count('href="/receipts/R') == 3
    assert client.get("/receipts?period=all&account=anna").text.count('href="/receipts/R') == 2
    # Unknown accounts fall back to all accounts
    assert client.get("/receipts?period=all&account=nobody").text.count('href="/receipts/R') == 3


def test_original_receipt(client):
    original = client.get("/receipts/R0/original")
    assert original.status_code == 200
    assert "default-src 'none'" in original.headers["Content-Security-Policy"]
    assert 'id="kb-print"' in original.text
    assert 'id="kb-print"' not in client.get("/receipts/R0/original?embed=1").text


def test_missing_pages(client):
    assert client.get("/receipts/unknown").status_code == 404
    assert client.get("/products/unknown").status_code == 404


def test_overview_numbers(conn):
    o = queries.overview(conn, None, queries.period("all"))
    assert o.receipts == 3 and o.spent_cents == 3 * 10578
    assert o.savings_cents == 3 * 294  # the fixture's discounts: 7 lines, -2,94 €
    assert [m for m, _ in o.monthly][0] == "2025-03" and o.monthly[-1][0] == "2026-10"
    assert sum(cents for _, cents in o.monthly) == o.spent_cents


@pytest.fixture
def protected(conn, monkeypatch):
    monkeypatch.setenv("WEB_PASSWORD", "geheim")
    return TestClient(create_app())


def test_login_required(protected):
    response = protected.get("/receipts", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/login?next=%2Freceipts"
    assert protected.get("/healthz").status_code == 200


def test_login_and_logout(protected, monkeypatch):
    monkeypatch.setattr("kassenbuch.web.app.time.sleep", lambda s: None)
    failed = protected.post("/login", data={"password": "falsch", "next": "/receipts"}, follow_redirects=False)
    assert "failed=1" in failed.headers["location"]

    ok = protected.post("/login", data={"password": "geheim", "next": "/receipts"}, follow_redirects=False)
    assert ok.headers["location"] == "/receipts"
    assert protected.get("/receipts").status_code == 200

    protected.post("/logout")
    assert protected.get("/receipts", follow_redirects=False).status_code == 303


@pytest.mark.parametrize("target", ["https://evil.example", "//evil.example", "javascript:alert(1)"])
def test_no_open_redirect_after_login(protected, target):
    response = protected.post("/login", data={"password": "geheim", "next": target}, follow_redirects=False)
    assert response.headers["location"] == "/"


@pytest.mark.parametrize("key, label, start, end", [
    ("12m", "Letzte 12 Monate", "2025-11-01", "2026-11-01"),
    ("2025", "2025", "2025-01-01", "2026-01-01"),
    ("all", "Gesamter Zeitraum", None, None),
])
def test_periods(key, label, start, end):
    from datetime import date
    p = queries.period(key, today=date(2026, 10, 31))
    assert (p.label, p.start, p.end) == (label, start, end)
