# Forgejo-Stack

Selbst gehosteter Git-Server mit Container-Registry, npm-Registry und CI, per Docker Compose.

- **Forgejo** mit Postgres: Git, Container-Registry und npm-Registry
- **Forgejo Actions** (GitHub-Actions-kompatibel): Runner mit Docker-in-Docker, kein Zugriff auf den Host-Docker
- **Renovate**: PRs für veraltete npm-Pakete und Docker-Images
- **Uptime Kuma**: Monitoring
- **Backup**: nächtlicher Postgres-Dump, restic schiebt alles verschlüsselt offsite
- **Reverse Proxy und TLS**: dein bestehender Nginx Proxy Manager (NPM), nicht Teil dieses Stacks

> **Status:** Der Stack ist noch **nicht auf einem Server getestet**. Befehle und Menüpfade unten sind nach bestem Wissen beschrieben; was ausdrücklich ungetestet ist, steht unter [Offene Punkte](#offene-punkte). Am besten zuerst auf einem Testserver durchspielen.

## Inhalt

1. [Überblick](#überblick)
2. [Installation Schritt für Schritt](#installation-schritt-für-schritt)
3. [Erstes Projekt anbinden](#erstes-projekt-anbinden)
4. [Release und Deploy](#release-und-deploy)
5. [Renovate](#renovate)
6. [Monitoring mit Uptime Kuma](#monitoring-mit-uptime-kuma)
7. [Backup und Restore](#backup-und-restore)
8. [Betrieb: Updates, Tokens, Reset](#betrieb-updates-tokens-reset)
9. [Fehlersuche](#fehlersuche)
10. [Offene Punkte](#offene-punkte)

---

## Überblick

### Dateien

| Datei | Zweck |
| --- | --- |
| `compose.yml` | der Stack |
| `bootstrap.sh` | Setup nach dem Forgejo-Start (Admin, `renovate-bot`, Tokens) |
| `.env.example` | Vorlage für `.env` |
| `runner-config.yml` | Runner-Konfiguration (Netz `forgejo-ci`, `DOCKER_HOST=tcp://dind:2375`) |
| `workflow-ci.yml` | Beispiel-Workflow: lint, test, build |
| `workflow-release.yml` | Beispiel-Workflow: Tests, Image bauen und in die Forgejo-Registry pushen |
| `renovate.json` | Vorlage für Projekt-Repos |
| `CLAUDE.md` | Entscheidungen und Hintergründe |

### Ablauf beim Start

Es gibt keinen Web-Installer und keine manuellen Token-Schritte. `docker compose up -d` erledigt alles in dieser Reihenfolge:

| Schritt | Dienst | Aufgabe |
| --- | --- | --- |
| 1 | `init-secrets` | erzeugt `SECRET_KEY` und `INTERNAL_TOKEN` in `./secrets/forgejo` (vorhandene Dateien bleiben) |
| 2 | `db`, `forgejo` | Forgejo startet mit `INSTALL_LOCK`, Healthcheck gegen `/api/healthz` |
| 3 | `bootstrap` | legt Admin und `renovate-bot` an, erzeugt Renovate-Access-Token und Runner-Registrierungs-Token |
| 4 | `dind`, `runner`, `renovate` | starten, sobald `bootstrap` erfolgreich war |

`init-secrets` und `bootstrap` laufen einmal und beenden sich (`Exited (0)`). Beide sind idempotent: Wiederholte Läufe ändern nichts, solange die Benutzer und die Token-Dateien in `./secrets` existieren.

### Secrets

`./secrets` ist `700` und nicht im Git. Jeder Verbraucher bekommt nur sein Unterverzeichnis gemountet:

| Verzeichnis | Inhalt | gemountet in |
| --- | --- | --- |
| `secrets/forgejo/` | `secret_key`, `internal_token` | `forgejo` |
| `secrets/runner/` | `runner_token` | `runner` |
| `secrets/renovate/` | `renovate_token` | `renovate` |

### Netze

| Netz | Zweck |
| --- | --- |
| `internal` | Forgejo, Postgres, bootstrap, Renovate, Backup |
| `proxy` (extern) | gemeinsames Netz mit dem NPM; Forgejo und Uptime Kuma hängen darin |
| `forgejo-ci` | Runner, Docker-in-Docker und Job-Container |

---

## Installation Schritt für Schritt

### Schritt 0: Voraussetzungen prüfen

Auf dem Server:

```bash
docker --version          # Docker Engine
docker compose version    # Compose-Plugin, v2
```

Außerdem nötig:

- Eine **Domain** für Forgejo (Beispiel: `git.example.com`) mit **DNS-Eintrag** (A/AAAA) auf den Server
- Ein laufender **Nginx Proxy Manager**, dessen Container im Docker-Netz `proxy` hängt (siehe Schritt 3)
- Ein **offsite Backup-Ziel** für restic (S3-kompatibel oder SFTP)
- Eine **offene Firewall-Freigabe** für den Git-SSH-Port (Standard `2222`) sowie 80/443 für den NPM

### Schritt 1: Repo auf den Server holen

```bash
git clone <URL-dieses-Repos> forgejo-stack
cd forgejo-stack
```

Alle weiteren Befehle laufen in diesem Verzeichnis. `data/`, `secrets/` und `backup/` entstehen beim ersten Start hier und sind per `.gitignore` ausgeschlossen.

### Schritt 2: `.env` anlegen und ausfüllen

```bash
cp .env.example .env
chmod 600 .env
```

Starke Passwörter erzeugen (Hex vermeidet Sonderzeichen wie `$`, die Compose in `.env`-Dateien sonst als Variablen deutet):

```bash
openssl rand -hex 24
```

Dann `.env` bearbeiten:

| Variable | Bedeutung | Beispiel |
| --- | --- | --- |
| `TZ` | Zeitzone aller Container | `Europe/Berlin` |
| `FORGEJO_DOMAIN` | Domain ohne `https://` | `git.example.com` |
| `FORGEJO_SSH_PORT` | veröffentlichter Port für Git über SSH | `2222` |
| `FORGEJO_VERSION` | Forgejo-Image-Tag, **Platzhalter, prüfen** | `11` |
| `POSTGRES_PASSWORD` | Passwort der Datenbank | Ausgabe von `openssl rand -hex 24` |
| `FORGEJO_ADMIN_USER` | Name des Admins; `admin` ist reserviert | `forgejo-admin` |
| `FORGEJO_ADMIN_PASSWORD` | Passwort des Admins | Ausgabe von `openssl rand -hex 24` |
| `FORGEJO_ADMIN_EMAIL` | E-Mail des Admins | `admin@example.com` |
| `PROXY_NETWORK` | Name des Docker-Netzes, in dem der NPM hängt | `proxy` |
| `RUNNER_VERSION` | Runner-Image-Tag, **Platzhalter, prüfen** | `11` |
| `RUNNER_NAME` | Anzeigename des Runners | `runner-1` |
| `RENOVATE_GITHUB_COM_TOKEN` | optional, vermeidet GitHub-Rate-Limits | leer lassen oder GitHub-Token ohne Rechte |
| `RESTIC_REPOSITORY` | Backup-Ziel | `s3:https://s3.example.com/bucket-name` oder `sftp:u12345@u12345.your-storagebox.de:/restic` |
| `RESTIC_PASSWORD` | Passwort für das verschlüsselte Backup, **sicher separat aufbewahren** | Ausgabe von `openssl rand -hex 24` |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | nur für S3-Ziele | Zugangsdaten des Buckets |

**Image-Tags prüfen:** Die Werte `FORGEJO_VERSION=11` und `RUNNER_VERSION=11` sind Platzhalter. Aktuelle Tags stehen unter
<https://codeberg.org/forgejo/forgejo/releases> (Forgejo) und <https://code.forgejo.org/forgejo/-/packages/container/runner> (Runner). Ein Tag muss in der Registry existieren, sonst scheitert `docker compose pull`.

**Merke dir das `RESTIC_PASSWORD`:** Ohne dieses Passwort sind die Backups nicht lesbar.

Konfiguration prüfen (muss ohne Fehler durchlaufen):

```bash
docker compose config -q && echo OK
```

### Schritt 3: Gemeinsames Proxy-Netz sicherstellen

```bash
docker network ls --filter name=proxy
```

Existiert das Netz nicht, anlegen und den NPM-Container hineinhängen:

```bash
docker network create proxy
docker network connect proxy <name-des-npm-containers>
```

Heißt dein Netz anders, setze `PROXY_NETWORK` in der `.env` entsprechend.

### Schritt 4: Stack starten

```bash
docker compose up -d
```

Der Befehl wartet auf die Abhängigkeiten (Datenbank, Forgejo gesund, `bootstrap` fertig) und kann deshalb ein paar Minuten dauern. Beim ersten Start werden außerdem alle Images geladen.

Status prüfen:

```bash
docker compose ps -a
```

Erwartet:

- `init-secrets` und `bootstrap`: **Exited (0)**
- `forgejo`: **running (healthy)**
- `db`, `dind`, `runner`, `renovate`, `uptime-kuma`, `db-dump`, `restic`: **running**

Logs der Einmal-Dienste ansehen:

```bash
docker compose logs init-secrets
docker compose logs bootstrap
```

Im `bootstrap`-Log sollte stehen: `Lege Admin ... an`, `Lege Benutzer renovate-bot an`, `Erzeuge Access-Token ...`, `Erzeuge Runner-Registrierungs-Token`, `Fertig`. Bei einem zweiten Lauf stattdessen `existiert bereits` bzw. `vorhanden`.

Kontrolle der erzeugten Dateien:

```bash
sudo ls -l secrets secrets/forgejo secrets/runner secrets/renovate
```

(`sudo` nötig, weil `./secrets` root gehört und `700` hat.) Dort müssen `secret_key`, `internal_token`, `runner_token` und `renovate_token` liegen.

Falls etwas nicht grün ist: [Fehlersuche](#fehlersuche).

### Schritt 5: Proxy-Host im Nginx Proxy Manager anlegen

Der Runner und Renovate sprechen Forgejo über die öffentliche URL an. Solange der Proxy-Host fehlt, starten sie deshalb neu (`restart: unless-stopped`) und fangen sich, sobald die URL erreichbar ist.

Im NPM unter **Hosts > Proxy Hosts > Add Proxy Host**:

**Tab „Details“**

| Feld | Wert |
| --- | --- |
| Domain Names | `git.example.com` |
| Scheme | `http` |
| Forward Hostname / IP | `forgejo` |
| Forward Port | `3000` |
| Block Common Exploits | an |
| Websockets Support | **an** |

**Tab „SSL“**

| Feld | Wert |
| --- | --- |
| SSL Certificate | Request a new SSL Certificate (Let's Encrypt) |
| Force SSL | an |
| HTTP/2 Support | an |

**Tab „Advanced“** (Registry-Uploads sind groß, ohne Limit gibt es 413-Fehler):

```nginx
client_max_body_size 0;
```

Speichern. Der Name `forgejo` funktioniert, weil NPM und Forgejo im selben Docker-Netz `proxy` hängen. Hängt der NPM **nicht** im selben Netz, muss in `compose.yml` stattdessen der auskommentierte Port `127.0.0.1:3000:3000` aktiviert und im NPM auf `127.0.0.1`/Host-IP weitergeleitet werden.

Test:

```bash
curl -I https://git.example.com
```

Erwartet: `HTTP/2 200`.

### Schritt 6: Anmelden und Setup prüfen

1. `https://git.example.com` öffnen und mit `FORGEJO_ADMIN_USER` / `FORGEJO_ADMIN_PASSWORD` anmelden.
2. **Site-Administration > Benutzerkonten:** Es gibt den Admin und `renovate-bot`.
3. **Site-Administration > Actions > Runner:** `runner-1` (bzw. dein `RUNNER_NAME`) steht auf **Idle/Online**. Falls nicht: `docker compose logs runner`.
4. Registrierung ist abgeschaltet (`DISABLE_REGISTRATION`). Weitere Benutzer legst du als Admin unter **Site-Administration > Benutzerkonten > Benutzerkonto erstellen** an.
5. Optional: Admin-Passwort in den eigenen Einstellungen ändern und 2FA aktivieren.

### Schritt 7: SSH-Zugang für Git einrichten

1. In Forgejo: **Einstellungen > SSH/GPG-Schlüssel > Schlüssel hinzufügen**, deinen öffentlichen Schlüssel (`~/.ssh/id_ed25519.pub`) einfügen.
2. Der Git-SSH-Port ist `FORGEJO_SSH_PORT` (Standard `2222`), nicht 22. Clone-URL:

   ```bash
   git clone ssh://git@git.example.com:2222/<benutzer>/<repo>.git
   ```

3. Optional in `~/.ssh/config`:

   ```
   Host git.example.com
       Port 2222
       User git
   ```

   Dann reicht `git clone git@git.example.com:<benutzer>/<repo>.git`.

---

## Erstes Projekt anbinden

### 1. Repo anlegen und pushen

In Forgejo ein Repo anlegen (**+ > Neues Repository**) und das Projekt dorthin pushen:

```bash
git remote add origin ssh://git@git.example.com:2222/<benutzer>/<repo>.git
git push -u origin main
```

**Benutzer- bzw. Organisationsname und Repo-Name kleinschreiben**, sonst funktioniert die Container-Registry nicht.

### 2. CI-Workflow einrichten

Im Projekt-Repo:

```bash
mkdir -p .forgejo/workflows
cp <pfad-zu-diesem-repo>/workflow-ci.yml      .forgejo/workflows/ci.yml
cp <pfad-zu-diesem-repo>/workflow-release.yml .forgejo/workflows/release.yml
git add .forgejo && git commit -m "CI und Release-Workflow" && git push
```

Der CI-Workflow (`ci.yml`) läuft bei Pushes auf `main` und bei Pull Requests: `npm ci`, dann `lint`, `test`, `build` (jeweils nur, wenn das Script existiert). Ergebnis im Repo unter dem Tab **Actions**.

Die Actions wie `actions/checkout@v4` werden von GitHub geladen (`DEFAULT_ACTIONS_URL=https://github.com`). Der Server braucht dafür Zugriff auf github.com.

### 3. Secrets für den Release-Workflow

1. **Token erzeugen:** In Forgejo **Einstellungen > Anwendungen > Zugriffstoken erzeugen**, Name z. B. `ci-registry`, Berechtigung **package: Lesen und Schreiben**. Token sofort kopieren, er wird nur einmal angezeigt.
2. **Secrets anlegen:** Im Repo **Einstellungen > Actions > Secrets**:

   | Name | Wert |
   | --- | --- |
   | `REGISTRY_USER` | dein Forgejo-Benutzername |
   | `REGISTRY_TOKEN` | der Token aus Schritt 1 |

---

## Release und Deploy

### Release auslösen

Die Freigabe ist manuell und läuft über einen Git-Tag:

```bash
git tag v1.2.3
git push origin v1.2.3
```

Der Release-Workflow (`release.yml`) führt dann aus:

1. Tests (`npm ci`, `npm test --if-present`)
2. `docker build` mit den Tags `<domain>/<benutzer>/<repo>:1.2.3` (ohne `v`) und `:latest`
3. `docker push` beider Tags in die Forgejo-Registry

Fortschritt im Repo unter **Actions**. Das fertige Image findest du unter **Pakete** (Benutzer- oder Organisationsprofil).

Voraussetzung: Im Projekt-Repo liegt ein `Dockerfile`.

### Auf dem Zielserver ausrollen

1. Einmalig am Zielserver anmelden (Benutzername und Token mit **package: read**):

   ```bash
   docker login git.example.com -u <benutzer>
   ```

2. In der Compose-Datei des Projekts das Image auf die neue Version setzen:

   ```yaml
   image: git.example.com/<benutzer>/<repo>:1.2.3
   ```

3. Ausrollen:

   ```bash
   docker compose pull && docker compose up -d
   ```

Es gibt bewusst kein Watchtower und keinen automatischen Deploy.

### npm-Pakete veröffentlichen (optional)

Forgejo bringt eine npm-Registry mit. In der `.npmrc` des Projekts:

```
@<scope>:registry=https://git.example.com/api/packages/<benutzer>/npm/
//git.example.com/api/packages/<benutzer>/npm/:_authToken=${NPM_TOKEN}
```

`NPM_TOKEN` ist ein Zugriffstoken mit **package: Lesen und Schreiben**. Veröffentlichen mit `npm publish`.

---

## Renovate

Renovate läuft als Dienst, startet beim Container-Start und wiederholt den Lauf alle 6 Stunden. Es nutzt den Benutzer `renovate-bot` und seinen automatisch erzeugten Token.

### Einrichtung pro Projekt

1. **`renovate-bot` Zugriff geben:** Im Repo **Einstellungen > Mitwirkende** `renovate-bot` mit **Schreibrechten** hinzufügen (bei Organisationen alternativ ein Team mit Schreibzugriff). Ohne das findet Renovate das Repo nicht (Autodiscover).
2. **Konfiguration einchecken:** `renovate.json` aus diesem Repo ins Projekt-Repo legen:

   ```bash
   cp <pfad-zu-diesem-repo>/renovate.json .
   git add renovate.json && git commit -m "Renovate konfigurieren" && git push
   ```

   Die Vorlage läuft montags vor 6 Uhr (Europe/Berlin), bündelt npm-Minor/Patch-Updates in einen PR und erlaubt höchstens 5 offene PRs gleichzeitig.
3. **Ersten Lauf auslösen und beobachten:**

   ```bash
   docker compose restart renovate
   docker compose logs -f renovate
   ```

Ohne `renovate.json` im Repo öffnet Renovate zuerst einen „Configure Renovate“-Onboarding-PR. Wegen des Zeitplans in `renovate.json` entstehen Update-PRs nur im konfigurierten Zeitfenster.

---

## Monitoring mit Uptime Kuma

Uptime Kuma hängt im Netz `proxy`, hat aber **keinen** Proxy-Host. Einrichten:

1. Im NPM einen weiteren Proxy-Host anlegen, z. B. `status.example.com` auf Host `uptime-kuma`, Port `3001`, Websockets **an**, SSL wie oben.
2. `https://status.example.com` öffnen. **Sofort** den Admin-Benutzer anlegen, denn der erste Besucher wird Admin.
3. Monitore anlegen, zum Beispiel:
   - **HTTP(s):** `https://git.example.com` (Forgejo erreichbar)
   - **TCP-Port:** `git.example.com`, Port `2222` (Git über SSH)
   - **HTTP(s):** `https://git.example.com/api/healthz`
4. Unter **Einstellungen > Benachrichtigungen** einen Kanal (E-Mail, Telegram o. ä.) hinterlegen und den Monitoren zuweisen.

---

## Backup und Restore

### Was wann passiert

| Zeit | Dienst | Aufgabe |
| --- | --- | --- |
| täglich 03:00 | `db-dump` | Postgres-Dump nach `./backup/db` (3 Tage Aufbewahrung) |
| täglich 03:30 | `restic` | sichert `data/forgejo`, `data/kuma` und die Dumps offsite |
| sonntags 05:00 | `restic` | `forget`/`prune` (7 täglich, 4 wöchentlich, 6 monatlich) |
| sonntags 06:00 | `restic` | `check` |

Das laufende `data/postgres` wird bewusst nicht gesichert, nur der konsistente Dump. Nicht gesichert werden außerdem `secrets/` und `.env`. Beides gehört separat in deinen Passwortmanager (mindestens `.env`, vor allem `RESTIC_PASSWORD`). `SECRET_KEY` und `INTERNAL_TOKEN` landen vermutlich zusätzlich in `data/forgejo/gitea/conf/app.ini` und wären damit im Backup (ungetestet, beim Restore prüfen).

Bei SFTP-Zielen (z. B. Storage Box) muss zusätzlich ein SSH-Key in den `restic`-Container gemountet werden; das ist in `compose.yml` nicht vorbereitet.

### Backup prüfen

Nach der ersten Nacht (oder dem ersten Lauf) im Container:

```bash
docker compose logs restic | tail -n 50
docker compose exec restic restic snapshots
```

Es muss mindestens ein Snapshot existieren. Existiert das restic-Repository noch nicht und die Logs melden das, einmalig anlegen:

```bash
docker compose exec restic restic init
```

(Ob das Image das Repository selbst anlegt, ist ungetestet.)

Der Postgres-Dump liegt unter `./backup/db/` (u. a. `last/`, `daily/`).

### Restore-Probe (empfohlen, regelmäßig)

Ein Backup zählt erst, wenn der Restore einmal funktioniert hat:

```bash
docker compose exec restic restic restore latest --target /tmp/restore
docker compose exec restic ls /tmp/restore
```

### Wiederherstellung auf einem neuen Server (Skizze, ungetestet)

1. Repo klonen, `.env` aus dem Passwortmanager wiederherstellen.
2. Daten per restic nach `./data/forgejo`, `./data/kuma` und `./backup/db` zurückholen (z. B. mit `restic restore` auf einem Rechner mit Zugriff auf das Repository).
3. Nur die Datenbank starten: `docker compose up -d db`
4. Dump einspielen (Dateiname je nach Layout des Dump-Images prüfen):

   ```bash
   zcat backup/db/last/forgejo-latest.sql.gz | docker compose exec -T db psql -U forgejo -d forgejo
   ```

5. Restlichen Stack starten: `docker compose up -d`. `init-secrets` überschreibt vorhandene Schlüssel nicht; ist `./secrets` neu, muss `SECRET_KEY` und `INTERNAL_TOKEN` zu den wiederhergestellten Daten passen, sonst sind verschlüsselte Inhalte (z. B. 2FA) nicht lesbar.

---

## Betrieb: Updates, Tokens, Reset

### Stack aktualisieren

1. **Backup prüfen** (siehe oben).
2. Release Notes von Forgejo lesen, besonders bei Major-Sprüngen.
3. Tag in `.env` anheben (`FORGEJO_VERSION`, `RUNNER_VERSION`), dann:

   ```bash
   docker compose pull && docker compose up -d
   ```

4. Logs prüfen: `docker compose logs -f forgejo`.

### Token verloren oder neu erzeugen

- **Runner-Token:** `secrets/runner/runner_token` löschen, dann `docker compose run --rm bootstrap`. Der Runner registriert sich nur, solange `./data/runner/.runner` fehlt; für eine Neuregistrierung auch diese Datei löschen, den alten Runner in Forgejo entfernen und `docker compose up -d runner` ausführen.
- **Renovate-Token:** Als Admin in Forgejo unter **Site-Administration > Benutzerkonten** den Benutzer `renovate-bot` löschen (damit verschwindet auch sein Token `renovate`), `secrets/renovate/renovate_token` löschen, dann `docker compose run --rm bootstrap` (legt Benutzer und Token neu an). Danach `renovate-bot` wieder als Mitwirkender in den Repos eintragen und `docker compose restart renovate`.
- **Admin-Passwort:** nachträgliche Änderungen in `.env` ändern **nicht** das bestehende Konto. Passwort in Forgejo ändern.

### Nützliche Befehle

```bash
docker compose ps -a                 # Status aller Dienste
docker compose logs -f <dienst>      # Logs, z. B. forgejo, runner, renovate, bootstrap
docker compose config                # aufgelöste Konfiguration
docker compose restart <dienst>      # Dienst neu starten
docker compose down                  # stoppen (Daten bleiben)
```

### Alles zurücksetzen (destruktiv)

Löscht lokal **alle** Repos, Pakete, die Datenbank, Tokens und die Postgres-Dumps. Nur auf Testservern oder nach bestätigtem Backup:

```bash
docker compose down -v
sudo rm -rf data secrets backup
```

---

## Fehlersuche

| Symptom | Ursache und Lösung |
| --- | --- |
| `docker compose up -d` bricht mit „dependency failed to start“ ab | `docker compose logs bootstrap` bzw. `init-secrets` ansehen und Fehler beheben, dann `docker compose run --rm bootstrap` bzw. erneut `docker compose up -d` |
| `forgejo` bleibt `unhealthy` | `docker compose logs forgejo`. Mögliche Ursachen: Datenbank nicht erreichbar, falsches `POSTGRES_PASSWORD` (nach dem ersten Start nicht mehr änderbar, ohne `data/postgres` zu löschen), kein `curl` im Image (dann Healthcheck in `compose.yml` auf `wget` ändern), Rechte an `./data/forgejo` (muss UID 1000 gehören: `sudo chown -R 1000:1000 data/forgejo`) |
| `bootstrap`: Befehl nicht gefunden oder unbekannte Option | CLI-Befehle in der gewählten Forgejo-Version prüfen (`docker compose exec -u git forgejo forgejo admin user --help`); Details unter [Offene Punkte](#offene-punkte) |
| `bootstrap`: Token existiert bereits | Benutzer `renovate-bot` bzw. Token `renovate` in Forgejo löschen, siehe [Token neu erzeugen](#token-verloren-oder-neu-erzeugen) |
| Runner erscheint nicht in Forgejo | `docker compose logs runner`. Meist Proxy-Host fehlt (URL nicht erreichbar) oder Runner-Token ungültig. Dann `./data/runner/.runner` und `secrets/runner/runner_token` löschen, `docker compose run --rm bootstrap` und `docker compose up -d runner` |
| Workflow bleibt „Waiting“ | Kein Runner mit passendem Label online. Label im Workflow ist `ubuntu-latest` (oder `docker`) |
| Workflow: `docker: Cannot connect to the Docker daemon` | `dind` läuft nicht oder `DOCKER_HOST` stimmt nicht: `docker compose ps dind`, `runner-config.yml` prüfen |
| Workflow: `actions/checkout` kann nicht geladen werden | Server erreicht github.com nicht, Netzwerk/Firewall prüfen |
| `docker push`: `unauthorized` oder `denied` | Token ohne `package: write`, Benutzername/Repo-Pfad nicht kleingeschrieben, oder Secrets `REGISTRY_USER`/`REGISTRY_TOKEN` fehlen |
| `docker push`: `413 Request Entity Too Large` | `client_max_body_size 0;` im NPM-Proxy-Host fehlt |
| Renovate findet keine Repos | `renovate-bot` ist nicht als Mitwirkender mit Schreibrechten eingetragen; Log mit `docker compose logs renovate` prüfen |
| Renovate: 401 oder 403 | Token ungültig oder Scopes zu eng; Token neu erzeugen |
| Git über SSH: `Connection refused` | Port `FORGEJO_SSH_PORT` in Firewall nicht freigegeben, oder falscher Port in der URL |
| Uptime Kuma nicht erreichbar | Proxy-Host für `uptime-kuma:3001` fehlt (siehe oben) |

---

## Offene Punkte

Noch nicht auf einem Server getestet. Vor dem produktiven Einsatz auf einem Testserver prüfen (`docker compose config`, dann `docker compose up`):

- Image-Tags `FORGEJO_VERSION` und `RUNNER_VERSION` gegen die Registries
- Init-Container: `forgejo generate secret`, `forgejo admin user create/list`, `forgejo forgejo-cli actions generate-runner-token`, `generate-access-token --raw`, `su-exec` und `bash` im Image, `__FILE`-Variablen
- Healthcheck mit `curl` im Forgejo-Image
- Runner-Registrierung und Netz `forgejo-ci` für Job-Container (`docker build` und `docker push` aus einem Workflow)
- Dateirechte der Bind-Mounts unter `./data` (UID 1000 für Forgejo)
- restic: ob das Repository automatisch angelegt wird, und der Restore-Ablauf
- Neuerzeugung von Tokens mit `docker compose run --rm bootstrap`
- Menünamen in Forgejo und NPM sind sinngemäß beschrieben und können je nach Version und Sprache leicht abweichen

Weitere Entscheidungen und Hintergründe stehen in `CLAUDE.md`.
