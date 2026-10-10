# Forgejo-Stack

Selbst gehosteter Git-Server mit Container-Registry, npm-Registry und CI, per Docker Compose.

- **Forgejo** mit Postgres: Git, Container-Registry und npm-Registry
- **Forgejo Actions** (GitHub-Actions-kompatibel): Runner mit Docker-in-Docker, kein Zugriff auf den Host-Docker
- **Renovate**: PRs für veraltete npm-Pakete und Docker-Images
- **Uptime Kuma**: Monitoring
- **Backup**: nächtlicher Postgres-Dump, restic schiebt alles verschlüsselt offsite
- **Reverse Proxy und TLS**: dein bestehender Nginx Proxy Manager (NPM), nicht Teil dieses Stacks

> **Status:** Der Stack ist noch nicht auf einem Server getestet. Die offenen Punkte stehen unten.

## Voraussetzungen

- Docker mit Compose-Plugin
- Nginx Proxy Manager im gemeinsamen Docker-Netz `proxy`
- Eine Domain für Forgejo (z. B. `git.example.com`) mit DNS-Eintrag auf den Server
- Ein offsite Backup-Ziel für restic (S3-kompatibel oder SFTP)

## Schnellstart

1. Konfiguration anlegen und ausfüllen:
   ```bash
   cp .env.example .env
   ```
   Wichtig sind `FORGEJO_DOMAIN`, `POSTGRES_PASSWORD`, die `FORGEJO_ADMIN_*`-Werte und die restic-Variablen.
   Die Image-Tags `FORGEJO_VERSION` und `RUNNER_VERSION` sind Platzhalter und müssen gegen die Registries geprüft werden.
2. Gemeinsames Proxy-Netz anlegen, falls es das noch nicht gibt (der NPM-Container muss darin hängen):
   ```bash
   docker network create proxy
   ```
3. Alles starten:
   ```bash
   docker compose up -d
   ```
4. Im NPM einen Proxy-Host anlegen: `git.example.com` auf Host `forgejo`, Port `3000`, Websockets an, unter Advanced `client_max_body_size 0;`. Der Runner erreicht Forgejo über die öffentliche URL, der Proxy-Host sollte also zügig stehen.
5. `renovate-bot` in Forgejo pro Repo oder Organisation als Mitwirkender eintragen, sonst findet Renovate nichts.

Es gibt keinen Web-Installer und keine manuellen Token-Schritte mehr.

## Ablauf beim Start

| Schritt | Dienst | Aufgabe |
| --- | --- | --- |
| 1 | `init-secrets` | erzeugt `SECRET_KEY` und `INTERNAL_TOKEN` in `./secrets/forgejo` (vorhandene Dateien bleiben) |
| 2 | `db`, `forgejo` | Forgejo startet mit `INSTALL_LOCK`, Healthcheck gegen `/api/healthz` |
| 3 | `bootstrap` | legt Admin und `renovate-bot` an, erzeugt Renovate-Access-Token und Runner-Registrierungs-Token |
| 4 | `dind`, `runner`, `renovate` | starten, sobald `bootstrap` erfolgreich war |

`bootstrap.sh` ist idempotent: Benutzer werden nur angelegt, wenn sie fehlen, und vorhandene Token-Dateien werden nicht erneuert.

## Secrets

`./secrets` ist `700` und nicht im Git. Jeder Verbraucher bekommt nur sein Unterverzeichnis gemountet:

| Verzeichnis | Inhalt | gemountet in |
| --- | --- | --- |
| `secrets/forgejo/` | `secret_key`, `internal_token` | `forgejo` |
| `secrets/runner/` | `runner_token` | `runner` |
| `secrets/renovate/` | `renovate_token` | `renovate` |

**Token verloren oder neu erzeugen:** Datei in `./secrets/...` löschen und `docker compose up -d`. Beim Renovate-Token vorher den alten Token `renovate` in Forgejo (Einstellungen von `renovate-bot`) löschen, sonst scheitert die Neuanlage am doppelten Namen. Der Runner registriert sich nur, solange `./data/runner/.runner` fehlt.

