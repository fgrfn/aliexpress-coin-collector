#!/usr/bin/env bash
# Installiert aliexpress-coin-collector in einem Debian/Ubuntu-LXC (als root ausfuehren).
# Nicht-interaktiv moeglich: ADB_SERIAL=... DISCORD_WEBHOOK_URL=... ./install.sh
# Erkennt eine aeltere Installation (aliexpress-coins) und uebernimmt .env, Datenbank und ADB-Freigabe.
# Aufrufformen und Umgebungsvariablen erklaert ./install.sh --help
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="${INSTALL_DIR:-/opt/aliexpress-coin-collector}"
OLD="${OLD_INSTALL_DIR:-/opt/aliexpress-coins}"
SVC_USER="coins"
SERVICE="aliexpress-coin-collector"

usage() {
    cat <<'USAGE'
aliexpress-coin-collector - Installationsskript (als root ausfuehren)

Aufruf:
  ./install.sh              Installiert neu oder aktualisiert eine vorhandene Installation
  ./install.sh --uninstall  Stoppt den Dienst und entfernt die systemd-Unit (Daten bleiben erhalten)
  ./install.sh --help       Diese Hilfe anzeigen (veraendert nichts)

Umgebungsvariablen:
  INSTALL_DIR          Zielverzeichnis der Installation
                       Standard: /opt/aliexpress-coin-collector
  OLD_INSTALL_DIR      Verzeichnis der Vorgaengerversion, das uebernommen wird
                       (.env, data/ und .android/ werden mitgenommen)
                       Standard: /opt/aliexpress-coins
  ADB_SERIAL           Adresse des Geraets als host:port (z. B. 192.168.1.50:5555) oder
                       USB-Seriennummer ohne Leerzeichen. Ohne diese Variable wird in einer
                       interaktiven Sitzung danach gefragt.
  DISCORD_WEBHOOK_URL  Discord-Webhook fuer Meldungen (leer = nur Log-Ausgabe). Ohne diese
                       Variable wird in einer interaktiven Sitzung danach gefragt.

Beispiel (ohne Rueckfragen):
  ADB_SERIAL=192.168.1.50:5555 DISCORD_WEBHOOK_URL=https://... ./install.sh

Eine vorhandene .env bleibt immer unveraendert. --uninstall loescht .env, data/ und
.android/ nur nach ausdruecklicher Bestaetigung.
USAGE
}

# Schreibt KEY=VALUE in eine .env-Datei, ohne den Wert zu veraendern.
# Bewusst kein sed: dort haetten '&', '\' und das Trennzeichen eine Sonderbedeutung und
# wuerden Werte wie Webhook-URLs mit Query-Parametern stillschweigend zerstoeren.
set_env_value() {
    local key="$1" value="$2" file="$3"
    local tmp line found=0
    tmp="$(mktemp "${file}.XXXXXX")"
    chmod 600 "$tmp"
    if [ -f "$file" ]; then
        # IFS= und read -r erhalten Leerzeichen und Backslashes der uebrigen Zeilen unveraendert.
        while IFS= read -r line || [ -n "$line" ]; do
            case "$line" in
                "$key"=*)
                    # Nur der erste Treffer wird ersetzt, weitere Doppelungen entfallen.
                    if [ "$found" -eq 0 ]; then
                        printf '%s=%s\n' "$key" "$value" >>"$tmp"
                        found=1
                    fi
                    ;;
                *)
                    printf '%s\n' "$line" >>"$tmp"
                    ;;
            esac
        done <"$file"
    fi
    if [ "$found" -eq 0 ]; then
        printf '%s=%s\n' "$key" "$value" >>"$tmp"
    fi
    mv "$tmp" "$file"
}

# Gueltig ist host:port mit Port 1-65535 oder eine USB-Seriennummer ohne Leerzeichen.
valid_adb_serial() {
    local value="$1" host port
    [ -n "$value" ] || return 1
    case "$value" in
        *[[:space:]]*) return 1 ;;
    esac
    case "$value" in
        *:*)
            host="${value%:*}"
            port="${value##*:}"
            [ -n "$host" ] || return 1
            case "$host" in
                *:*) return 1 ;;   # mehrere Doppelpunkte (z. B. IPv6) werden nicht unterstuetzt
            esac
            case "$port" in
                ''|*[!0-9]*) return 1 ;;
            esac
            [ "${#port}" -le 5 ] || return 1
            [ "$port" -ge 1 ] || return 1
            [ "$port" -le 65535 ] || return 1
            ;;
    esac
    return 0
}

