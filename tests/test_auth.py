import json
import stat

import pytest

from kassenbuch import auth
from conftest import fake_jwt


def login(monkeypatch, account: str, sub: str, state: str | None = None) -> None:
    url = auth.start_login(account)
    state = state or url.split("state=")[1].split("&")[0]
    monkeypatch.setattr(auth, "_token_request",
                        lambda payload: {"access_token": fake_jwt(sub), "refresh_token": f"refresh-{sub}", "expires_in": 1200})
    auth.finish_login(account, f"com.lidlplus.app://callback?code=CODE&state={state}")


def test_login_stores_private_token(monkeypatch, data_dir):
    login(monkeypatch, "anna", "S1")

    path = data_dir / "tokens" / "anna.json"
    stored = json.loads(path.read_text())
    assert stored["refresh_token"] == "refresh-S1" and stored["sub"] == "S1" and stored["country"] == "de"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert auth.list_accounts() == ["anna"]
    assert not (data_dir / "tokens" / "anna.pending.json").exists()


def test_wrong_state_is_rejected(monkeypatch):
    with pytest.raises(auth.AuthError, match="state"):
        login(monkeypatch, "anna", "S1", state="forged")


def test_account_mixups_are_rejected(monkeypatch):
    login(monkeypatch, "anna", "S1")
    login(monkeypatch, "anna", "S1")  # logging in again is fine
    with pytest.raises(auth.AuthError, match="bereits als 'anna'"):
        login(monkeypatch, "bob", "S1")
    with pytest.raises(auth.AuthError, match="anderes Lidl-Konto"):
        login(monkeypatch, "anna", "S2")
    login(monkeypatch, "bob", "S2")
    assert auth.list_accounts() == ["anna", "bob"]


def test_refresh_keeps_file_private_and_stores_new_token(monkeypatch, data_dir):
    login(monkeypatch, "anna", "S1")
    path = data_dir / "tokens" / "anna.json"
    path.chmod(0o644)
    monkeypatch.setattr(auth, "_token_request",
                        lambda payload: {"access_token": fake_jwt("S1"), "refresh_token": "rotated", "expires_in": 1200})

    token, country = auth.get_access_token("anna")

    assert country == "de" and token.count(".") == 2
    assert json.loads(path.read_text())["refresh_token"] == "rotated"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_missing_or_broken_token(data_dir):
    with pytest.raises(auth.AuthError, match="Kein Login"):
        auth.get_access_token("anna")
    (data_dir / "tokens").mkdir(parents=True)
    (data_dir / "tokens" / "anna.json").write_text("{broken")
    with pytest.raises(auth.AuthError, match="beschädigt"):
        auth.get_access_token("anna")


@pytest.mark.parametrize("name", ["anna", "konto_2", "a-b"])
def test_valid_account_names(name):
    assert auth.validate_account(name) == name


@pytest.mark.parametrize("name", ["", "../x", "a/b", "foo.pending", "x" * 41])
def test_invalid_account_names(name):
    with pytest.raises(ValueError):
        auth.validate_account(name)
