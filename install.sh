#!/usr/bin/env bash
# Installiert aliexpress-coin-collector in einem Debian/Ubuntu-LXC (als root ausfuehren).
# Nicht-interaktiv moeglich: ADB_SERIAL=... DISCORD_WEBHOOK_URL=... ./install.sh
# Erkennt eine aeltere Installation (aliexpress-coins) und uebernimmt .env, Datenbank und ADB-Freigabe.
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "Bitte als root ausfuehren." >&2; exit 1; }

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="${INSTALL_DIR:-/opt/aliexpress-coin-collector}"
OLD="${OLD_INSTALL_DIR:-/opt/aliexpress-coins}"
SVC_USER="coins"
SERVICE="aliexpress-coin-collector"
HAVE_SYSTEMD=0
if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then HAVE_SYSTEMD=1; fi

echo "==> Pakete installieren (adb, tesseract, python3-venv)"
export DEBIAN_FRONTEND=noninteractive
# Ein defektes Fremd-Repository soll die Installation nicht abbrechen: Update-Fehler nur melden.
apt-get update -qq || echo "    Warnung: apt-get update mit Fehlern, versuche trotzdem zu installieren"
apt-get install -y -qq adb tesseract-ocr tesseract-ocr-deu python3-venv rsync >/dev/null

# --- Migration von der alten Installation -----------------------------------------------------
if [ -d "$OLD" ] && [ ! -d "$DEST" ] && [ "$OLD" != "$DEST" ]; then
    echo "==> Migration von $OLD nach $DEST"
    if [ "$HAVE_SYSTEMD" = 1 ]; then
        systemctl stop aliexpress-coins 2>/dev/null || true
        systemctl disable aliexpress-coins 2>/dev/null || true
    fi
    rm -f /etc/systemd/system/aliexpress-coins.service
    pkill -u "$SVC_USER" 2>/dev/null || true   # auch der ADB-Server des Benutzers muss beendet sein
    sleep 1
    mv "$OLD" "$DEST"
    rm -rf "$DEST/.venv" "$DEST/aliexpress_coins"   # venv enthaelt absolute Pfade, altes Paket entfaellt
    if id "$SVC_USER" >/dev/null 2>&1; then usermod -d "$DEST" "$SVC_USER"; fi
    echo "    .env, Datenbank (data/) und ADB-Freigabe (.android/) wurden uebernommen"
fi

echo "==> Benutzer '$SVC_USER' und Verzeichnis $DEST"
id "$SVC_USER" >/dev/null 2>&1 || useradd -r -m -d "$DEST" -s /usr/sbin/nologin "$SVC_USER"
mkdir -p "$DEST"
if [ "$SRC" != "$DEST" ]; then
    rsync -a --exclude .env --exclude data --exclude .venv --exclude .android --exclude __pycache__ \
        --exclude .pytest_cache --exclude .git "$SRC/" "$DEST/"
fi

echo "==> Python-Umgebung"
[ -d "$DEST/.venv" ] || python3 -m venv "$DEST/.venv"
"$DEST/.venv/bin/pip" install -q --upgrade pip
"$DEST/.venv/bin/pip" install -q -r "$DEST/requirements.txt"

echo "==> Konfiguration"
if [ ! -f "$DEST/.env" ]; then
    cp "$DEST/.env.example" "$DEST/.env"
    serial="${ADB_SERIAL:-}"
    webhook="${DISCORD_WEBHOOK_URL:-}"
    if [ -t 0 ]; then
        [ -n "$serial" ] || read -r -p "Geraete-Adresse (z. B. 192.168.1.50:5555): " serial
        [ -n "$webhook" ] || read -r -p "Discord-Webhook-URL (leer lassen zum Ueberspringen): " webhook
    fi
    [ -z "$serial" ] && echo "    WARNUNG: ADB_SERIAL ist noch leer, bitte in $DEST/.env eintragen (z. B. 192.168.1.50:5555)"
    [ -z "$serial" ] || sed -i "s|^ADB_SERIAL=.*|ADB_SERIAL=${serial}|" "$DEST/.env"
    [ -z "$webhook" ] || sed -i "s|^DISCORD_WEBHOOK_URL=.*|DISCORD_WEBHOOK_URL=${webhook}|" "$DEST/.env"
else
    echo "    .env existiert bereits, bleibt unveraendert"
fi
chmod 600 "$DEST/.env"
chown -R "$SVC_USER": "$DEST"

echo "==> systemd-Dienst"
if [ "$HAVE_SYSTEMD" = 1 ]; then
    sed "s|/opt/aliexpress-coin-collector|${DEST}|g" "$DEST/$SERVICE.service" > "/etc/systemd/system/$SERVICE.service"
    systemctl daemon-reload
    echo "    Dienst installiert, aber noch NICHT gestartet"
else
    echo "    systemd nicht verfuegbar, Dienstdatei uebersprungen"
fi

cat <<MSG

Fertig. Zeitzone des Containers: $(date +%Z) (fuer die Zeitfenster sollte es CET/CEST sein).

Naechste Schritte:
  1. Geraet freigeben (Dialog auf dem Geraet mit "Immer zulassen" bestaetigen; bei einer
     migrierten Installation ist das schon erledigt):
       runuser -u $SVC_USER -- adb connect \$(grep ^ADB_SERIAL= $DEST/.env | cut -d= -f2)
  2. Installation pruefen:
       cd $DEST && runuser -u $SVC_USER -- .venv/bin/python -m aliexpress_coin_collector doctor
  3. Ersten echten Lauf machen:
       cd $DEST && runuser -u $SVC_USER -- .venv/bin/python -m aliexpress_coin_collector once --force --no-notify
  4. Wenn das klappt, Dienst starten:
       systemctl enable --now $SERVICE
MSG
