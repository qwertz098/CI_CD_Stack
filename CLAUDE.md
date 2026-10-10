# Forgejo-Stack (Git, Registry, CI)

## Ziel
Selbst gehosteter Forgejo-Stack per Docker Compose als Cloud-Repo für Claude-Code-Projekte:
- Git-Hosting, Container-Registry und npm-Registry in Forgejo
- CI mit Forgejo Actions (GitHub-Actions-kompatibel), Runner mit Docker-in-Docker
- Reverse Proxy und TLS: bestehender Nginx Proxy Manager (NPM), im gemeinsamen Docker-Netz `proxy`
- Deploy: manuelle Freigabe per Git-Tag (`v1.2.3`), auf dem Zielserver danach `docker compose pull && docker compose up -d`

## Dateien
- `compose.yml`: Stack (Forgejo, Postgres, dind, Runner, Renovate, Uptime Kuma, db-dump, restic)
- `.env.example`: Vorlage für `.env`
- `runner-config.yml`: Runner-Konfiguration (Netzwerk `forgejo-ci`, `DOCKER_HOST=tcp://dind:2375`)
- `workflow-ci.yml` und `workflow-release.yml`: Beispiele, gehören in Projekt-Repos nach `.forgejo/workflows/`
- `renovate.json`: Vorlage für die Repos

## Entscheidungen
- Kein Traefik, kein Caddy, kein Watchtower, Deploy bleibt manuell
- Forgejo statt Gitea oder GitLab (leichter, Registry und npm eingebaut)
- Runner nutzt Docker-in-Docker (`dind`) statt Host-Docker-Socket
- Runner erreicht Forgejo über die öffentliche URL, nicht über das interne Netz
- Backup: nächtlicher Postgres-Dump (`db-dump`), restic sichert Dump, `data/forgejo` und `data/kuma` offsite. Das laufende `data/postgres` wird nicht gesichert
- Profil `ci` (dind, runner, renovate) startet erst, wenn die Tokens existieren

## Nächster Schritt
Init-Container einbauen, damit `docker compose up -d` ohne Web-Installer und ohne manuelle Token-Schritte alles aufsetzt:
1. `init-secrets`: erzeugt `SECRET_KEY` und `INTERNAL_TOKEN` per `forgejo generate secret`, legt sie in `./secrets` ab. Forgejo liest sie über `FORGEJO__security__SECRET_KEY__FILE` und `FORGEJO__security__INTERNAL_TOKEN__FILE`, dazu `FORGEJO__security__INSTALL_LOCK=true`
2. `bootstrap` (nach Forgejo, gleiches Image, `restart: "no"`): legt Admin und Benutzer `renovate-bot` an, erzeugt deren Access-Tokens und den Runner-Token, schreibt sie nach `./secrets`
3. Runner und Renovate lesen Tokens aus `./secrets` statt aus `.env`, starten mit `depends_on: bootstrap: condition: service_completed_successfully`
4. Das Skript `bootstrap.sh` muss idempotent sein (Abfrage "existiert schon?", Token-Dateien als Merker)
5. Das Profil `ci` entfällt dann

## Offene Punkte
- Image-Tags `FORGEJO_VERSION` und `RUNNER_VERSION` gegen die Registries prüfen (Platzhalter, nicht verifiziert)
- Healthcheck für Forgejo einbauen und testen, ob `curl` im Image vorhanden ist (sonst `wget` oder Alternative)
- Prüfen, ob `forgejo forgejo-cli actions generate-runner-token` und `forgejo admin user generate-access-token --raw` in der gewählten Version so funktionieren
- Registrierung des Runners und Netzwerk `forgejo-ci` für Job-Container im Test verifizieren (docker build und push aus einem Workflow)
- Dateirechte der Bind-Mounts unter `./data` (UID 1000 für Forgejo) testen
- Optional später: NPM-Proxy-Host per NPM-API und Repo-Secrets (`REGISTRY_USER`, `REGISTRY_TOKEN`) per Forgejo-API automatisieren

## Regeln
- Keine Secrets ins Git: `.env`, `secrets/`, `data/` und `backup/` in die `.gitignore`
- `secrets/` mit `chmod 700`, Token-Scopes so eng wie möglich
- Änderungen am Stack zuerst auf einem Testserver oder lokal mit `docker compose config` und `docker compose up` prüfen
- Antworten und Kommentare auf Deutsch
