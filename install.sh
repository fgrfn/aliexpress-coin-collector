#!/usr/bin/env bash
# Installiert aliexpress-coin-collector in einem Debian/Ubuntu-LXC (als root ausfuehren).
# Nicht-interaktiv moeglich: ADB_SERIAL=... DISCORD_WEBHOOK_URL=... ./install.sh
# Erkennt eine aeltere Installation (aliexpress-coins) und uebernimmt .env, Datenbank und ADB-Freigabe.
# Mit --update wird der Quelltext von GitHub geladen und eine bestehende Installation ersetzt.
# Aufrufformen und Umgebungsvariablen erklaert ./install.sh --help
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="${INSTALL_DIR:-/opt/aliexpress-coin-collector}"
OLD="${OLD_INSTALL_DIR:-/opt/aliexpress-coins}"
SVC_USER="coins"
SERVICE="aliexpress-coin-collector"
OWNER="fgrfn"
REPO="aliexpress-coin-collector"

# Dateien und Verzeichnisse, die beim Kopieren nie angefasst werden (Zugangsdaten, Datenbank,
# ADB-Freigabe, venv mit absoluten Pfaden und Arbeitsreste).
KEEP_EXCLUDES=(--exclude .env --exclude data --exclude .venv --exclude .android
    --exclude __pycache__ --exclude .pytest_cache --exclude .git)

usage() {
    cat <<'USAGE'
aliexpress-coin-collector - Installationsskript (als root ausfuehren)

Aufruf:
  ./install.sh                     Installiert neu oder aktualisiert aus diesem Verzeichnis
  ./install.sh --update            Laedt den Quelltext von GitHub und aktualisiert die
                                   vorhandene Installation (mit Rauchtest und Rollback)
  ./install.sh --update --ref X    Aktualisiert auf ein bestimmtes Tag oder einen Branch
  ./install.sh --uninstall         Stoppt den Dienst und entfernt die systemd-Unit
                                   (Daten bleiben erhalten)
  ./install.sh --help              Diese Hilfe anzeigen (veraendert nichts)

Ohne lokale Kopie installiert bootstrap.sh:
  curl -fsSL <url>/bootstrap.sh | bash

Ermittlung des Standes bei --update (in dieser Reihenfolge):
  1. --ref bzw. die Umgebungsvariable REF, falls gesetzt
  2. Neuestes Release-Tag ueber die GitHub-API
  3. Hoechstes Tag der GitHub-API (sortiert nach Versionsnummer)
  4. Branch 'main' (unveroeffentlichter Stand, mit deutlichem Hinweis)

Umgebungsvariablen:
  INSTALL_DIR          Zielverzeichnis der Installation
                       Standard: /opt/aliexpress-coin-collector
  OLD_INSTALL_DIR      Verzeichnis der Vorgaengerversion, das uebernommen wird
                       (.env, data/ und .android/ werden mitgenommen)
                       Standard: /opt/aliexpress-coins
  ADB_SERIAL           Adresse des Geraets als host:port (z. B. 192.168.1.50:5555) oder
                       USB-Seriennummer ohne Leerzeichen. Ohne diese Variable wird in einer
                       interaktiven Sitzung danach gefragt. Bei --update wird nicht gefragt.
  DISCORD_WEBHOOK_URL  Discord-Webhook fuer Meldungen (leer = nur Log-Ausgabe). Ohne diese
                       Variable wird in einer interaktiven Sitzung danach gefragt.
                       Bei --update wird nicht gefragt.
  REF                  Tag oder Branch, der bei --update geladen wird (wie --ref)
  CODELOAD_BASE_URL    Basis-URL fuer den Tarball
                       Standard: https://codeload.github.com
  GITHUB_API_URL       Basis-URL der GitHub-API
                       Standard: https://api.github.com

Beispiel (ohne Rueckfragen):
  ADB_SERIAL=192.168.1.50:5555 DISCORD_WEBHOOK_URL=https://... ./install.sh

Eine vorhandene .env bleibt immer unveraendert; .env, data/ und .android/ werden bei einer
Aktualisierung nie ueberschrieben. --uninstall loescht diese Daten nur nach ausdruecklicher
Bestaetigung.
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

# --- Quelltext von GitHub laden -----------------------------------------------------------------

require_downloader() {
    if command -v curl >/dev/null 2>&1; then return 0; fi
    if command -v wget >/dev/null 2>&1; then return 0; fi
    echo "Weder curl noch wget ist vorhanden, der Quelltext kann nicht geladen werden." >&2
    echo "Bitte eines davon installieren, z. B.: apt-get update && apt-get install -y curl" >&2
    return 1
}

# Laedt eine URL nach stdout. Fehler (auch HTTP-Fehler) fuehren zu einem Rueckgabewert != 0.
fetch_stdout() {
    local url="$1"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$url"
    else
        wget -qO- "$url"
    fi
}

# Laedt eine URL in eine Datei. Bei einem Fehler bleibt keine halbe Datei zurueck.
download_to() {
    local url="$1" target="$2"
    # Fehlermeldungen des Werkzeugs bleiben aus: ein 404 gehoert hier zum Ablauf
    # (erst wird ein Tag, dann ein Branch versucht). Scheitert beides, meldet der Aufrufer es.
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL -o "$target" "$url" 2>/dev/null && return 0
    else
        wget -q -O "$target" "$url" 2>/dev/null && return 0
    fi
    rm -f "$target"
    return 1
}

# Erlaubt sind nur Zeichen, die in Tag- und Branchnamen vorkommen. Das haelt Sonderzeichen
# aus der URL heraus.
valid_ref() {
    local value="$1"
    [ -n "$value" ] || return 1
    case "$value" in
        -*|*..*) return 1 ;;
    esac
    case "$value" in
        *[!A-Za-z0-9._/+-]*) return 1 ;;
    esac
    return 0
}

