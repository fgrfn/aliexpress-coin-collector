#!/usr/bin/env bash
# Erstinstallation von aliexpress-coin-collector ohne lokale Kopie (als root ausfuehren):
#   curl -fsSL https://raw.githubusercontent.com/fgrfn/aliexpress-coin-collector/main/bootstrap.sh | bash
# Laedt den Quelltext als Tarball von GitHub, entpackt ihn temporaer und startet das darin
# enthaltene install.sh. Alle Argumente werden an install.sh durchgereicht.
# Aufrufformen und Umgebungsvariablen erklaert: bootstrap.sh --help
#
# Der gesamte Code steht in Funktionen und die letzte Zeile ruft main auf. Bricht der Download
# dieser Datei mitten drin ab, fehlt dieser Aufruf und es wird nichts ausgefuehrt.
set -euo pipefail

OWNER="fgrfn"
REPO="aliexpress-coin-collector"

usage() {
    cat <<'USAGE'
aliexpress-coin-collector - bootstrap (als root ausfuehren)

Laedt den Quelltext von GitHub und startet das darin enthaltene install.sh.

Aufruf:
  curl -fsSL <url>/bootstrap.sh | bash
  curl -fsSL <url>/bootstrap.sh | bash -s -- --ref v1.2.3
  ./bootstrap.sh                 Neueste Veroeffentlichung installieren
  ./bootstrap.sh --ref <wert>    Bestimmtes Tag oder Branch installieren
  ./bootstrap.sh --uninstall     install.sh --uninstall des geladenen Standes ausfuehren
  ./bootstrap.sh --help          Diese Hilfe anzeigen (veraendert nichts)

Eine bestehende Installation wird mit "install.sh --update" aktualisiert, nicht mit
bootstrap.sh.

Ermittlung des Standes (in dieser Reihenfolge):
  1. --ref bzw. die Umgebungsvariable REF, falls gesetzt
  2. Neuestes Release-Tag ueber die GitHub-API
  3. Hoechstes Tag der GitHub-API (sortiert nach Versionsnummer)
  4. Branch 'main' (unveroeffentlichter Stand, mit deutlichem Hinweis)

Umgebungsvariablen:
  REF                  Tag oder Branch, der geladen wird (wie --ref)
  CODELOAD_BASE_URL    Basis-URL fuer den Tarball
                       Standard: https://codeload.github.com
  GITHUB_API_URL       Basis-URL der GitHub-API
                       Standard: https://api.github.com
  INSTALL_DIR          Zielverzeichnis der Installation (wird an install.sh durchgereicht)
                       Standard: /opt/aliexpress-coin-collector
  OLD_INSTALL_DIR      Verzeichnis der Vorgaengerversion, das uebernommen wird
                       Standard: /opt/aliexpress-coins
  ADB_SERIAL           Adresse des Geraets als host:port oder USB-Seriennummer
  DISCORD_WEBHOOK_URL  Discord-Webhook fuer Meldungen (leer = nur Log-Ausgabe)

Benoetigt curl oder wget sowie tar. git wird nicht benoetigt.
USAGE
}

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

main() {
    local pass_args=() ref work src
    while [ "$#" -gt 0 ]; do
        case "$1" in
            -h|--help)
                usage
                exit 0
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
            --uninstall)
                pass_args+=("$1")
                ;;
            --update)
                echo "bootstrap.sh installiert immer den neuesten Stand, --update gehoert zu install.sh." >&2
                echo "Eine bestehende Installation aktualisiert: <installdir>/install.sh --update" >&2
                exit 2
                ;;
            *)
                printf 'Unbekanntes Argument: %s\n' "$1" >&2
                printf 'Hilfe mit: %s --help\n' "$0" >&2
                exit 2
                ;;
        esac
        shift
    done
    export REF="${REF:-}"

    [ "$(id -u)" -eq 0 ] || { echo "Bitte als root ausfuehren." >&2; exit 1; }
    require_downloader
    command -v tar >/dev/null 2>&1 || { echo "tar fehlt, bitte installieren." >&2; exit 1; }

    echo "==> Quelltext von GitHub laden"
    ref="$(resolve_ref)"

    work="$(mktemp -d)"
    # shellcheck disable=SC2064
    trap "rm -rf '$work'" EXIT INT TERM

    src="$(download_source "$work" "$ref")"
    echo "    Stand $ref entpackt"

    echo "==> install.sh starten"
    bash "$src/install.sh" ${pass_args[0]+"${pass_args[@]}"}
}

main "$@"
