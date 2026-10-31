import pytest
import requests

from kassenbuch import notify

TOKEN = "123456:SECRET-bot-token"


class Response:
    def __init__(self, payload, status=200):
        self.payload, self.status_code = payload, status

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} for url: https://ntfy.sh/secret-topic")


@pytest.fixture
def channels(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "4242")
    monkeypatch.setenv("NTFY_URL", "https://ntfy.sh/secret-topic")
    calls, mode = [], {"telegram": "ok", "ntfy": "ok"}

    def post(url, data=None, headers=None, timeout=None):
        calls.append((url, data))
        if "telegram" in url:
            if mode["telegram"] == "down":
                raise requests.ConnectionError(f"Max retries exceeded with url: /bot{TOKEN}/sendMessage")
            if mode["telegram"] == "error":
                return Response({"ok": False, "description": "Bad Request: chat not found"})
            if url.endswith("getUpdates"):
                return Response({"ok": True, "result": [{"message": {"chat": {"id": 4242, "first_name": "Anna"}}}]})
            return Response({"ok": True, "result": {}})
        return Response({}, 500 if mode["ntfy"] == "down" else 200)

    monkeypatch.setattr(requests, "post", post)
    return calls, mode


def test_sends_to_all_channels(channels):
    calls, _ = channels
    assert notify.send("Hallo", log=lambda m: None)
    assert calls[0] == (f"https://api.telegram.org/bot{TOKEN}/sendMessage", {"chat_id": "4242", "text": "Hallo"})
    assert calls[1] == ("https://ntfy.sh/secret-topic", "Hallo".encode())


def test_failures_are_logged_without_secrets(channels):
    _, mode = channels
    mode.update(telegram="down", ntfy="down")
    logs = []
    assert not notify.send("Hallo", log=logs.append)
    assert len(logs) == 2
    assert all(TOKEN not in line and "secret-topic" not in line for line in logs)


def test_telegram_error_description_is_logged(channels):
    _, mode = channels
    mode["telegram"] = "error"
    logs = []
    notify.send("Hallo", log=logs.append)
    assert "chat not found" in logs[0]


def test_telegram_chat_ids(channels):
    assert notify.telegram_chats() == {4242: "Anna"}


def test_nothing_configured(monkeypatch):
    for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "NTFY_URL"):
        monkeypatch.delenv(name, raising=False)
    assert not notify.is_configured()
    assert notify.send("Hallo")
