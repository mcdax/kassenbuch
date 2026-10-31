"""
Notifications via Telegram and/or ntfy, configured by environment variables:

    TELEGRAM_BOT_TOKEN   bot token from @BotFather
    TELEGRAM_CHAT_ID     chat the bot writes to ('kassenbuch telegram-chat-id' finds it)
    NTFY_URL             ntfy topic URL, e.g. https://ntfy.sh/<secret-topic>
"""

import os

import requests

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
TIMEOUT = 10


class NotifyError(RuntimeError):
    pass


def telegram(method: str, **data):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise NotifyError("TELEGRAM_BOT_TOKEN ist nicht gesetzt")
    try:
        result = requests.post(TELEGRAM_API.format(token=token, method=method), data=data, timeout=TIMEOUT).json()
    except (requests.RequestException, ValueError) as e:
        # The exception text contains the URL and with it the bot token, so only the error type is shown
        raise NotifyError(f"Telegram nicht erreichbar ({type(e).__name__})") from None
    if not result.get("ok"):
        raise NotifyError(f"Telegram: {result.get('description', 'unbekannter Fehler')}")
    return result["result"]


def telegram_chats() -> dict[int, str]:
    """Chats that recently wrote to the bot (for finding TELEGRAM_CHAT_ID)."""
    chats = {}
    for update in telegram("getUpdates"):
        chat = (update.get("message") or {}).get("chat")
        if chat:
            chats[chat["id"]] = chat.get("title") or chat.get("username") or chat.get("first_name", "")
    return chats


def is_configured() -> bool:
    return bool(os.environ.get("NTFY_URL")
                or (os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID")))


def send(message: str, log=print) -> bool:
    """
    Send a message to every configured channel. Errors are logged, never raised.

    Returns:
        bool: True if every configured channel accepted the message
    """
    ok = True
    if os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"):
        try:
            telegram("sendMessage", chat_id=os.environ["TELEGRAM_CHAT_ID"], text=message)
        except NotifyError as e:
            log(f"Telegram-Benachrichtigung fehlgeschlagen: {e}")
            ok = False

    if ntfy_url := os.environ.get("NTFY_URL"):
        try:
            requests.post(ntfy_url, data=message.encode(), headers={"Title": "Kassenbuch"},
                          timeout=TIMEOUT).raise_for_status()
        except requests.RequestException as e:
            # The URL contains the secret topic
            log(f"ntfy-Benachrichtigung fehlgeschlagen ({type(e).__name__})")
            ok = False
    return ok
