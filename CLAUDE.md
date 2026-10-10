# Forgejo-Stack (Git, Registry, CI)

## Ziel
Selbst gehosteter Forgejo-Stack per Docker Compose als Cloud-Repo für Claude-Code-Projekte:
- Git-Hosting, Container-Registry und npm-Registry in Forgejo
- CI mit Forgejo Actions (GitHub-Actions-kompatibel), Runner mit Docker-in-Docker
- Reverse Proxy und TLS: bestehender Nginx Proxy Manager (NPM), im gemeinsamen Docker-Netz `proxy`; alternativ Caddy (`compose.caddy.yml`, `Caddyfile`)
- Deploy: manuelle Freigabe per Git-Tag (`v1.2.3`), auf dem Zielserver danach `docker compose pull && docker compose up -d`

## Dateien
- `README.md`: Überblick, Schnellstart, Betrieb
- `compose.yml`: Stack (init-secrets, Forgejo, Postgres, bootstrap, dind, Runner, Renovate, Uptime Kuma, db-dump, restic)
- `compose.caddy.yml` und `Caddyfile`: optionale Alternative zum NPM (Caddy mit automatischem HTTPS für Forgejo und Uptime Kuma)
- `bootstrap.sh`: idempotentes Setup nach dem Forgejo-Start (Admin, `renovate-bot`, Tokens)
- `.env.example`: Vorlage für `.env`
- `runner-config.yml`: Runner-Konfiguration (Netzwerk `forgejo-ci`, `DOCKER_HOST=tcp://dind:2375`)
- `workflow-ci.yml` und `workflow-release.yml`: Beispiele, gehören in Projekt-Repos nach `.forgejo/workflows/`
- `renovate.json`: Vorlage für die Repos

## Entscheidungen
- Kein Traefik, kein Watchtower, Deploy bleibt manuell. Caddy nur als optionale Alternative zum NPM (Overlay-Datei, das Basis-`compose.yml` bleibt unverändert; nicht zusammen mit dem NPM auf demselben Host)
- Forgejo statt Gitea oder GitLab (leichter, Registry und npm eingebaut)
- Runner nutzt Docker-in-Docker (`dind`) statt Host-Docker-Socket
- Runner erreicht Forgejo über die öffentliche URL, nicht über das interne Netz
- Backup: nächtlicher Postgres-Dump (`db-dump`), restic sichert Dump, `data/forgejo` und `data/kuma` offsite. Das laufende `data/postgres` wird nicht gesichert
- Kein Profil `ci` mehr: `docker compose up -d` startet alles. `init-secrets` erzeugt `SECRET_KEY`/`INTERNAL_TOKEN`, `bootstrap` legt Admin, `renovate-bot` und Tokens an; Runner und Renovate warten auf `bootstrap` (`service_completed_successfully`)
- `./secrets` (700) hat je Verbraucher ein Unterverzeichnis (`forgejo/`, `runner/`, `renovate/`), das nur dort gemountet wird. Grund: Forgejo (UID 1000) könnte sonst ein root-eigenes 700er-Verzeichnis nicht lesen
- Admin-Zugangsdaten (`FORGEJO_ADMIN_*`) stehen in `.env`; `renovate-bot` bekommt ein Zufallspasswort, nur der Token zählt
- Token verloren: Datei in `./secrets/...` löschen und `docker compose run --rm bootstrap` (beim Renovate-Token vorher den Benutzer `renovate-bot` in Forgejo löschen, damit der Token-Name frei wird)

## Offene Punkte
- Image-Tags `FORGEJO_VERSION` und `RUNNER_VERSION` gegen die Registries prüfen (Platzhalter, nicht verifiziert)
- Healthcheck von Forgejo (`curl`) ist eingebaut, aber ungetestet; falls `curl` im Image fehlt, `wget` oder Alternative nehmen
- Init-Container im Test verifizieren (nicht gelaufen): `forgejo generate secret`, `forgejo admin user create/list`, `forgejo forgejo-cli actions generate-runner-token`, `generate-access-token --raw`, `su-exec` und `bash` im Image, `__FILE`-Variablen, Healthcheck-Pfad `/api/healthz`
- `renovate-bot` muss pro Repo/Organisation als Mitwirkender eingetragen werden, sonst findet Autodiscover nichts
- Registrierung des Runners und Netzwerk `forgejo-ci` für Job-Container im Test verifizieren (docker build und push aus einem Workflow)
- Dateirechte der Bind-Mounts unter `./data` (UID 1000 für Forgejo) testen
- Optional später: NPM-Proxy-Host per NPM-API und Repo-Secrets (`REGISTRY_USER`, `REGISTRY_TOKEN`) per Forgejo-API automatisieren

## Regeln
- Keine Secrets ins Git: `.env`, `secrets/`, `data/` und `backup/` in die `.gitignore`
- `secrets/` mit `chmod 700`, Token-Scopes so eng wie möglich
- Änderungen am Stack zuerst auf einem Testserver oder lokal mit `docker compose config` und `docker compose up` prüfen
- Antworten und Kommentare auf Deutsch
