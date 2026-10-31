"""Import receipts from the Lidl Plus API into the database."""

import sqlite3
import time
from dataclasses import dataclass, field

from kassenbuch import db
from kassenbuch.client import ApiError, LidlPlusClient
from kassenbuch.parser import PARSER_VERSION, parse_receipt

REQUEST_DELAY = 0.5


@dataclass
class NewReceipt:
    id: str
    account: str
    purchased_at: str
    store_name: str | None
    total_cents: int
    items_count: int
    savings_cents: int


@dataclass
class SyncResult:
    account: str
    new: list[NewReceipt] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)  # receipt IDs that could not be downloaded
    without_html: int = 0
    with_warnings: int = 0

    @property
    def ok(self) -> bool:
        return not self.failed


def sync_account(conn: sqlite3.Connection, account: str, full: bool = True, log=print,
                 delay: float = REQUEST_DELAY) -> SyncResult:
    """
    Download receipts that are not in the database yet.

    A quick check (full=False) only looks at the newest page (25 receipts); a full sync goes through
    all pages and so also fills gaps. Raises AuthError/ApiError if the receipt list can't be loaded,
    before anything is stored, so a run never leaves a silent gap.
    """
    result = SyncResult(account)
    client = LidlPlusClient(account)
    db.ensure_account(conn, account, client.country)

    ids, page = [], 1
    while True:
        tickets = client.tickets_page(page)
        ids += [t.id for t in tickets.tickets if t.has_html]
        result.without_html += sum(1 for t in tickets.tickets if not t.has_html)
        if not full or not tickets.tickets or page * tickets.page_size >= tickets.total_count:
            break
        page += 1

    known = db.known_receipt_ids(conn)
    new_ids = [i for i in ids if i not in known]
    log(f"{account}: {len(ids)} Kassenbons in Lidl Plus, {len(new_ids)} neu")

    for n, receipt_id in enumerate(new_ids, 1):
        try:
            ticket = client.ticket(receipt_id)
        except ApiError as e:
            log(f"  {receipt_id}: Download fehlgeschlagen ({e})")
            result.failed.append(receipt_id)
            continue
        parsed = parse_receipt(ticket.get("htmlPrintedReceipt") or "")
        db.save_receipt(conn, account, ticket, parsed)
        if parsed.warnings:
            result.with_warnings += 1
            log(f"  {receipt_id}: gespeichert, aber mit Hinweisen: {'; '.join(parsed.warnings)}")
        result.new.append(NewReceipt(receipt_id, account, ticket["date"], (ticket.get("store") or {}).get("name"),
                                     round(ticket["totalAmount"] * 100), len(parsed.items),
                                     -sum(d.amount_cents for d in parsed.discounts)))
        if len(new_ids) > 10 and n % 25 == 0:
            log(f"  {n}/{len(new_ids)}")
        time.sleep(delay)

    result.new.sort(key=lambda r: r.purchased_at)
    return result


def reparse(conn: sqlite3.Connection, all_receipts: bool = False) -> tuple[int, int]:
    """
    Parse stored receipt HTML again (after a parser update).

    Returns:
        tuple: (receipts parsed, receipts with warnings)
    """
    query = "SELECT id, html, total_cents FROM receipts"
    rows = conn.execute(query if all_receipts else query + " WHERE parser_version < ?",
                        () if all_receipts else (PARSER_VERSION,)).fetchall()
    with_warnings = 0
    for row in rows:
        parsed = parse_receipt(row["html"] or "")
        db.update_parsed(conn, row["id"], parsed, row["total_cents"])
        with_warnings += bool(parsed.warnings)
    return len(rows), with_warnings


def receipts_with_warnings(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT id, account, purchased_at, warnings FROM receipts WHERE warnings IS NOT NULL "
                        "ORDER BY purchased_at DESC").fetchall()
