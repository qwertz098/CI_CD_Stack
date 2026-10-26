#!/bin/sh
# Startet die Setup-UI (Weboberfläche zum Konfigurieren und Aufbauen des Stacks).
#
#   ./setup.sh            UI auf 127.0.0.1:8080 starten
#   SETUP_PORT=9000 ./setup.sh
#   SETUP_IMAGE=git.example.com/owner/stack-setup:latest ./setup.sh   (eigene Registry statt ghcr.io)
#
# Die UI braucht den Docker-Socket und hat damit Root-Rechte auf dem Host. Deshalb:
# nur an 127.0.0.1 gebunden (Zugriff von außen per SSH-Tunnel), Zugriffstoken in der
# Konsole, nach dem Setup mit Strg+C beenden.
set -eu

DIR=$(cd "$(dirname "$0")" && pwd)
PORT=${SETUP_PORT:-8080}
IMAGE=${SETUP_IMAGE:-ghcr.io/qwertz098/ci_cd_stack-setup:latest}
TOKEN=${SETUP_TOKEN:-$(head -c 18 /dev/urandom | od -An -tx1 | tr -d ' \n')}

if ! docker pull -q "$IMAGE" >/dev/null 2>&1 && ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "Image $IMAGE nicht verfügbar (privat oder nicht gebaut?), baue lokal ..."
  IMAGE=stack-setup
  docker build -q -t "$IMAGE" "$DIR/setup" >/dev/null
fi

# Der Stack-Ordner wird unter demselben Pfad wie auf dem Host gemountet. Nur so lösen
# `./data`, `./secrets` usw. in compose.yml auf Host-Pfade auf, die der Daemon kennt.
exec docker run --rm -it --name stack-setup \
  -p "127.0.0.1:${PORT}:8080" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$DIR:$DIR" -w "$DIR" \
  -e STACK_DIR="$DIR" -e SETUP_TOKEN="$TOKEN" -e SETUP_PUBLISHED_PORT="$PORT" \
  "$IMAGE"