# Liest den ersten Zeichenketten-Wert eines JSON-Feldes. Bewusst ohne jq, das ist nicht
# vorausgesetzt. awk statt head, damit kein vorzeitig geschlossenes Pipe-Ende stoert.
json_first_string() {
    local key="$1" json="$2"
    printf '%s' "$json" \
        | grep -o "\"$key\"[[:space:]]*:[[:space:]]*\"[^\"]*\"" \
        | awk 'NR==1' \
        | sed -e "s/.*:[[:space:]]*\"//" -e 's/"$//'
}

# Hoechstes Tag aus der Tag-Liste der GitHub-API, sortiert nach Versionsnummer.
json_highest_tag() {
    local json="$1"
    printf '%s' "$json" \
        | grep -o '"name"[[:space:]]*:[[:space:]]*"[^"]*"' \
        | sed -e 's/.*:[[:space:]]*"//' -e 's/"$//' \
        | sort -V \
        | awk 'END { print }'
}

# Gibt den zu ladenden Stand auf stdout aus, Meldungen gehen nach stderr.
resolve_ref() {
    local api="${GITHUB_API_URL:-https://api.github.com}"
    local wanted="${REF:-}" json tag
    if [ -n "$wanted" ]; then
        if ! valid_ref "$wanted"; then
            echo "Ungueltiger Wert fuer --ref/REF. Erlaubt sind Tag- und Branchnamen." >&2
            return 1
        fi
        echo "    Vorgegebener Stand: $wanted" >&2
        printf '%s\n' "$wanted"
        return 0
    fi

    json="$(fetch_stdout "$api/repos/$OWNER/$REPO/releases/latest" 2>/dev/null || true)"
    tag="$(json_first_string tag_name "$json")"
    if valid_ref "$tag"; then
        echo "    Neueste Veroeffentlichung: $tag" >&2
        printf '%s\n' "$tag"
        return 0
    fi

    json="$(fetch_stdout "$api/repos/$OWNER/$REPO/tags" 2>/dev/null || true)"
    tag="$(json_highest_tag "$json")"
    if valid_ref "$tag"; then
        echo "    Kein Release gefunden, hoechstes Tag: $tag" >&2
        printf '%s\n' "$tag"
        return 0
    fi

    echo "    HINWEIS: Weder eine Veroeffentlichung noch ein Tag war erreichbar." >&2
    echo "             Es wird der Branch 'main' installiert, also ein UNVEROEFFENTLICHTER Stand." >&2
    printf '%s\n' "main"
}

