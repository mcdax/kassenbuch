"""Read-only analyses for the web interface. Amounts are cents; 'account=None' means all accounts."""

import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

DISCOUNT_KINDS = {
    "lidl_plus": "Lidl Plus",
    "promotion": "Aktionen",
    "sticker": "Sticker (MHD)",
    "coupon": "Coupons",
    "other": "Sonstige",
}

# Net amount of an article after its own discounts
ITEM_NET_SQL = """
    SELECT i.receipt_id, i.position, i.article_id, i.name, i.quantity, i.unit, i.unit_price_cents,
           i.amount_cents, i.is_deposit, r.purchased_at, r.account,
           i.amount_cents + COALESCE((SELECT SUM(d.amount_cents) FROM discounts d
                                      WHERE d.receipt_id = i.receipt_id AND d.item_position = i.position), 0)
               AS net_cents
    FROM items i JOIN receipts r ON r.id = i.receipt_id
"""


@dataclass
class Period:
    key: str
    label: str
    start: str | None  # inclusive, ISO date
    end: str | None  # exclusive, ISO date


def period(key: str | None, today: date | None = None) -> Period:
    """'12m' (default, last 12 months incl. the current one), 'all' or a year like '2025'."""
    today = today or date.today()
    if key == "all":
        return Period("all", "Gesamter Zeitraum", None, None)
    if key and key.isdigit() and len(key) == 4:
        return Period(key, key, f"{key}-01-01", f"{int(key) + 1}-01-01")
    year, month = today.year, today.month - 11
    if month < 1:
        year, month = year - 1, month + 12
    next_year, next_month = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
    return Period("12m", "Letzte 12 Monate", f"{year}-{month:02d}-01", f"{next_year}-{next_month:02d}-01")


def _filter(account: str | None, p: Period | None, alias: str = "r") -> tuple[str, list]:
    clauses, params = [], []
    if account:
        clauses.append(f"{alias}.account = ?")
        params.append(account)
    if p and p.start:
        clauses.append(f"{alias}.purchased_at >= ?")
        params.append(p.start)
    if p and p.end:
        clauses.append(f"{alias}.purchased_at < ?")
        params.append(p.end)
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def accounts(conn: sqlite3.Connection) -> list[str]:
    return [row[0] for row in conn.execute("SELECT name FROM accounts ORDER BY name")]


def years(conn: sqlite3.Connection) -> list[str]:
    return [row[0] for row in conn.execute(
        "SELECT DISTINCT substr(purchased_at, 1, 4) FROM receipts ORDER BY 1 DESC")]


def _previous_month(month: str) -> str:
    year, number = int(month[:4]), int(month[5:7])
    return f"{year - 1}-12" if number == 1 else f"{year}-{number - 1:02d}"