# Die Zeitfenster gelten in Serverzeit. Ein Container in UTC liefert falsche Zeitstempel.
check_timezone() {
    local abbrev zone=""
    # 'date +%Z' zeigt die tatsaechlich wirksame Zeitzone (inklusive einer gesetzten TZ-Variablen).
    abbrev="$(date +%Z)"
    if [ -n "${TZ:-}" ]; then
        zone="$TZ"
    elif command -v timedatectl >/dev/null 2>&1; then
        zone="$(timedatectl show -p Timezone --value 2>/dev/null || true)"
    fi
    if [ -z "$zone" ] && [ -f /etc/timezone ]; then
        zone="$(tr -d '[:space:]' </etc/timezone || true)"
    fi
    case "$abbrev" in
        UTC|UTC0|Z)
            echo "    WARNUNG: Die Zeitzone ist UTC. Die Zeitfenster (MORNING_START usw.) gelten in"
            echo "             Serverzeit, die Laeufe und Zeitstempel waeren damit um Stunden verschoben."
            if command -v timedatectl >/dev/null 2>&1; then
                echo "             Umstellen mit: timedatectl set-timezone Europe/Berlin"
            else
                echo "             timedatectl fehlt, stattdessen: dpkg-reconfigure tzdata"
            fi
            ;;
        *)
            echo "    Zeitzone: ${abbrev}${zone:+ (${zone})}"
            ;;
    esac
}

do_uninstall() {
    echo "==> Deinstallation"
    if [ "$HAVE_SYSTEMD" = 1 ]; then
        systemctl stop "$SERVICE" 2>/dev/null || true
        systemctl disable "$SERVICE" 2>/dev/null || true
    else
        echo "    systemd nicht verfuegbar, Dienst konnte nicht gestoppt werden"
    fi
    rm -f "/etc/systemd/system/$SERVICE.service"
    if [ "$HAVE_SYSTEMD" = 1 ]; then
        systemctl daemon-reload
        echo "    Dienst gestoppt, deaktiviert und die systemd-Unit entfernt"
    else
        echo "    Unit-Datei entfernt, falls sie vorhanden war"
    fi

    if [ ! -d "$DEST" ]; then
        echo "    Kein Verzeichnis $DEST vorhanden, nichts weiter zu tun"
        return 0
    fi

    local answer=""
    if [ -t 0 ]; then
        echo "    In $DEST liegen .env (Zugangsdaten), data/ (Datenbank) und .android/ (ADB-Freigabe)."
        read -r -p "    Dieses Verzeichnis mit allen Daten unwiderruflich loeschen? Nur 'JA' loescht: " answer || answer=""
    fi
    if [ "$answer" = "JA" ]; then
        rm -rf "$DEST"
        echo "    $DEST wurde geloescht"
    else
        echo "    $DEST bleibt bestehen, dort liegen weiterhin:"
        echo "      $DEST/.env       Geraete-Adresse und Webhook"
        echo "      $DEST/data/      Datenbank und Screenshots"
        echo "      $DEST/.android/  ADB-Freigabe des Geraets"
        echo "    Der Dienstbenutzer '$SVC_USER' bleibt ebenfalls erhalten."
    fi
}

ACTION="install"
while [ "$#" -gt 0 ]; do
    case "$1" in
        -h|--help)
            usage
            exit 0
            ;;
        --uninstall)
            ACTION="uninstall"
            ;;
        *)
            printf 'Unbekanntes Argument: %s\n' "$1" >&2
            printf 'Hilfe mit: %s --help\n' "$0" >&2
            exit 2
            ;;
    esac
    shift
done

[ "$(id -u)" -eq 0 ] || { echo "Bitte als root ausfuehren." >&2; exit 1; }

HAVE_SYSTEMD=0
if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then HAVE_SYSTEMD=1; fi

if [ "$ACTION" = "uninstall" ]; then
    do_uninstall
    exit 0
fi

# Vor der Migration merken, ob es schon eine Installation am Zielort gibt.
IS_UPDATE=0
if [ -d "$DEST" ]; then IS_UPDATE=1; fi

if [ "$IS_UPDATE" = 1 ]; then
    echo "==> Bestehende Installation in $DEST gefunden: Aktualisierung (keine Neuinstallation)"
    echo "    .env, data/ und .android/ bleiben dabei unveraendert"
else
    echo "==> Neuinstallation nach $DEST"
