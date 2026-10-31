"""Command line: kassenbuch <command> (see 'kassenbuch --help')."""

import argparse
import os
import sys

from kassenbuch import auth, db, sync


def cmd_login(args) -> None:
    from kassenbuch.login_window import login_window
    if login_window(args.account, args.country):
        print(f"✓ Konto '{args.account}' gespeichert. Kassenbons holen: kassenbuch sync --account {args.account}")
    else:
        sys.exit(1)


def cmd_login_start(args) -> None:
    print(auth.start_login(args.account, args.country))
    print("\nIm Browser mit offenen Entwicklertools (Netzwerk, 'Protokoll beibehalten') öffnen, einloggen und die "
          "Adresse com.lidlplus.app://callback?code=... kopieren. Dann innerhalb von ~5 Minuten:\n"
          f"  kassenbuch login-finish {args.account} '<adresse>'", file=sys.stderr)


def cmd_login_finish(args) -> None:
    auth.finish_login(args.account, args.callback_url)
    print(f"✓ Konto '{args.account}' gespeichert")


def cmd_accounts(args) -> None:
    conn = db.connect()
    counts = dict(conn.execute("SELECT account, COUNT(*) FROM receipts GROUP BY account").fetchall())
    for account in auth.list_accounts():
        print(f"{account}: {counts.get(account, 0)} Kassenbons")


def cmd_sync(args) -> None:
    conn = db.connect()
    accounts = [args.account] if args.account else auth.list_accounts()
    if not accounts:
        sys.exit("Keine Konten. Zuerst einloggen: kassenbuch login NAME")
    failed = False
    for account in accounts:
        result = sync.sync_account(conn, account, full=not args.quick)
        print(f"✓ {account}: {len(result.new)} neu, {len(result.failed)} fehlgeschlagen, "
              f"{result.with_warnings} mit Hinweisen")
        failed |= not result.ok
    if failed:
        sys.exit(1)


def cmd_reparse(args) -> None:
    count, with_warnings = sync.reparse(db.connect(), all_receipts=args.all)
    print(f"✓ {count} Kassenbons neu ausgewertet, {with_warnings} mit Hinweisen")


def cmd_check(args) -> None:
    rows = sync.receipts_with_warnings(db.connect())
    for row in rows:
        print(f"{row['purchased_at'][:16]}  {row['account']}  {row['id']}\n    " + row["warnings"].replace("\n", "\n    "))
    print(f"{len(rows)} Kassenbons mit Hinweisen")


def cmd_poll(args) -> None:
    from kassenbuch import poller
    if args.once:
        failed = poller.poll_all(db.connect(), {}, full=True)
        sys.exit(1 if failed else 0)
    poller.run_forever()


def cmd_serve(args) -> None:
    import uvicorn
    from kassenbuch.web.app import create_app
    if args.host not in ("127.0.0.1", "localhost") and not os.environ.get("WEB_PASSWORD"):
        print("⚠ Die Web-App ist ohne WEB_PASSWORD von anderen Rechnern erreichbar.", file=sys.stderr)
    uvicorn.run(create_app(), host=args.host, port=args.port, proxy_headers=True, log_level="warning")


def cmd_notify_test(args) -> None:
    from kassenbuch import notify
    if not notify.is_configured():
        sys.exit("✗ Kein Kanal eingerichtet: TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID oder NTFY_URL setzen")
    if not notify.send("✅ Testnachricht vom Kassenbuch"):
        sys.exit(1)
    print("✓ Testnachricht gesendet")


def cmd_telegram_chat_id(args) -> None:
    from kassenbuch import notify
    try:
        chats = notify.telegram_chats()
    except notify.NotifyError as e:
        sys.exit(f"✗ {e}")
    if not chats:
        sys.exit("✗ Noch keine Nachricht an den Bot gefunden. Schreib ihm in Telegram z. B. /start und versuch es erneut.")
    for chat_id, name in chats.items():
        print(f"TELEGRAM_CHAT_ID={chat_id}   ({name})")


def main() -> None:
    parser = argparse.ArgumentParser(prog="kassenbuch", description="Lidl Plus Kassenbons importieren und auswerten")
    commands = parser.add_subparsers(dest="command", required=True)

    def account_arg(p):
        p.add_argument("account", type=auth.validate_account, metavar="KONTO", help="Name für das Konto, z. B. anna")
        p.add_argument("--country", default="de", help="Land des Lidl-Plus-Kontos (Standard: de)")

    p = commands.add_parser("login", help="Konto im Login-Fenster anmelden (benötigt PySide6)")
    account_arg(p)
    p.set_defaults(func=cmd_login)

    p = commands.add_parser("login-start", help="Login ohne Fenster beginnen: gibt den Login-Link aus")
    account_arg(p)
    p.set_defaults(func=cmd_login_start)

    p = commands.add_parser("login-finish", help="Login ohne Fenster abschließen")
    p.add_argument("account", type=auth.validate_account, metavar="KONTO")
    p.add_argument("callback_url", metavar="ADRESSE", help="com.lidlplus.app://callback?code=...")
    p.set_defaults(func=cmd_login_finish)

    p = commands.add_parser("accounts", help="Konten und Anzahl der Kassenbons anzeigen")
    p.set_defaults(func=cmd_accounts)

    p = commands.add_parser("sync", help="Neue Kassenbons herunterladen")
    p.add_argument("--account", type=auth.validate_account, metavar="KONTO", help="nur dieses Konto")
    p.add_argument("--quick", action="store_true", help="nur die neuesten 25 Kassenbons prüfen")
    p.set_defaults(func=cmd_sync)

    p = commands.add_parser("reparse", help="Gespeicherte Kassenbons neu auswerten (nach einem Update)")
    p.add_argument("--all", action="store_true", help="alle, nicht nur die mit älterer Parser-Version")
    p.set_defaults(func=cmd_reparse)

    p = commands.add_parser("check", help="Kassenbons anzeigen, die nicht sauber aufgehen")
    p.set_defaults(func=cmd_check)

    p = commands.add_parser("poll", help="Dauerhaft nach neuen Einkäufen sehen und benachrichtigen")
    p.add_argument("--once", action="store_true", help="einmal voll abgleichen und beenden")
    p.set_defaults(func=cmd_poll)

    p = commands.add_parser("serve", help="Web-Oberfläche starten")
    p.add_argument("--host", default="127.0.0.1", help="Adresse (Standard: nur dieser Rechner)")
    p.add_argument("--port", type=int, default=8080)
    p.set_defaults(func=cmd_serve)

    p = commands.add_parser("notify-test", help="Testnachricht an alle eingerichteten Kanäle senden")
    p.set_defaults(func=cmd_notify_test)

    p = commands.add_parser("telegram-chat-id", help="Chat-ID für TELEGRAM_CHAT_ID herausfinden")
    p.set_defaults(func=cmd_telegram_chat_id)

    args = parser.parse_args()
    try:
        args.func(args)
    except (auth.AuthError, sync.ApiError, ValueError) as e:
        sys.exit(f"✗ {e}")


if __name__ == "__main__":
    main()