def _months(first: str, last: str) -> list[str]:
    """All months 'YYYY-MM' from first to last."""
    year, month = int(first[:4]), int(first[5:7])
    months = []
    while f"{year}-{month:02d}" <= last:
        months.append(f"{year}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


@dataclass
class Overview:
    spent_cents: int = 0
    receipts: int = 0
    savings_by_kind: dict[str, int] = field(default_factory=dict)
    deposit_net_cents: int = 0
    monthly: list[tuple[str, int]] = field(default_factory=list)
    by_tax_rate: list[tuple[float, int]] = field(default_factory=list)
    top_by_spend: list[dict] = field(default_factory=list)
    top_by_count: list[dict] = field(default_factory=list)

    @property
    def average_cents(self) -> int:
        return round(self.spent_cents / self.receipts) if self.receipts else 0

    @property
    def savings_cents(self) -> int:
        return sum(self.savings_by_kind.values())


def overview(conn: sqlite3.Connection, account: str | None, p: Period) -> Overview:
    where, params = _filter(account, p)
    o = Overview()
    o.receipts, o.spent_cents = conn.execute(
        f"SELECT COUNT(*), COALESCE(SUM(total_cents), 0) FROM receipts r{where}", params).fetchone()

    for kind, cents in conn.execute(
            f"SELECT d.kind, -SUM(d.amount_cents) FROM discounts d JOIN receipts r ON r.id = d.receipt_id{where} "
            "GROUP BY d.kind ORDER BY 2 DESC", params):
        o.savings_by_kind[kind] = cents
    o.deposit_net_cents = conn.execute(
        f"SELECT COALESCE(SUM(i.amount_cents), 0) FROM items i JOIN receipts r ON r.id = i.receipt_id{where}"
        + (" AND" if where else " WHERE") + " i.is_deposit = 1", params).fetchone()[0]

    totals = dict(conn.execute(
        f"SELECT substr(purchased_at, 1, 7), SUM(total_cents) FROM receipts r{where} GROUP BY 1", params).fetchall())
    if totals:
        first = p.start[:7] if p.start else min(totals)
        # Period ends are exclusive and always the 1st of a month; never show months in the future
        last = min(_previous_month(p.end[:7]), date.today().isoformat()[:7]) if p.end else max(totals)
        o.monthly = [(m, totals.get(m, 0)) for m in _months(first, last)]

    o.by_tax_rate = [(rate, cents) for rate, cents in conn.execute(
        f"SELECT t.rate, SUM(t.gross_cents) FROM taxes t JOIN receipts r ON r.id = t.receipt_id{where} "
        "GROUP BY t.rate ORDER BY t.rate", params)]

    products_ = products(conn, account, p)
    o.top_by_spend = sorted(products_, key=lambda x: -x["spent_cents"])[:10]
    o.top_by_count = sorted(products_, key=lambda x: (-x["purchases"], -x["spent_cents"]))[:10]
    return o


def receipts(conn: sqlite3.Connection, account: str | None, p: Period) -> list[sqlite3.Row]:
    where, params = _filter(account, p)
    return conn.execute(f"""
        SELECT r.id, r.account, r.purchased_at, r.store_name, r.store_locality, r.total_cents, r.warnings,
               (SELECT COUNT(*) FROM items WHERE receipt_id = r.id AND is_deposit = 0) AS items,
               -(SELECT COALESCE(SUM(amount_cents), 0) FROM discounts WHERE receipt_id = r.id) AS savings_cents
        FROM receipts r{where} ORDER BY r.purchased_at DESC""", params).fetchall()


def receipt(conn: sqlite3.Connection, receipt_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM receipts WHERE id = ?", (receipt_id,)).fetchone()
    if row is None:
        return None
    discounts = conn.execute("SELECT * FROM discounts WHERE receipt_id = ? ORDER BY position", (receipt_id,)).fetchall()
    by_item = defaultdict(list)
    for d in discounts:
        by_item[d["item_position"]].append(d)
    items = [dict(i) | {"discounts": by_item.get(i["position"], [])}
             for i in conn.execute("SELECT * FROM items WHERE receipt_id = ? ORDER BY position", (receipt_id,))]
    return {
        "receipt": row,
        "items": items,
        "unassigned_discounts": by_item.get(None, []),
        "savings_cents": -sum(d["amount_cents"] for d in discounts),
        "payments": conn.execute("SELECT * FROM payments WHERE receipt_id = ? ORDER BY position", (receipt_id,)).fetchall(),
        "taxes": conn.execute("SELECT * FROM taxes WHERE receipt_id = ? ORDER BY rate", (receipt_id,)).fetchall(),
    }


def products(conn: sqlite3.Connection, account: str | None, p: Period | None = None, search: str = "") -> list[dict]:
    """Articles (by Lidl article ID) with purchases, quantity, net spend and price development."""
    where, params = _filter(account, p)
    rows = conn.execute(
        f"SELECT * FROM ({ITEM_NET_SQL}{where}) WHERE is_deposit = 0 AND article_id IS NOT NULL "
        "ORDER BY purchased_at", params).fetchall()
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["article_id"]].append(row)

    result = []
    for article_id, purchases in grouped.items():
        latest = purchases[-1]
        if search and search.lower() not in latest["name"].lower():
            continue
        first_price, last_price = purchases[0]["unit_price_cents"], latest["unit_price_cents"]
        result.append({
            "article_id": article_id,
            "name": latest["name"],
            "unit": latest["unit"],
            "purchases": len({r["receipt_id"] for r in purchases}),
            "quantity": sum(r["quantity"] for r in purchases),
            "spent_cents": sum(r["net_cents"] for r in purchases),
            "last_price_cents": last_price,
            "price_change": (last_price - first_price) / first_price if first_price and last_price else None,
            "last_purchase": latest["purchased_at"],
        })
    return result


def product(conn: sqlite3.Connection, article_id: str, account: str | None) -> dict | None:
    where, params = _filter(account, None)
    rows = conn.execute(
        f"SELECT * FROM ({ITEM_NET_SQL}{where}) WHERE article_id = ? ORDER BY purchased_at",
        params + [article_id]).fetchall()
    if not rows:
        return None
    purchases = [dict(r) | {"paid_unit_cents": round(r["net_cents"] / r["quantity"]) if r["quantity"] else None}
                 for r in rows]
    return {
        "article_id": article_id,
        "name": rows[-1]["name"],
        "unit": rows[-1]["unit"],
        "purchases": purchases,
        "spent_cents": sum(r["net_cents"] for r in rows),
        "quantity": sum(r["quantity"] for r in rows),
    }
