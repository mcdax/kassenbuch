"""
SQLite storage. Amounts are integer cents. The original receipt HTML is kept, so receipts can be
shown in their original layout and parsed again when the parser improves.
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from kassenbuch import settings
from kassenbuch.parser import PARSER_VERSION, ParsedReceipt

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    name TEXT PRIMARY KEY,
    country TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS receipts (
    id TEXT PRIMARY KEY,
    account TEXT NOT NULL REFERENCES accounts(name),
    purchased_at TEXT NOT NULL,          -- local time, ISO 8601
    store_id TEXT,
    store_name TEXT,
    store_address TEXT,
    store_postal_code TEXT,
    store_locality TEXT,
    total_cents INTEGER NOT NULL,        -- amount paid
    reported_savings_cents INTEGER,      -- "Gesamter Preisvorteil" as printed
    lidlplus_savings_cents INTEGER,      -- "Mit Lidl Plus ... gespart" as printed
    html TEXT,
    meta_json TEXT NOT NULL,             -- API response without the HTML
    parser_version INTEGER NOT NULL,
    warnings TEXT,                       -- parser warnings, NULL if the receipt adds up
    imported_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS receipts_account_date ON receipts(account, purchased_at);

CREATE TABLE IF NOT EXISTS items (
    receipt_id TEXT NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    article_id TEXT,
    name TEXT NOT NULL,
    quantity REAL NOT NULL,
    unit TEXT NOT NULL,                  -- 'kg' or 'stk'
    unit_price_cents INTEGER,
    amount_cents INTEGER NOT NULL,       -- before discounts
    tax_type TEXT,
    tax_rate REAL,
    is_deposit INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (receipt_id, position)
);
CREATE INDEX IF NOT EXISTS items_article ON items(article_id);

CREATE TABLE IF NOT EXISTS discounts (
    receipt_id TEXT NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    item_position INTEGER,
    kind TEXT NOT NULL,                  -- promotion | lidl_plus | coupon | sticker | other
    description TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,       -- negative
    promotion_id TEXT,
    PRIMARY KEY (receipt_id, position)
);

CREATE TABLE IF NOT EXISTS payments (
    receipt_id TEXT NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    method TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    PRIMARY KEY (receipt_id, position)
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS taxes (
    receipt_id TEXT NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
    tax_type TEXT NOT NULL,
    rate REAL NOT NULL,
    gross_cents INTEGER NOT NULL,
    tax_cents INTEGER NOT NULL,
    PRIMARY KEY (receipt_id, tax_type)
);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or settings.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    # WAL: the web app can read while the poller writes
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn


def app_setting(conn: sqlite3.Connection, key: str, create) -> str:
    """Stored setting; created with create() on first use (e.g. the web app's session secret)."""
    with conn:
        conn.execute("INSERT OR IGNORE INTO app_settings VALUES (?, ?)", (key, create()))
    return conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()[0]


def ensure_account(conn: sqlite3.Connection, name: str, country: str) -> None:
    with conn:
        conn.execute("INSERT OR IGNORE INTO accounts (name, country, created_at) VALUES (?, ?, ?)",
                     (name, country, datetime.now().isoformat(timespec="seconds")))


def known_receipt_ids(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT id FROM receipts")}


def _write_parsed(conn: sqlite3.Connection, receipt_id: str, parsed: ParsedReceipt) -> None:
    for table in ("items", "discounts", "payments", "taxes"):
        conn.execute(f"DELETE FROM {table} WHERE receipt_id = ?", (receipt_id,))
    conn.executemany(
        "INSERT INTO items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(receipt_id, i.position, i.article_id, i.name, i.quantity, i.unit, i.unit_price_cents, i.amount_cents,
          i.tax_type, i.tax_rate, int(i.is_deposit)) for i in parsed.items])
    conn.executemany(
        "INSERT INTO discounts VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(receipt_id, d.position, d.item_position, d.kind, d.description, d.amount_cents, d.promotion_id)
         for d in parsed.discounts])
    conn.executemany(
        "INSERT INTO payments VALUES (?, ?, ?, ?)",
        [(receipt_id, n, p.method, p.amount_cents) for n, p in enumerate(parsed.payments, 1)])
    conn.executemany(
        "INSERT OR REPLACE INTO taxes VALUES (?, ?, ?, ?, ?)",
        [(receipt_id, t.tax_type, t.rate, t.gross_cents, t.tax_cents) for t in parsed.taxes])


def save_receipt(conn: sqlite3.Connection, account: str, ticket: dict, parsed: ParsedReceipt) -> None:
    """Store a receipt from the API (ticket detail) with its parsed content, in one transaction."""
    meta = {k: v for k, v in ticket.items() if k != "htmlPrintedReceipt"}
    store = ticket.get("store") or {}
    # The API's total is authoritative for what was paid; the parser's total is checked against it
    total_cents = round(ticket["totalAmount"] * 100)
    warnings = list(parsed.warnings)
    if parsed.total_cents is not None and parsed.total_cents != total_cents:
        warnings.append(f"'zu zahlen' ({parsed.total_cents / 100:.2f}) weicht vom API-Betrag ab")
    with conn:
        conn.execute(
            "INSERT INTO receipts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ticket["id"], account, ticket["date"], store.get("id"), store.get("name"), store.get("address"),
             store.get("postalCode"), store.get("locality"), total_cents, parsed.reported_savings_cents,
             parsed.lidlplus_savings_cents, ticket.get("htmlPrintedReceipt"), json.dumps(meta, ensure_ascii=False),
             PARSER_VERSION, "\n".join(warnings) or None, datetime.now().isoformat(timespec="seconds")))
        _write_parsed(conn, ticket["id"], parsed)


def update_parsed(conn: sqlite3.Connection, receipt_id: str, parsed: ParsedReceipt, total_cents: int) -> None:
    """Replace a receipt's parsed content (after a parser change)."""
    warnings = list(parsed.warnings)
    if parsed.total_cents is not None and parsed.total_cents != total_cents:
        warnings.append(f"'zu zahlen' ({parsed.total_cents / 100:.2f}) weicht vom API-Betrag ab")
    with conn:
        conn.execute(
            "UPDATE receipts SET reported_savings_cents = ?, lidlplus_savings_cents = ?, parser_version = ?, "
            "warnings = ? WHERE id = ?",
            (parsed.reported_savings_cents, parsed.lidlplus_savings_cents, PARSER_VERSION,
             "\n".join(warnings) or None, receipt_id))
        _write_parsed(conn, receipt_id, parsed)
