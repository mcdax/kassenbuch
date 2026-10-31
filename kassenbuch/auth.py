"""
Lidl Plus login like the app does it (OAuth 2 authorization code + PKCE) and refresh-token storage.

The login itself needs a browser (captcha, 2FA). Lidl then redirects to
com.lidlplus.app://callback?code=..., which is exchanged for a long-lived refresh token.
Each account's token lives in <data>/tokens/<account>.json (mode 600).
"""

import base64
import hashlib
import json
import os
import re
import secrets
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import requests

from kassenbuch import settings

AUTH_API = "https://accounts.lidl.com"
CLIENT_ID = "LidlPlusNativeClient"
REDIRECT_URI = "com.lidlplus.app://callback"
SCOPE = "openid profile offline_access lpprofile lpapis"
TIMEOUT = 15


class AuthError(RuntimeError):
    """Login or token refresh failed - usually the account has to log in again."""


def validate_account(name: str) -> str:
    """Account names become file names, so only letters, digits, '-' and '_' are allowed."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", name):
        raise ValueError(f"Ungültiger Kontoname '{name}': nur Buchstaben, Ziffern, '-' und '_' erlaubt")
    return name


def token_path(account: str, suffix: str = "") -> Path:
    return settings.tokens_dir() / f"{validate_account(account)}{suffix}.json"


def list_accounts() -> list[str]:
    """All accounts with a stored refresh token."""
    folder = settings.tokens_dir()
    if not folder.is_dir():
        return []
    return sorted(p.stem for p in folder.glob("*.json") if not p.name.endswith(".pending.json"))


def _write_private(path: Path, data: dict) -> None:
    """Write JSON readable only by the current user, atomically (it contains login secrets)."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    tmp_path = path.with_name(path.name + ".tmp")
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    # The mode above only applies to new files
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)


def _subject(access_token: str) -> str:
    """Lidl account ID ('sub' claim) from an access token, to tell accounts apart."""
    payload = access_token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["sub"]


def _token_request(payload: dict) -> dict:
    try:
        response = requests.post(f"{AUTH_API}/connect/token", auth=(CLIENT_ID, "secret"), data=payload, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise AuthError(f"Lidl-Login nicht erreichbar ({type(e).__name__})") from None
    if response.status_code != 200:
        raise AuthError(f"Token-Anfrage fehlgeschlagen ({response.status_code}): {response.text[:200]}")
    return response.json()


def start_login(account: str, country: str = "de") -> str:
    """Create PKCE secrets for this login and return the URL to open in the browser."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    _write_private(token_path(account, ".pending"), {"verifier": verifier, "state": state, "country": country.lower()})

    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "scope": SCOPE,
        "redirect_uri": REDIRECT_URI,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "Country": country.upper(),
        "language": f"{country.lower()}-{country.upper()}",
    }
    return f"{AUTH_API}/connect/authorize?{urlencode(params)}"


def finish_login(account: str, callback_url: str) -> None:
    """Exchange the code from the callback URL for tokens and store the refresh token."""
    pending_path = token_path(account, ".pending")
    if not pending_path.exists():
        raise AuthError(f"Kein begonnener Login für '{account}'. Zuerst den Login starten.")
    pending = json.loads(pending_path.read_text(encoding="utf-8"))

    query = parse_qs(urlsplit(callback_url.strip()).query)
    if "code" not in query:
        raise AuthError("Die Adresse enthält keinen 'code'-Parameter.")
    if query.get("state", [None])[0] != pending["state"]:
        raise AuthError("Der 'state' passt nicht zu diesem Login. Bitte den Login neu starten.")

    tokens = _token_request({
        "grant_type": "authorization_code",
        "code": query["code"][0],
        "redirect_uri": REDIRECT_URI,
        "code_verifier": pending["verifier"],
    })

    # The browser easily reuses an existing Lidl login, so guard against mixing up accounts
    sub = _subject(tokens["access_token"])
    for other in list_accounts():
        stored_sub = json.loads(token_path(other).read_text(encoding="utf-8")).get("sub")
        if other == account and stored_sub and stored_sub != sub:
            raise AuthError(f"Unter dem Namen '{account}' ist ein anderes Lidl-Konto gespeichert. "
                            f"Anderen Namen wählen oder das Konto zuerst entfernen.")
        if other != account and stored_sub == sub:
            raise AuthError(f"Dieses Lidl-Konto ist bereits als '{other}' gespeichert.")

    _write_private(token_path(account), {
        "refresh_token": tokens["refresh_token"],
        "sub": sub,
        "country": pending["country"],
        "updated": datetime.now().isoformat(timespec="seconds"),
    })
    pending_path.unlink()


def get_access_token(account: str) -> tuple[str, str]:
    """
    Get a fresh access token (valid ~20 minutes) for an account.

    Lidl may rotate the refresh token on use, so a new one is stored right away.

    Returns:
        tuple: (access_token, country)
    """
    path = token_path(account)
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise AuthError(f"Kein Login für '{account}' gespeichert.") from None
    except ValueError:
        raise AuthError(f"{path} ist beschädigt.") from None

    tokens = _token_request({"grant_type": "refresh_token", "refresh_token": stored["refresh_token"]})
    stored["refresh_token"] = tokens.get("refresh_token", stored["refresh_token"])
    stored["sub"] = _subject(tokens["access_token"])
    stored["updated"] = datetime.now().isoformat(timespec="seconds")
    _write_private(path, stored)
    return tokens["access_token"], stored["country"]


def remove_account(account: str) -> None:
    for suffix in ("", ".pending"):
        token_path(account, suffix).unlink(missing_ok=True)
