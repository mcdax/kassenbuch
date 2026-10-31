import pytest

from kassenbuch import db, poller
from kassenbuch.sync import NewReceipt, SyncResult


def receipt(n: int, total: int = 2345, savings: int = 0) -> NewReceipt:
    return NewReceipt(f"R{n}", "anna", f"2026-10-{n:02d}T15:14:00", "Lidl Musterstadt", total, 12, savings)


def test_one_message_per_purchase_oldest_first():
    messages = poller.new_receipt_messages("anna", [receipt(30, 100), receipt(31, 2345, savings=120)], False)
    assert messages == [
        "🛒 Neuer Einkauf (anna): 1,00 €\n30.10.2026 15:14 · Lidl Musterstadt · 12 Artikel",
        "🛒 Neuer Einkauf (anna): 23,45 €\n31.10.2026 15:14 · Lidl Musterstadt · 12 Artikel · 1,20 € gespart",
    ]


def test_many_receipts_become_one_summary():
    messages = poller.new_receipt_messages("anna", [receipt(n) for n in range(1, 14)], False)
    assert len(messages) == 1
    assert messages[0].startswith("🧾 anna: 13 neue Kassenbons\n23,45 € · 13.10.2026")
    assert messages[0].endswith("… und 3 weitere")


def test_first_import_is_one_summary():
    assert poller.new_receipt_messages("anna", [receipt(1)], True) == ["🛒 anna: 1 Kassenbon importiert"]


@pytest.fixture
def polling(monkeypatch):
    """poll_account with a fake sync: state['new'] receipts are 'imported' on the next call."""
    state = {"new": [], "ok": True, "calls": [], "sent": []}

    def fake_sync(conn, account, full, log):
        state["calls"].append(full)
        if not state["ok"]:
            raise RuntimeError("Lidl nicht erreichbar")
        db.ensure_account(conn, account, "de")
        for r in state["new"]:
            conn.execute("INSERT INTO receipts (id, account, purchased_at, total_cents, meta_json, parser_version, "
                         "imported_at) VALUES (?, ?, ?, ?, '{}', 1, '')", (r.id, account, r.purchased_at, r.total_cents))
        conn.commit()
        result = SyncResult(account, new=list(state["new"]))
        state["new"] = []
        return result

    monkeypatch.setattr(poller, "sync_account", fake_sync)
    monkeypatch.setattr(poller.notify, "send", lambda message, log=None: state["sent"].append(message))
    monkeypatch.setenv("ACCOUNTS", "anna")
    return db.connect(), state


def test_first_poll_is_full_and_summarised_then_single_messages(polling):
    conn, state = polling
    state["new"] = [receipt(1), receipt(2)]
    poller.poll_all(conn, {}, full=False)
    assert state["calls"] == [True]  # no receipts yet: full sync even on a quick check
    assert state["sent"] == ["🛒 anna: 2 Kassenbons importiert"]

    state["new"] = [receipt(3)]
    poller.poll_all(conn, {}, full=False)
    assert state["calls"] == [True, False]
    assert state["sent"][-1].startswith("🛒 Neuer Einkauf (anna): 23,45 €")


def test_new_receipt_messages_can_be_switched_off(polling, monkeypatch):
    conn, state = polling
    monkeypatch.setenv("NOTIFY_NEW_RECEIPTS", "false")
    state["new"] = [receipt(1)]
    poller.poll_all(conn, {}, full=True)
    assert state["sent"] == []


def test_alert_only_after_repeated_failures(polling):
    conn, state = polling
    counts = {}
    state["ok"] = False
    poller.poll_all(conn, counts, full=False)
    poller.poll_all(conn, counts, full=False)
    assert state["sent"] == []
    assert poller.poll_all(conn, counts, full=False) == {"anna"}
    assert len(state["sent"]) == 1 and "wiederholt fehl" in state["sent"][0]
    poller.poll_all(conn, counts, full=False)
    assert len(state["sent"]) == 1

    state["ok"] = True
    assert poller.poll_all(conn, counts, full=False) == set()
    assert "funktioniert wieder" in state["sent"][-1]

    # A single blip stays silent
    state["ok"] = False
    poller.poll_all(conn, counts, full=False)
    state["ok"] = True
    poller.poll_all(conn, counts, full=False)
    assert len(state["sent"]) == 2
