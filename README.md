# Kassenbuch

Holt deine digitalen Kassenbons aus Lidl Plus, wertet sie aus und meldet jeden neuen Einkauf per Telegram oder ntfy.

- **Import** aller Kassenbons über die API der Lidl Plus App, ohne Browser-Cookies
- **Genaue Auswertung**: Artikel, Wiegeware, Pfand und Pfandrückgabe, alle Rabattarten (Aktionen, Lidl Plus, Coupons, Sticker), Zahlarten und Steuersätze. Jeder Bon wird geprüft: Artikel plus Rabatte müssen den bezahlten Betrag ergeben.
- **Web-Oberfläche**: Ausgaben pro Monat, Ersparnisse, Preisverlauf pro Produkt, alle Einkäufe mit Original-Bon (druckbar bzw. als PDF)
- **Mehrere Konten** getrennt oder zusammen auswerten, z. B. für einen Haushalt
- **Benachrichtigung** nach jedem Einkauf mit dem Gesamtbetrag

> [!IMPORTANT]
> Die Lidl Plus API ist inoffiziell und nicht dokumentiert. Sie kann sich jederzeit ändern. Dieses Projekt hat nichts mit Lidl zu tun.

## Installation

Benötigt Python 3.11 oder neuer.

```bash
python -m venv .venv
.venv/bin/pip install -e '.[login]'   # [login] = Login-Fenster für den Desktop (PySide6, groß)
```

Ohne Desktop (z. B. auf einem Server) reicht `pip install -e .`, der Login läuft dann über `login-start`/`login-finish` (siehe unten).

Daten und Logins liegen in `./data` (änderbar mit `KASSENBUCH_DATA`): die Datenbank in `data/db/`, die Logins in `data/tokens/` (nur für dich lesbar).

## Konto einloggen

```bash
kassenbuch login anna
```

Ein Fenster mit dem Lidl-Login öffnet sich. Melde dich ganz normal an (inkl. Captcha und Bestätigungscode). Danach speichert das Fenster einen dauerhaften Zugang und schließt sich. Jedes Fenster startet ohne Cookies, so kann ein zweites Konto nicht versehentlich den Login des ersten bekommen. Ein Lidl-Konto lässt sich nicht unter zwei Namen speichern.

**Ohne Fenster** (z. B. auf einem Server):

```bash
kassenbuch login-start anna
```

Den ausgegebenen Link in einem Browser mit offenen Entwicklertools öffnen (Reiter *Netzwerk*, *Protokoll beibehalten*) und einloggen. Am Ende leitet Lidl auf `com.lidlplus.app://callback?code=…` weiter. Diese Adresse aus dem Netzwerk-Reiter kopieren und innerhalb von etwa 5 Minuten:

```bash
kassenbuch login-finish anna 'com.lidlplus.app://callback?code=...'
```

Kontonamen dürfen nur Buchstaben, Ziffern, `-` und `_` enthalten. Für Konten außerhalb Deutschlands: `--country nl` usw.

## Benutzung

```bash
kassenbuch sync                 # alle neuen Kassenbons aller Konten holen
kassenbuch sync --quick         # nur die neuesten 25 prüfen
kassenbuch serve                # Web-Oberfläche auf http://127.0.0.1:8080
kassenbuch poll                 # dauerhaft nach neuen Einkäufen sehen und benachrichtigen
kassenbuch accounts             # Konten und Anzahl der Kassenbons
kassenbuch check                # Kassenbons, die nicht sauber aufgehen
kassenbuch reparse              # gespeicherte Kassenbons nach einem Update neu auswerten
```

Das Original-HTML jedes Bons wird gespeichert. Verbessert sich die Auswertung, wertet `kassenbuch reparse` alles neu aus, ohne erneuten Download.

## Docker

```bash
cp .env.example .env && chmod 600 .env    # Passwort, Telegram, ntfy eintragen
docker compose up -d --build
```

- **poller** prüft alle 15 Minuten, ob es neue Einkäufe gibt, und gleicht alle 6 Stunden (und beim Start) vollständig ab
- **web** läuft auf http://127.0.0.1:8503 (Port über `WEB_PORT`), sieht nur die Datenbank, nicht die Logins

In `docker-compose.yml` bei `user:` die eigene UID:GID eintragen (`id -u`, `id -g`), damit die Dateien in `./data` dir gehören.

Befehle im Container, z. B. ein Konto auf einem Server einloggen:

```bash
docker compose run --rm poller login-start anna
docker compose run --rm poller login-finish anna 'com.lidlplus.app://callback?code=...'
docker compose restart poller     # sofort abrufen
docker compose logs -f poller
```

Oder am Desktop einloggen und `data/tokens/anna.json` auf den Server verschieben.

## Benachrichtigungen

Nach jedem neuen Einkauf (meist innerhalb von 15 Minuten, nachdem der Bon in Lidl Plus erscheint):

```text
🛒 Neuer Einkauf (anna): 23,45 €
31.10.2026 15:14 · Lidl Musterstadt · 12 Artikel · 1,20 € gespart
```

Mehr als 5 neue Bons auf einmal kommen als eine Sammelnachricht. Schlägt ein Konto 3-mal hintereinander fehl (z. B. weil der Login abgelaufen ist), gibt es eine Warnung und später eine Entwarnung.

**Telegram:**

1. Mit [@BotFather](https://t.me/BotFather) einen Bot anlegen (`/newbot`), Token in `.env` als `TELEGRAM_BOT_TOKEN` eintragen.
2. Dem Bot `/start` schreiben, dann `docker compose run --rm poller telegram-chat-id` und die Ausgabe in `.env` eintragen.
3. `docker compose run --rm poller notify-test`, dann `docker compose up -d`.

**ntfy:** `NTFY_URL=https://ntfy.sh/<geheimes-topic>` in `.env`.

Mit `NOTIFY_NEW_RECEIPTS=false` entfallen die Einkaufsnachrichten. Die Nachrichten laufen über die Server von Telegram bzw. ntfy.

## Sicherheit

- `data/tokens/` gewährt Zugriff auf dein Lidl-Konto. Nie teilen oder einchecken (steht in `.gitignore`).
- Die Web-Oberfläche ist standardmäßig nur auf diesem Rechner erreichbar. Wer sie im Netz freigibt, setzt `WEB_PASSWORD` und stellt sie hinter einen Reverse-Proxy mit HTTPS (dann `WEB_HTTPS_ONLY=true`).
- Die Oberfläche lädt nichts von fremden Servern; Diagramme werden auf dem Server erzeugt.

## Entwicklung

```bash
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

Die Tests greifen nie auf das Netz zu. Der Beispiel-Bon in `tests/fixtures/` ist anonymisiert.

## Lizenz

MIT, siehe [LICENSE](LICENSE).