## Dateien

| Datei | Zweck |
| --- | --- |
| `compose.yml` | der Stack |
| `bootstrap.sh` | Setup nach dem Forgejo-Start |
| `.env.example` | Vorlage für `.env` |
| `runner-config.yml` | Runner-Konfiguration (Netz `forgejo-ci`, `DOCKER_HOST=tcp://dind:2375`) |
| `workflow-ci.yml` | Beispiel-Workflow: lint, test, build |
| `workflow-release.yml` | Beispiel-Workflow: Tests, Image bauen und in die Forgejo-Registry pushen |
| `renovate.json` | Vorlage für Projekt-Repos |

## Projekte anbinden

1. Workflows ins Projekt-Repo kopieren:
   - `workflow-ci.yml` nach `.forgejo/workflows/ci.yml`
   - `workflow-release.yml` nach `.forgejo/workflows/release.yml`
2. `renovate.json` ins Projekt-Repo legen. Die Vorlage läuft montags vor 6 Uhr (Europe/Berlin) und bündelt npm-Minor/Patch-Updates in einen PR.
3. Für den Release-Workflow in den Repo-Einstellungen unter Actions > Secrets anlegen:
   - `REGISTRY_USER`: Forgejo-Benutzername
   - `REGISTRY_TOKEN`: Zugriffstoken mit dem Recht „package: read/write“

   Der Repo-Pfad (Benutzer oder Organisation plus Repo) muss kleingeschrieben sein.

## Deploy

Die Freigabe ist manuell und läuft über einen Git-Tag:

```bash
git tag v1.2.3 && git push origin v1.2.3
```

Der Release-Workflow baut daraufhin das Image als `v1.2.3` (ohne `v`) und `latest`. Auf dem Zielserver die Version in der Compose-Datei anheben und ausführen:

```bash
docker compose pull && docker compose up -d
```

Es gibt bewusst kein Watchtower und keinen automatischen Deploy.

## Backup

| Zeit | Dienst | Aufgabe |
| --- | --- | --- |
| täglich 03:00 | `db-dump` | Postgres-Dump nach `./backup/db` (3 Tage Aufbewahrung) |
| täglich 03:30 | `restic` | sichert `data/forgejo`, `data/kuma` und die Dumps offsite |
| sonntags 05:00 | `restic` | `forget`/`prune` (7 täglich, 4 wöchentlich, 6 monatlich) |
| sonntags 06:00 | `restic` | `check` |

Das laufende `data/postgres` wird bewusst nicht gesichert, nur der konsistente Dump. Bei SFTP-Zielen (z. B. Storage Box) muss zusätzlich ein SSH-Key in den `restic`-Container gemountet werden. `SECRET_KEY` und `INTERNAL_TOKEN` landen vermutlich in `data/forgejo/gitea/conf/app.ini` und wären damit im Backup enthalten. Das ist ungetestet; für den Restore prüfen.

## Offene Punkte

Vor dem produktiven Einsatz auf einem Testserver prüfen (`docker compose config`, dann `docker compose up`):

- Image-Tags `FORGEJO_VERSION` und `RUNNER_VERSION` gegen die Registries
- Init-Container: `forgejo generate secret`, `forgejo admin user create/list`, `forgejo forgejo-cli actions generate-runner-token`, `generate-access-token --raw`, `su-exec` und `bash` im Image, `__FILE`-Variablen
- Healthcheck mit `curl` im Forgejo-Image
- Runner-Registrierung und Netz `forgejo-ci` für Job-Container (`docker build` und `docker push` aus einem Workflow)
- Dateirechte der Bind-Mounts unter `./data` (UID 1000 für Forgejo)

Weitere Entscheidungen und Hintergründe stehen in `CLAUDE.md`.
