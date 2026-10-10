#!/bin/bash
# Einmaliges, idempotentes Setup nach dem ersten Forgejo-Start.
# Läuft im Forgejo-Image (Dienst "bootstrap" in compose.yml) und nutzt die forgejo-CLI
# direkt gegen die Datenbank. Wiederholte Läufe ändern nichts:
#   - Benutzer werden nur angelegt, wenn sie fehlen
#   - Token-Dateien in /secrets dienen als Merker; vorhandene Tokens werden nicht erneuert
# Ein verlorener Token wird neu erzeugt, indem die Datei gelöscht wird (bei Access-Tokens
# vorher den alten Token "renovate" in den Forgejo-Einstellungen von renovate-bot löschen).
set -euo pipefail
umask 077

: "${FORGEJO_DOMAIN:?}" "${FORGEJO_ADMIN_USER:?}" "${FORGEJO_ADMIN_PASSWORD:?}" "${FORGEJO_ADMIN_EMAIL:?}"

RENOVATE_USER=renovate-bot
RENOVATE_TOKEN_NAME=renovate
# Scopes so eng wie möglich (siehe Renovate-Doku für Forgejo)
RENOVATE_SCOPES="write:repository,write:issue,read:user,read:organization"

export HOME=/data/git

# Die CLI läuft nicht als root; app.ini liegt in /data/gitea (GITEA_CUSTOM aus dem Image)
forgejo() { su-exec 1000:1000 forgejo "$@"; }

user_exists() {
  forgejo admin user list | awk 'NR>1 {print $2}' | grep -qx "$1"
}

log() { echo "[bootstrap] $*"; }

if ! user_exists "$FORGEJO_ADMIN_USER"; then
  log "Lege Admin $FORGEJO_ADMIN_USER an"
  forgejo admin user create --admin \
    --username "$FORGEJO_ADMIN_USER" \
    --email "$FORGEJO_ADMIN_EMAIL" \
    --password "$FORGEJO_ADMIN_PASSWORD" \
    --must-change-password=false
else
  log "Admin $FORGEJO_ADMIN_USER existiert bereits"
fi

if ! user_exists "$RENOVATE_USER"; then
  log "Lege Benutzer $RENOVATE_USER an"
  forgejo admin user create \
    --username "$RENOVATE_USER" \
    --email "renovate@${FORGEJO_DOMAIN}" \
    --random-password \
    --must-change-password=false
else
  log "Benutzer $RENOVATE_USER existiert bereits"
fi

# Schreibt stdout eines Befehls atomar in eine Datei (nur bei Erfolg und nicht leer)
# $1 = Datei, $2 = Modus, $3 = Besitzer, Rest = Befehl
write_secret() {
  local file=$1 mode=$2 owner=$3
  shift 3
  local value
  value=$("$@" | tr -d '\r\n')
  [ -n "$value" ] || { log "FEHLER: leere Ausgabe für $file"; exit 1; }
  printf '%s' "$value" > "$file.tmp"
  chown "$owner" "$file.tmp"
  chmod "$mode" "$file.tmp"
  mv "$file.tmp" "$file"
}

if [ ! -s /secrets/renovate/renovate_token ]; then
  log "Erzeuge Access-Token für $RENOVATE_USER"
  write_secret /secrets/renovate/renovate_token 644 root:root \
    forgejo admin user generate-access-token \
      --username "$RENOVATE_USER" --token-name "$RENOVATE_TOKEN_NAME" \
      --scopes "$RENOVATE_SCOPES" --raw
else
  log "Renovate-Token vorhanden"
fi

if [ ! -s /secrets/runner/runner_token ]; then
  log "Erzeuge Runner-Registrierungs-Token"
  write_secret /secrets/runner/runner_token 600 root:root \
    forgejo forgejo-cli actions generate-runner-token
else
  log "Runner-Token vorhanden"
fi

log "Fertig"
