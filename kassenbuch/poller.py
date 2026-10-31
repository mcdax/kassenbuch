"""
Background polling: a quick check for new purchases every POLL_INTERVAL_MINUTES (default 15)
and a full sync every FULL_SYNC_HOURS (default 6, and on start) that fills any gaps.

Environment:
    ACCOUNTS               comma-separated accounts to poll (default: all logged-in accounts)
    POLL_INTERVAL_MINUTES  minutes between quick checks (default 15)
    FULL_SYNC_HOURS        hours between full syncs (default 6)
    NOTIFY_NEW_RECEIPTS    message for every new purchase (default true)
    plus the notification settings in notify.py
"""

import os
import random
import time
from datetime import datetime

from kassenbuch import auth, db, notify, settings
from kassenbuch.sync import NewReceipt, sync_account

# More new receipts at once than this (e.g. after downtime) get one summary instead of a message each
MAX_SINGLE_MESSAGES = 5
MAX_LISTED_RECEIPTS = 10
# Single failures (e.g. a network blip) are retried silently; alert only when an account keeps failing
FAILURES_BEFORE_ALERT = 3


def log(message: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}", flush=True)


def euro(cents: int) -> str:
    return f"{cents / 100:.2f} €".replace(".", ",")


def _details(receipt: NewReceipt) -> str:
    when = datetime.fromisoformat(receipt.purchased_at).strftime("%d.%m.%Y %H:%M")
    details = f"{when} · {receipt.store_name or 'Lidl'} · {receipt.items_count} Artikel"
    return details + (f" · {euro(receipt.savings_cents)} gespart" if receipt.savings_cents > 0 else "")


def new_receipt_messages(account: str, receipts: list[NewReceipt], first_import: bool) -> list[str]:
    """
    Messages for new receipts (given oldest first), normally one per purchase:
        🛒 Neuer Einkauf (anna): 23,45 €
        31.10.2026 15:14 · Lidl Musterstadt · 12 Artikel · 1,20 € gespart
    """
    if first_import:
        return [f"🛒 {account}: {len(receipts)} Kassenbon{'' if len(receipts) == 1 else 's'} importiert"]
    if len(receipts) > MAX_SINGLE_MESSAGES:
        newest = receipts[::-1][:MAX_LISTED_RECEIPTS]
        lines = [f"🧾 {account}: {len(receipts)} neue Kassenbons"]
        lines += [f"{euro(r.total_cents)} · {_details(r)}" for r in newest]
        if len(receipts) > MAX_LISTED_RECEIPTS:
            lines.append(f"… und {len(receipts) - MAX_LISTED_RECEIPTS} weitere")
        return ["\n".join(lines)]
    return [f"🛒 Neuer Einkauf ({account}): {euro(r.total_cents)}\n{_details(r)}" for r in receipts]


def poll_account(conn, account: str, full: bool) -> bool:
    first_import = not conn.execute("SELECT 1 FROM receipts WHERE account = ? LIMIT 1", (account,)).fetchone()
    result = sync_account(conn, account, full=full or first_import, log=log)
    if result.new and settings.env_bool("NOTIFY_NEW_RECEIPTS", True):
        for message in new_receipt_messages(account, result.new, first_import):
            notify.send(message, log=log)
    return result.ok


def poll_all(conn, fail_counts: dict[str, int], full: bool) -> set[str]:
    """
    Poll every account once.

    Args:
        fail_counts: consecutive failures per account, kept between rounds

    Returns:
        set: accounts that failed in this round
    """
    configured = os.environ.get("ACCOUNTS")
    accounts = [a.strip() for a in configured.split(",") if a.strip()] if configured else auth.list_accounts()
    if not accounts:
        log("Keine Konten. Zuerst einloggen: kassenbuch login NAME")

    failed = set()
    for account in accounts:
        log(f"--- {account} ({'voller Abgleich' if full else 'kurze Prüfung'}) ---")
        try:
            ok = poll_account(conn, account, full)
        except Exception as e:  # AuthError, ApiError or anything unexpected: retry next round
            log(f"Fehler: {e}")
            ok = False

        if ok:
            if fail_counts.get(account, 0) >= FAILURES_BEFORE_ALERT:
                notify.send(f"✅ Abruf für '{account}' funktioniert wieder.", log=log)
            fail_counts[account] = 0
        else:
            failed.add(account)
            fail_counts[account] = fail_counts.get(account, 0) + 1
            if fail_counts[account] == FAILURES_BEFORE_ALERT:
                notify.send(f"⚠️ Abruf für '{account}' schlägt wiederholt fehl (Details im Log). "
                            f"Falls der Login abgelaufen ist: kassenbuch login {account}", log=log)
    return failed


def run_forever() -> None:
    conn = db.connect()
    interval = settings.env_int("POLL_INTERVAL_MINUTES", 15) * 60
    full_every = settings.env_int("FULL_SYNC_HOURS", 6) * 3600
    fail_counts: dict[str, int] = {}
    last_full = None
    while True:
        full = last_full is None or time.monotonic() - last_full >= full_every
        poll_all(conn, fail_counts, full)
        if full:
            last_full = time.monotonic()
        # A little jitter so requests don't always hit Lidl at the same second
        delay = interval + random.uniform(0, min(60, interval / 10))
        log(f"Nächster Abruf in {delay / 60:.0f} min")
        time.sleep(delay)
