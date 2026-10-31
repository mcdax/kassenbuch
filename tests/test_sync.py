from pathlib import Path

import pytest

from kassenbuch import db, sync
from kassenbuch.client import ApiError, TicketPage, TicketSummary

HTML = (Path(__file__).parent / "fixtures" / "receipt_anonymized.html").read_text()


class FakeClient:
    """Stands in for LidlPlusClient: `count` receipts, newest first, 25 per page."""
    count = 60
    failing_pages: set = set()
    failing_tickets: set = set()
    pages_requested: list = []

    def __init__(self, account):
        self.account, self.country = account, "de"

    def tickets_page(self, page):
        FakeClient.pages_requested.append(page)
        if page in self.failing_pages:
            raise ApiError(f"HTTP 500 für Seite {page}")
        ids = [f"R{n:04d}" for n in range(self.count, 0, -1)][(page - 1) * 25:page * 25]
        return TicketPage([TicketSummary(i, True) for i in ids], self.count, 25)

    def ticket(self, ticket_id):
        if ticket_id in self.failing_tickets:
            raise ApiError("HTTP 500")
        n = int(ticket_id[1:])
        return {"id": ticket_id, "date": f"2026-01-{n % 28 + 1:02d}T10:{n % 60:02d}:00", "totalAmount": 105.78,
                "store": {"id": "S1", "name": "Lidl Teststadt"}, "htmlPrintedReceipt": HTML}


@pytest.fixture
def conn(monkeypatch):
    monkeypatch.setattr(sync, "LidlPlusClient", FakeClient)
    FakeClient.count, FakeClient.failing_pages, FakeClient.failing_tickets = 60, set(), set()
    FakeClient.pages_requested = []
    return db.connect()


def run(conn, full=True):
    return sync.sync_account(conn, "anna", full=full, log=lambda *a: None, delay=0)


def test_full_sync_imports_everything(conn):
    result = run(conn)

    assert result.ok and len(result.new) == 60 and result.with_warnings == 0
    assert FakeClient.pages_requested == [1, 2, 3]
    assert conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 60
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 60 * 45
    row = conn.execute("SELECT total_cents, warnings, html IS NOT NULL AS has_html FROM receipts LIMIT 1").fetchone()
    assert (row["total_cents"], row["warnings"], row["has_html"]) == (10578, None, 1)
    # New receipts come oldest first, ready for notifications
    assert [r.purchased_at for r in result.new] == sorted(r.purchased_at for r in result.new)


def test_quick_check_only_reads_newest_page(conn):
    run(conn)
    FakeClient.count, FakeClient.pages_requested = 62, []

    result = run(conn, full=False)

    assert FakeClient.pages_requested == [1]
    assert [r.id for r in result.new] == ["R0061", "R0062"]


def test_failed_page_stores_nothing(conn):
    FakeClient.failing_pages = {2}
    with pytest.raises(ApiError):
        run(conn)
    assert conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 0

    FakeClient.failing_pages = set()
    assert len(run(conn).new) == 60


def test_failed_receipt_is_retried_next_time(conn):
    FakeClient.failing_tickets = {"R0007"}
    result = run(conn)
    assert not result.ok and result.failed == ["R0007"] and len(result.new) == 59

    FakeClient.failing_tickets = set()
    result = run(conn)
    assert result.ok and [r.id for r in result.new] == ["R0007"]


def test_reparse_after_parser_change(conn, monkeypatch):
    run(conn)
    conn.execute("UPDATE receipts SET parser_version = 0 WHERE id = 'R0001'")
    conn.execute("DELETE FROM items WHERE receipt_id = 'R0001'")
    conn.commit()

    assert sync.reparse(conn) == (1, 0)
    assert conn.execute("SELECT COUNT(*) FROM items WHERE receipt_id = 'R0001'").fetchone()[0] == 45