# Laedt den Tarball, entpackt ihn und gibt das Quellverzeichnis auf stdout aus.
download_source() {
    local work="$1" ref="$2"
    local base="${CODELOAD_BASE_URL:-https://codeload.github.com}"
    local tarball="$work/source.tar.gz"
    local unpack="$work/unpack"
    local kind url loaded=0 entry src=""

    for kind in tags heads; do
        url="$base/$OWNER/$REPO/tar.gz/refs/$kind/$ref"
        if download_to "$url" "$tarball"; then
            loaded=1
            break
        fi
    done
    if [ "$loaded" -ne 1 ]; then
        echo "Der Quelltext konnte nicht geladen werden (weder als Tag noch als Branch: $ref)." >&2
        echo "Moegliche Ursachen: das Repository ist privat (GitHub antwortet dann mit 404," >&2
        echo "ohne auf fehlende Rechte hinzuweisen), der gewuenschte Stand existiert nicht," >&2
        echo "oder es besteht keine Netzwerkverbindung." >&2
        return 1
    fi

    mkdir -p "$unpack"
    if ! tar -xzf "$tarball" -C "$unpack"; then
        echo "Das Archiv konnte nicht entpackt werden." >&2
        return 1
    fi

    # Der Tarball von GitHub hat genau ein Wurzelverzeichnis, dessen Name wird nicht geraten.
    for entry in "$unpack"/*; do
        [ -d "$entry" ] || continue
        if [ -n "$src" ]; then
            echo "Das Archiv enthaelt mehrere Wurzelverzeichnisse, das ist unerwartet." >&2
            return 1
        fi
        src="$entry"
    done
    if [ -z "$src" ]; then
        echo "Im Archiv wurde kein Quellverzeichnis gefunden." >&2
        return 1
    fi

    if [ ! -f "$src/install.sh" ] || [ ! -d "$src/aliexpress_coin_collector" ]; then
        echo "Der geladene Stand ist unvollstaendig (install.sh oder aliexpress_coin_collector fehlt)." >&2
        echo "Es wurde nichts veraendert." >&2
        return 1
    fi

    printf '%s\n' "$src"
}

# --- Aktualisierung einer bestehenden Installation ------------------------------------------------

# Version der installierten Fassung, niemals ein Abbruch: eine kaputte Installation soll die
# Meldung nicht verhindern.
read_installed_version() {
    local out=""
    if [ -x "$DEST/.venv/bin/python" ]; then
        out="$( cd "$DEST" && "$DEST/.venv/bin/python" -m aliexpress_coin_collector --version 2>/dev/null || true )"
    fi
    if [ -n "$out" ]; then
        printf '%s\n' "$out" | awk 'NR==1'
    else
        printf '%s\n' "unbekannt"
    fi
}

# Baut die venv auf und aktualisiert die Abhaengigkeiten.
build_venv() {
    [ -d "$DEST/.venv" ] || python3 -m venv "$DEST/.venv"
    "$DEST/.venv/bin/pip" install -q --upgrade pip
    "$DEST/.venv/bin/pip" install -q -r "$DEST/requirements.txt"
}

do_update() {
    local was_active=0 work="" backup="" src="" ref=""
    local version_before version_after

    if [ ! -d "$DEST" ]; then
        echo "In $DEST liegt keine Installation." >&2
        echo "Bitte zuerst installieren, z. B. mit: curl -fsSL <url>/bootstrap.sh | bash" >&2
        exit 1
    fi

    echo "==> Aktualisierung von $DEST"
    echo "    .env, data/ und .android/ bleiben dabei unveraendert"

    require_downloader
    command -v tar >/dev/null 2>&1 || { echo "tar fehlt, bitte installieren." >&2; exit 1; }
    if ! command -v rsync >/dev/null 2>&1; then
        echo "==> rsync nachinstallieren"
        export DEBIAN_FRONTEND=noninteractive
        apt-get install -y -qq rsync >/dev/null
    fi

    version_before="$(read_installed_version)"
    echo "    Version vorher:  $version_before"

    echo "==> Quelltext von GitHub laden"
    ref="$(resolve_ref)"
    work="$(mktemp -d)"
    # shellcheck disable=SC2064
    trap "rm -rf '$work'" EXIT INT TERM
    src="$(download_source "$work" "$ref")"
    echo "    Stand $ref entpackt"

    if [ "$HAVE_SYSTEMD" = 1 ]; then
        if systemctl is-active --quiet "$SERVICE"; then was_active=1; fi
        systemctl stop "$SERVICE" 2>/dev/null || true
        if [ "$was_active" = 1 ]; then
            echo "==> Dienst gestoppt (lief vorher, wird nachher wieder gestartet)"
        else
            echo "==> Dienst lief nicht, er wird nachher auch nicht gestartet"
        fi
    else
        echo "==> systemd nicht verfuegbar, Dienst wurde weder gestoppt noch gestartet"
    fi

    # Sicherung des Programmstandes. .env, data/ und .android/ bleiben ohnehin liegen und
    # werden deshalb bewusst nicht mitgesichert.
    backup="$work/backup"
    mkdir -p "$backup"
    rsync -a "${KEEP_EXCLUDES[@]}" "$DEST/" "$backup/"
    echo "==> Alter Programmstand gesichert"

    echo "==> Dateien aktualisieren"
    rsync -a "${KEEP_EXCLUDES[@]}" "$src/" "$DEST/"

    echo "==> Python-Umgebung"
    build_venv
    chmod 600 "$DEST/.env" 2>/dev/null || true
    chown -R "$SVC_USER": "$DEST" 2>/dev/null || true

    if [ "$HAVE_SYSTEMD" = 1 ] && [ -f "$DEST/$SERVICE.service" ]; then
        sed "s|/opt/aliexpress-coin-collector|${DEST}|g" "$DEST/$SERVICE.service" > "/etc/systemd/system/$SERVICE.service"
        systemctl daemon-reload
    fi

    echo "==> Rauchtest"
    version_after="$(read_installed_version)"
    if [ "$version_after" = "unbekannt" ]; then
        echo "    FEHLER: '--version' schlug nach der Aktualisierung fehl, Rollback laeuft" >&2
        rsync -a --delete "${KEEP_EXCLUDES[@]}" "$backup/" "$DEST/"
        build_venv >/dev/null 2>&1 || true
        chown -R "$SVC_USER": "$DEST" 2>/dev/null || true
        if [ "$HAVE_SYSTEMD" = 1 ] && [ -f "$DEST/$SERVICE.service" ]; then
            sed "s|/opt/aliexpress-coin-collector|${DEST}|g" "$DEST/$SERVICE.service" > "/etc/systemd/system/$SERVICE.service"
            systemctl daemon-reload
        fi
        if [ "$was_active" = 1 ]; then
            systemctl start "$SERVICE" 2>/dev/null || true
            echo "    Der vorherige Stand ist wiederhergestellt und der Dienst laeuft wieder." >&2
        else
            echo "    Der vorherige Stand ist wiederhergestellt." >&2
        fi
        echo "    .env, data/ und .android/ wurden zu keinem Zeitpunkt veraendert." >&2
        exit 1
    fi

    rm -rf "$backup"
    echo "    Version nachher: $version_after"

    if [ "$was_active" = 1 ]; then
        systemctl start "$SERVICE"
        echo "==> Dienst wieder gestartet"
    fi

    echo ""
    echo "Fertig. $DEST wurde auf $ref aktualisiert ($version_before -> $version_after)."
    echo ".env, data/ und .android/ blieben unveraendert."
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
        --update)
            ACTION="update"
            ;;
        --ref)
            if [ "$#" -lt 2 ]; then
                echo "--ref erwartet einen Wert (Tag oder Branch)." >&2
                exit 2
            fi
            REF="$2"
            shift
            ;;
        --ref=*)
            REF="${1#--ref=}"
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

if [ "$ACTION" = "update" ]; then
    do_update
    exit 0
fi

if [ -n "${REF:-}" ]; then
    echo "    Hinweis: --ref/REF wirkt nur zusammen mit --update und wird hier ignoriert."
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
    rsync -a "${KEEP_EXCLUDES[@]}" "$SRC/" "$DEST/"
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
