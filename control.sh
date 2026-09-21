#!/usr/bin/env bash
# Startet, stoppt oder startet die Dienste neu -- angestossen von der Weboberflaeche.
#
# Warum dieser Umweg: der Webdienst laeuft als unprivilegierter Nutzer mit NoNewPrivileges=yes.
# Damit funktioniert weder sudo (braucht setuid) noch zuverlaessig polkit (in einem schlanken
# LXC oft gar nicht installiert). Stattdessen schreibt er eine Zeile in data/control, eine
# systemd-Pfadeinheit bemerkt das und startet dieses Skript als root.
#
# Das Skript nimmt aus der Datei nichts als Befehl entgegen. Es liest zwei Woerter, prueft
# beide gegen eine feste Liste und setzt daraus selbst den systemctl-Aufruf zusammen. Alles,
# was nicht exakt passt, wird abgelehnt und protokolliert.
set -euo pipefail

DEST="${INSTALL_DIR:-/opt/aliexpress-coin-collector}"
SERVICE="aliexpress-coin-collector"
CONTROL="$DEST/data/control"
RESULT="$DEST/data/control-result"
SVC_USER="${SVC_USER:-coins}"

write_result() {
    local status="$1" text="$2"
    printf '%s\t%s\t%s\n' "$(date --iso-8601=seconds)" "$status" "$text" > "$RESULT.tmp"
    chown "$SVC_USER": "$RESULT.tmp" 2>/dev/null || true
    chmod 640 "$RESULT.tmp"
    mv "$RESULT.tmp" "$RESULT"
}

[ -f "$CONTROL" ] || exit 0

# Nur die erste Zeile, und nur die ersten beiden Woerter. Ein drittes wird ignoriert,
# nicht weitergereicht.
read -r verb target _rest < "$CONTROL" || true
rm -f "$CONTROL"

case "$verb" in
    start|stop|restart) ;;
    *)
        echo "abgelehnt: unbekannte Aktion" >&2
        write_result "abgelehnt" "Unbekannte Aktion."
        exit 0
        ;;
esac

case "$target" in
    collector) unit="$SERVICE.service" ;;
    web)       unit="$SERVICE-web.service" ;;
    *)
        echo "abgelehnt: unbekanntes Ziel" >&2
        write_result "abgelehnt" "Unbekanntes Ziel."
        exit 0
        ;;
esac

# Sich selbst zu stoppen waere eine Sackgasse: danach koennte niemand mehr etwas anfordern.
if [ "$target" = "web" ] && [ "$verb" = "stop" ]; then
    echo "abgelehnt: die Weboberflaeche darf sich nicht selbst stoppen" >&2
    write_result "abgelehnt" "Die Weboberflaeche darf sich nicht selbst stoppen."
    exit 0
fi

echo "fuehre aus: systemctl $verb $unit"
if systemctl "$verb" "$unit"; then
    write_result "erledigt" "$verb $unit"
else
    write_result "fehlgeschlagen" "$verb $unit hat einen Fehler gemeldet."
fi
exit 0