fi

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
    chmod 600 "$DEST/.env"
    serial="${ADB_SERIAL:-}"
    webhook="${DISCORD_WEBHOOK_URL:-}"
    if [ -n "$serial" ] && ! valid_adb_serial "$serial"; then
        echo "    WARNUNG: Die vorgegebene ADB_SERIAL ist ungueltig."
        echo "             Erwartet wird host:port (Port 1-65535) oder eine USB-Seriennummer ohne Leerzeichen."
        serial=""
    fi
    if [ -t 0 ]; then
        tries=0
        while [ -z "$serial" ] && [ "$tries" -lt 3 ]; do
            tries=$((tries + 1))
            read -r -p "Geraete-Adresse (z. B. 192.168.1.50:5555) oder USB-Seriennummer: " serial || serial=""
            if [ -z "$serial" ]; then
                echo "    Keine Eingabe."
            elif ! valid_adb_serial "$serial"; then
                echo "    Ungueltig: erwartet wird host:port (Port 1-65535) oder eine USB-Seriennummer ohne Leerzeichen."
                serial=""
            fi
        done
        if [ -z "$webhook" ]; then
            read -r -p "Discord-Webhook-URL (leer lassen zum Ueberspringen): " webhook || webhook=""
        fi
    fi
    if [ -n "$serial" ]; then
        set_env_value ADB_SERIAL "$serial" "$DEST/.env"
    else
        echo "    WARNUNG: ADB_SERIAL wurde nicht gesetzt, bitte in $DEST/.env eintragen (z. B. 192.168.1.50:5555)"
    fi
    if [ -n "$webhook" ]; then
        set_env_value DISCORD_WEBHOOK_URL "$webhook" "$DEST/.env"
    fi
else
    echo "    .env existiert bereits, bleibt unveraendert"
fi
chmod 600 "$DEST/.env"
chown -R "$SVC_USER": "$DEST"

echo "==> systemd-Dienst"
if [ "$HAVE_SYSTEMD" = 1 ]; then
    sed "s|/opt/aliexpress-coin-collector|${DEST}|g" "$DEST/$SERVICE.service" > "/etc/systemd/system/$SERVICE.service"
    systemctl daemon-reload
    if [ "$IS_UPDATE" = 1 ]; then
        echo "    Dienstdatei aktualisiert. Laeuft der Dienst schon, wirkt die neue Version erst nach:"
        echo "      systemctl restart $SERVICE"
    else
        echo "    Dienst installiert, aber noch NICHT gestartet"
    fi
else
    echo "    systemd nicht verfuegbar, Dienstdatei uebersprungen"
fi

echo "==> Zeitzone pruefen"
check_timezone

if [ "$IS_UPDATE" = 1 ]; then
    echo ""
    echo "Fertig. $DEST wurde aktualisiert (bestehende Daten und Einstellungen blieben erhalten)."
else
    echo ""
    echo "Fertig. $DEST wurde neu eingerichtet."
fi

cat <<MSG

Naechste Schritte:
  1. Geraet freigeben (Dialog auf dem Geraet mit "Immer zulassen" bestaetigen; bei einer
     migrierten Installation ist das schon erledigt):
       runuser -u $SVC_USER -- adb connect \$(grep ^ADB_SERIAL= $DEST/.env | cut -d= -f2-)
  2. Installation pruefen:
       cd $DEST && runuser -u $SVC_USER -- .venv/bin/python -m aliexpress_coin_collector doctor
  3. Ersten echten Lauf machen:
       cd $DEST && runuser -u $SVC_USER -- .venv/bin/python -m aliexpress_coin_collector once --force --no-notify
  4. Wenn das klappt, Dienst starten:
       systemctl enable --now $SERVICE
MSG

# 'doctor' spricht ein echtes Geraet an, daher nur interaktiv und nur nach Zustimmung.
if [ -t 0 ] && [ -x "$DEST/.venv/bin/python" ]; then
    echo ""
    run_doctor=""
    read -r -p "Jetzt 'doctor' ausfuehren? Das spricht das Geraet wirklich an. [j/N]: " run_doctor || run_doctor=""
    case "$run_doctor" in
        j|J|ja|Ja|JA|y|Y|yes|Yes)
            echo "==> doctor"
            ( cd "$DEST" && runuser -u "$SVC_USER" -- .venv/bin/python -m aliexpress_coin_collector doctor ) \
                || echo "    doctor meldete einen Fehler, Hinweise siehe oben"
            ;;
        *)
            echo "    Uebersprungen, spaeter selbst ausfuehren (Schritt 2 oben)."
            ;;
    esac
fi
