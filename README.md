# Forgejo-Stack

Selbst gehosteter Git-Server mit Container-Registry, npm-Registry und CI, per Docker Compose.

- **Forgejo** mit Postgres: Git, Container-Registry und npm-Registry
- **Forgejo Actions** (GitHub-Actions-kompatibel): Runner mit Docker-in-Docker, kein Zugriff auf den Host-Docker
- **Renovate**: PRs für veraltete npm-Pakete und Docker-Images
- **Uptime Kuma**: Monitoring
- **Backup** (optional): nächtlicher Postgres-Dump, restic schiebt alles verschlüsselt offsite
- **Setup-UI**: Weboberfläche zum Konfigurieren, Aufbauen und Beobachten des Stacks (`./setup.sh`)
- **Reverse Proxy und TLS**: ein separater Reverse Proxy, nicht Teil des Basis-Stacks. Standard ist ein bestehender Nginx Proxy Manager (NPM); als Alternative liegt eine fertige **Caddy**-Konfiguration bei

> **Status:** Der Stack ist noch **nicht auf einem Server getestet**. Befehle und Menüpfade unten sind nach bestem Wissen beschrieben; was ausdrücklich ungetestet ist, steht unter [Offene Punkte](#offene-punkte). Am besten zuerst auf einem Testserver durchspielen.

**Hilfe:** Beschreibung, Arbeitsweise, CI/CD-Anleitung und Hinweise für LLM-Agenten stehen in [`docs/HELP.md`](docs/HELP.md) (Englisch). Die Setup-UI zeigt sie im Reiter *Help*.

### Setup-UI

```
./setup.sh
```

Startet eine Weboberfläche auf `127.0.0.1:8080` (Token steht in der Konsole; von außen per SSH-Tunnel). Sie schreibt die `.env`, führt `docker compose up -d` aus und zeigt Ablauf, Logs und Status. Sie bekommt den Docker-Socket und damit Root auf dem Host: nur für das Setup starten und danach mit Strg+C beenden. Das Image `ghcr.io/qwertz098/ci_cd_stack-setup` baut der Workflow `.github/workflows/setup-image.yml`; fehlt es, baut `setup.sh` lokal aus `setup/`.

Ohne `setup.sh` geht es auch per Compose: `docker compose -f compose.setup.yml up` startet dasselbe Image aus ghcr (Datei `compose.setup.yml`, Port per `SETUP_PORT`, Version per `SETUP_VERSION`). Das Paket muss dafür öffentlich sein. Das Backup ist optional (Profil `backup`).

## Inhalt

1. [Voraussetzungen und Randbedingungen](#voraussetzungen-und-randbedingungen)
2. [Überblick](#überblick)
3. [Installation Schritt für Schritt](#installation-schritt-für-schritt)
4. [Reverse Proxy: Caddy statt NPM](#reverse-proxy-caddy-statt-npm)
5. [Init-Container im Detail](#init-container-im-detail)
6. [Erstes Projekt anbinden](#erstes-projekt-anbinden)
7. [Release und Deploy](#release-und-deploy)
8. [Renovate](#renovate)
9. [Monitoring mit Uptime Kuma](#monitoring-mit-uptime-kuma)
10. [Backup und Restore](#backup-und-restore)
11. [Betrieb: Updates, Tokens, Reset](#betrieb-updates-tokens-reset)
12. [Fehlersuche](#fehlersuche)
13. [Offene Punkte](#offene-punkte)

---

## Voraussetzungen und Randbedingungen

Der Stack ist für die folgenden Randbedingungen ausgelegt. Passen sie nicht, sind meist kleine Anpassungen in `compose.yml` oder `.env` nötig; die betroffenen Stellen sind jeweils genannt.

### Randbedingungen

| Bereich | Annahme | Wenn es anders ist |
| --- | --- | --- |
| Betrieb | ein einzelner Linux-Server mit Docker Engine und Compose v2, Betrieb per Docker Compose, kein Orchestrator | für Kubernetes o. ä. nicht gedacht |
| Erreichbarkeit | eigene Domain mit DNS-Eintrag auf den Server; Ports 80 und 443 aus dem Internet erreichbar (Zertifikate, Weboberfläche); ein Port für Git über SSH (Standard `2222`) | andere SSH-Ports über `FORGEJO_SSH_PORT`; ohne öffentliche Erreichbarkeit funktionieren Let's-Encrypt-Zertifikate per HTTP-Challenge nicht |
| Reverse Proxy und TLS | ein **separater** Reverse Proxy terminiert TLS und hängt im gemeinsamen Docker-Netz `proxy`. Standard: bestehender Nginx Proxy Manager (NPM). Alternative: Caddy (siehe [Reverse Proxy: Caddy statt NPM](#reverse-proxy-caddy-statt-npm)) | Netzname über `PROXY_NETWORK` änderbar |
| Ausgehender Zugriff | der Server erreicht github.com (Actions werden von dort geladen, Renovate liest Changelogs) sowie die Image-Registries (Docker Hub, `ghcr.io`, `codeberg.org`, `code.forgejo.org`) | hinter einer Firewall die Ziele freigeben |
| CI | Forgejo Actions; Runner mit Docker-in-Docker. Dafür läuft ein **privilegierter** Container (`dind`); der Host-Docker-Socket wird bewusst nicht benutzt | ohne privilegierte Container ist dieses CI-Konzept nicht möglich |
| Runner und Forgejo | der Runner erreicht Forgejo über die **öffentliche URL**, nicht über das interne Netz | der Reverse Proxy muss vor dem Runner-Start stehen |
| Deploy | manuell per Git-Tag (`v1.2.3`), danach `docker compose pull && docker compose up -d` auf dem Zielserver; kein Auto-Deploy, kein Watchtower | |
| Updates | Abhängigkeiten in Projekten per Renovate; Updates dieses Stacks manuell | |
| Backup | offsite per restic (S3-kompatibel oder SFTP); Datenbank über täglichen Dump | ohne Backup-Ziel `restic` aus `compose.yml` entfernen |
| Nutzung | kleines Team oder Einzelperson; Registrierung geschlossen, der Admin legt Benutzer an | offene Registrierung: `DISABLE_REGISTRATION` in `compose.yml` ändern |
| Sprache | Dokumentation und Kommentare auf Deutsch | |

### Das musst du bereithalten

- einen Server mit Docker Engine und Compose-Plugin (`docker compose version`)
- eine Domain für Forgejo (z. B. `git.example.com`), optional eine zweite für Uptime Kuma (bei Caddy Pflicht)
- eine E-Mail-Adresse für das Zertifikatskonto (nur bei Caddy)
- einen Reverse Proxy: einen laufenden NPM **oder** die mitgelieferte Caddy-Konfiguration
- ein Backup-Ziel für restic samt Zugangsdaten
- Firewall-Freigaben: 80/tcp, 443/tcp, bei Caddy zusätzlich 443/udp (HTTP/3), sowie der Git-SSH-Port

### Welcher Reverse Proxy?

| | Nginx Proxy Manager (Standard) | Caddy (Alternative) |
| --- | --- | --- |
| Voraussetzung | NPM läuft schon (z. B. für andere Dienste) und hängt im Netz `proxy` | nichts Bestehendes nötig; Caddy gehört dann zu diesem Stack |
| Konfiguration | über die Weboberfläche, von Hand | eine Textdatei (`Caddyfile`), im Repo versioniert |
| Zertifikate | pro Host in der Oberfläche beantragen | automatisch, inklusive Erneuerung und Weiterleitung HTTP auf HTTPS |
| Mehrere Dienste auf dem Server | gut geeignet | geeignet, aber dann sollte Caddy auch deine anderen Dienste übernehmen |
| Beide gleichzeitig | nicht möglich, beide brauchen 80/443 auf demselben Host | |

Faustregel: Gibt es schon einen NPM, bleib dabei. Fängst du neu an oder willst die Proxy-Konfiguration im Git haben, nimm Caddy.

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
| `compose.caddy.yml` | optional: Caddy-Dienst als Ergänzung zu `compose.yml` |
| `Caddyfile` | optional: Konfiguration für Caddy |
| `CLAUDE.md` | Entscheidungen und Hintergründe |

### Ablauf beim Start

Es gibt keinen Web-Installer und keine manuellen Token-Schritte. `docker compose up -d` erledigt alles in dieser Reihenfolge:

| Schritt | Dienst | Aufgabe |
| --- | --- | --- |
| 1 | `init-secrets` | erzeugt `SECRET_KEY` und `INTERNAL_TOKEN` in `./secrets/forgejo` (vorhandene Dateien bleiben) |
| 2 | `db`, `forgejo` | Forgejo startet mit `INSTALL_LOCK`, Healthcheck gegen `/api/healthz` |
| 3 | `bootstrap` | legt Admin und `renovate-bot` an, erzeugt Renovate-Access-Token und Runner-Registrierungs-Token |
| 4 | `dind`, `runner`, `renovate` | starten, sobald `bootstrap` erfolgreich war |

`init-secrets` und `bootstrap` laufen einmal und beenden sich (`Exited (0)`). Beide sind idempotent: Wiederholte Läufe ändern nichts, solange die Benutzer und die Token-Dateien in `./secrets` existieren. Details stehen unter [Init-Container im Detail](#init-container-im-detail).

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
| `proxy` (extern) | gemeinsames Netz mit dem Reverse Proxy (NPM oder Caddy); Forgejo und Uptime Kuma hängen darin |
| `forgejo-ci` | Runner, Docker-in-Docker und Job-Container |

---

## Installation Schritt für Schritt

### Schritt 0: Voraussetzungen prüfen

Auf dem Server:

```bash
docker --version          # Docker Engine
docker compose version    # Compose-Plugin, v2
```

Die übrigen Voraussetzungen (Domain, Reverse Proxy, Backup-Ziel, Firewall) stehen unter [Voraussetzungen und Randbedingungen](#voraussetzungen-und-randbedingungen). Gehe sie vor dem Start durch.

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

Existiert das Netz nicht, anlegen (bei NPM zusätzlich den NPM-Container hineinhängen; bei Caddy tritt Caddy dem Netz selbst bei):

```bash
docker network create proxy
docker network connect proxy <name-des-npm-containers>
```

Heißt dein Netz anders, setze `PROXY_NETWORK` in der `.env` entsprechend.

### Schritt 4: Stack starten

> **Mit Caddy statt NPM:** Vorher die Schritte aus [Reverse Proxy: Caddy statt NPM](#reverse-proxy-caddy-statt-npm) erledigen (zwei zusätzliche Variablen in der `.env`). Der Startbefehl bleibt derselbe, wenn `COMPOSE_FILE` in der `.env` gesetzt ist.

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

### Schritt 5: Reverse Proxy einrichten

Der Runner und Renovate sprechen Forgejo über die öffentliche URL an. Solange der Proxy-Host fehlt, starten sie deshalb neu (`restart: unless-stopped`) und fangen sich, sobald die URL erreichbar ist.

**Variante B (Caddy):** nichts zu tun, die Konfiguration kommt aus dem `Caddyfile`. Weiter mit Schritt 6; Details unter [Reverse Proxy: Caddy statt NPM](#reverse-proxy-caddy-statt-npm).

#### Variante A: Nginx Proxy Manager

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

## Reverse Proxy: Caddy statt NPM

Optionale Alternative zum Nginx Proxy Manager. Caddy übernimmt TLS-Zertifikate, Weiterleitung auf HTTPS und die Verteilung der Anfragen an Forgejo und Uptime Kuma. Es ersetzt Schritt 5 der Installation.

### Was dazugehört

| Datei | Zweck |
| --- | --- |
| `compose.caddy.yml` | fügt den Dienst `caddy` zum Stack hinzu |
| `Caddyfile` | die Konfiguration: welche Domain geht an welchen Dienst |
| `.env` | `STATUS_DOMAIN`, `ACME_EMAIL`, `COMPOSE_FILE` |

Das Basis-`compose.yml` bleibt unverändert. Wer beim NPM bleibt, braucht beide Dateien nicht.

### Einrichtung Schritt für Schritt

1. **NPM stilllegen, falls vorhanden.** Caddy und NPM können nicht gleichzeitig die Ports 80 und 443 belegen. Läuft auf dem Server noch ein NPM für andere Dienste, nimm Caddy nicht oder verschiebe auch diese Dienste in das `Caddyfile`.
2. **DNS prüfen:** `FORGEJO_DOMAIN` und `STATUS_DOMAIN` (z. B. `git.example.com` und `status.example.com`) zeigen per A/AAAA-Eintrag auf den Server.
3. **Firewall:** 80/tcp, 443/tcp und 443/udp freigeben (HTTP/3).
4. **`.env` ergänzen:**

   ```bash
   STATUS_DOMAIN=status.example.com
   ACME_EMAIL=du@example.com
   COMPOSE_FILE=compose.yml:compose.caddy.yml
   ```

   (`COMPOSE_FILE` ist in `.env.example` auskommentiert vorbereitet. Ohne diese Zeile gibst du bei jedem Aufruf `-f compose.yml -f compose.caddy.yml` an.)
5. **Proxy-Netz anlegen** (das Basis-`compose.yml` erwartet es als extern): `docker network create proxy`
6. **Konfiguration prüfen:**

   ```bash
   docker compose config -q && echo OK
   docker compose run --rm --no-deps caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
   ```

   Die erste Zeile meldet fehlende Variablen (`STATUS_DOMAIN`, `ACME_EMAIL`), die zweite prüft das `Caddyfile` und sollte `Valid configuration` ausgeben.
7. **Starten:** `docker compose up -d`
8. **Zertifikate beobachten:**

   ```bash
   docker compose logs -f caddy
   ```

   Erwartet werden Meldungen wie `certificate obtained successfully` für beide Domains. Das dauert beim ersten Start einige Sekunden.
9. **Testen:**

   ```bash
   curl -I https://git.example.com
   curl -I http://git.example.com     # muss auf https weiterleiten
   ```

### Vollständige Konfiguration

**`compose.caddy.yml`**

```yaml
# Optionale Ergänzung zu compose.yml: Caddy statt Nginx Proxy Manager.
#
# Starten:
#   docker compose -f compose.yml -f compose.caddy.yml up -d
# oder dauerhaft in der .env:  COMPOSE_FILE=compose.yml:compose.caddy.yml
#
# Caddy hängt im Netz "proxy" und erreicht Forgejo und Uptime Kuma über deren Namen.
# Das Netz muss vorher existieren:  docker network create proxy
# Nicht gleichzeitig mit einem NPM auf demselben Host betreiben (beide brauchen 80/443).

services:
  caddy:
    image: caddy:2
    container_name: caddy
    restart: unless-stopped
    environment:
      TZ: ${TZ}
      FORGEJO_DOMAIN: ${FORGEJO_DOMAIN}
      STATUS_DOMAIN: ${STATUS_DOMAIN:?STATUS_DOMAIN in der .env setzen (Domain für Uptime Kuma)}
      ACME_EMAIL: ${ACME_EMAIL:?ACME_EMAIL in der .env setzen}
    ports:
      - "80:80"
      - "443:443"
      # HTTP/3 (QUIC)
      - "443:443/udp"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      # Zertifikate und ACME-Konto: muss erhalten bleiben (Rate-Limits von Let's Encrypt)
      - caddy-data:/data
      - caddy-config:/config
    depends_on:
      - forgejo
      - uptime-kuma
    networks:
      - proxy

volumes:
  caddy-data:
  caddy-config:
```

**`Caddyfile`**

```
# Caddy als Alternative zum Nginx Proxy Manager (siehe README, "Reverse Proxy: Caddy").
# Alle Werte kommen aus der .env (über compose.caddy.yml als Umgebungsvariablen).
{
	# E-Mail für das ACME-Konto (Let's Encrypt): Ablaufwarnungen und Kontowiederherstellung
	email {$ACME_EMAIL}

	# Zum Testen auskommentieren: Staging-CA von Let's Encrypt (keine Rate-Limits, aber
	# Zertifikate sind nicht vertrauenswürdig). Danach wieder entfernen und neu laden.
	# acme_ca https://acme-staging-v02.api.letsencrypt.org/directory
}

# --- Forgejo: Weboberfläche, Container-Registry, npm-Registry, API ---
{$FORGEJO_DOMAIN} {
	encode zstd gzip

	header {
		Strict-Transport-Security "max-age=31536000"
		X-Content-Type-Options "nosniff"
		-Server
	}

	# Websockets laufen automatisch mit. Eine Größenbegrenzung für Uploads gibt es
	# in Caddy standardmäßig nicht (Registry-Layer sind groß), nichts zu setzen.
	reverse_proxy forgejo:3000
}

# --- Uptime Kuma: Monitoring-Oberfläche ---
{$STATUS_DOMAIN} {
	encode zstd gzip

	header {
		Strict-Transport-Security "max-age=31536000"
		X-Content-Type-Options "nosniff"
		-Server
	}

	reverse_proxy uptime-kuma:3001
}
```

### Wie es funktioniert

**Rolle im Stack.** Caddy nimmt Anfragen aus dem Internet auf Port 80 und 443 entgegen, beendet die TLS-Verschlüsselung und reicht die Anfrage unverschlüsselt über das interne Docker-Netz `proxy` weiter:

```
Browser / docker / npm ──HTTPS──► caddy :443 ──HTTP──► forgejo :3000
                                         └────HTTP──► uptime-kuma :3001
git (SSH) ───────────────────────────────────────────► forgejo :22 (veröffentlicht als 2222, nicht über Caddy)
```

SSH ist kein HTTP und läuft deshalb direkt an den Forgejo-Container, genau wie beim NPM.

**Aufbau des `Caddyfile`.**

- Der erste Block in `{ ... }` ohne Adresse enthält **globale Optionen**; hier die E-Mail für das Zertifikatskonto.
- Danach folgt je ein **Site-Block**, dessen Kopf die Domain ist (`{$FORGEJO_DOMAIN}`). Alles darin gilt nur für Anfragen an diese Domain.
- `{$NAME}` wird beim Laden durch die Umgebungsvariable ersetzt. Die Variablen setzt `compose.caddy.yml` aus der `.env`.

**Automatisches HTTPS.** Eine Domain als Site-Adresse genügt, damit Caddy

1. beim Start ein Zertifikat per ACME bei Let's Encrypt anfordert. Der Nachweis, dass die Domain dir gehört, läuft über Port 80 (HTTP-Challenge) oder 443 (TLS-ALPN-Challenge). Deshalb müssen DNS und Ports stimmen.
2. das Zertifikat speichert (Volume `caddy-data`, `/data` im Container) und rechtzeitig vor Ablauf selbst erneuert.
3. Port 80 automatisch auf HTTPS umleitet.

Das Volume `caddy-data` darf nicht verloren gehen: Bei jedem Neuaufbau ohne Volume würden Zertifikate neu angefordert, und Let's Encrypt begrenzt das (Rate-Limits). Das Volume wird nicht von restic gesichert; bei Verlust holt sich Caddy neue Zertifikate. Zum Testen eignet sich die Staging-CA (auskommentierte Zeile `acme_ca` im `Caddyfile`).

**Weiterleitung (`reverse_proxy`).** `reverse_proxy forgejo:3000` schickt die Anfrage an den Container `forgejo`. Der Name wird über den Docker-DNS im gemeinsamen Netz `proxy` aufgelöst, deshalb muss Caddy in diesem Netz hängen. Caddy setzt dabei automatisch die Header `X-Forwarded-For`, `X-Forwarded-Proto` und `X-Forwarded-Host`. Forgejo kennt seine öffentliche Adresse über `ROOT_URL`. In den Forgejo-Logs erscheint als Client-Adresse die Docker-Adresse von Caddy, nicht die echte IP.

**Websockets und große Uploads.** Websockets (für Uptime Kuma und die Forgejo-Oberfläche) erkennt und leitet Caddy ohne Zusatzeinstellung durch. Eine Obergrenze für die Größe von Anfragen gibt es standardmäßig nicht. Das Gegenstück zu `client_max_body_size 0;` im NPM ist also, nichts zu setzen. Container-Layer für die Registry lassen sich deshalb ohne Sonderregel hochladen.

**Die übrigen Direktiven.**

| Direktive | Wirkung |
| --- | --- |
| `encode zstd gzip` | komprimiert Antworten, soweit der Client es unterstützt |
| `header Strict-Transport-Security "max-age=31536000"` | Browser nutzen für ein Jahr nur noch HTTPS für diese Domain. Erst setzen, wenn HTTPS dauerhaft funktioniert |
| `header X-Content-Type-Options "nosniff"` | verhindert MIME-Raten im Browser |
| `header -Server` | entfernt den Server-Header der Antwort |

**HTTP/3.** Die Zeile `443:443/udp` veröffentlicht den UDP-Port für QUIC. Fällt der Client darauf zurück, geht es normal über TCP.

### Betrieb

| Aufgabe | Befehl |
| --- | --- |
| `Caddyfile` nach Änderung ohne Ausfall neu laden | `docker compose exec -w /etc/caddy caddy caddy reload --config /etc/caddy/Caddyfile` |
| Datei vorher prüfen | `docker compose run --rm --no-deps caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile` |
| Logs ansehen | `docker compose logs -f caddy` |
| Caddy aktualisieren | `docker compose pull caddy && docker compose up -d caddy` |

### Wechsel von NPM zu Caddy

1. Backup prüfen. Forgejo selbst bleibt unverändert, `ROOT_URL` und Domain bleiben gleich.
2. `.env` ergänzen und `Caddyfile` prüfen (Schritte 4 bis 6 oben).
3. NPM stoppen, damit 80/443 frei werden: `docker stop <npm-container>`.
4. `docker compose up -d`. Caddy holt die Zertifikate neu.
5. Test: Weboberfläche, `git push` über SSH und `docker login git.example.com` prüfen. Runner und Renovate erreichen Forgejo weiter über die öffentliche URL.

Zurück zu NPM: `COMPOSE_FILE` aus der `.env` entfernen, `docker compose up -d --remove-orphans`, NPM wieder starten.

### Fehlersuche bei Caddy

| Symptom | Ursache und Lösung |
| --- | --- |
| `docker compose config` meldet `STATUS_DOMAIN`/`ACME_EMAIL` fehle | Variablen in der `.env` setzen |
| Caddy startet nicht: `address already in use` | Port 80 oder 443 ist belegt, meist läuft noch der NPM oder ein anderer Webserver (`sudo ss -ltnp \| grep ':80\|:443'`) |
| Kein Zertifikat, Logs zeigen ACME-Fehler | DNS zeigt nicht auf den Server, Port 80/443 von außen gesperrt, oder Rate-Limit erreicht. Mit der Staging-CA testen |
| `502 Bad Gateway` | `forgejo` bzw. `uptime-kuma` läuft nicht oder ist nicht im Netz `proxy`: `docker compose ps`, `docker network inspect proxy` |
| Browser zeigt Zertifikatswarnung | Staging-CA ist noch aktiv; `acme_ca`-Zeile entfernen, neu laden |
| Registry-Push scheitert mit 413 | tritt bei Caddy normalerweise nicht auf; prüfen, ob davor noch ein anderer Proxy sitzt |

---

## Init-Container im Detail

Zwei Einmal-Dienste ersetzen den Web-Installer und alle manuellen Token-Schritte. Beide laufen mit `restart: "no"`, beenden sich nach getaner Arbeit (`Exited (0)`) und sind idempotent.

```
init-secrets ──► db ──► forgejo (healthy) ──► bootstrap ──► runner
 (Schlüssel)            (INSTALL_LOCK)        (Benutzer,  └─► renovate
                                               Tokens)
```

### Schritt A: `init-secrets` (vor Forgejo)

**Aufgabe:** erzeugt die beiden Schlüssel, die Forgejo sonst beim Web-Installer selbst anlegen würde.

Was der Dienst tut (aus `compose.yml`):

1. legt `./secrets/forgejo`, `./secrets/runner` und `./secrets/renovate` an und setzt `./secrets` auf `700`
2. erzeugt `secret_key` und `internal_token` mit `forgejo generate secret SECRET_KEY` bzw. `INTERNAL_TOKEN`, **nur wenn die Datei fehlt oder leer ist**
3. setzt die Schlüsseldateien auf Besitzer `1000:1000` (Forgejo-Benutzer) und Modus `600`, das Verzeichnis `secrets/forgejo` auf `700`
4. setzt `secrets/renovate` auf `755`, weil Renovate im Container als unprivilegierter Benutzer läuft. Der Schutz liegt im `700`-Elternverzeichnis auf dem Host

Wie Forgejo die Schlüssel bekommt (Umgebung des Dienstes `forgejo`):

| Variable | Wert | Wirkung |
| --- | --- | --- |
| `FORGEJO__security__INSTALL_LOCK` | `true` | Installer wird übersprungen, Datenbank-Migration läuft beim Start |
| `FORGEJO__security__SECRET_KEY__FILE` | `/secrets/secret_key` | Schlüssel wird aus der Datei gelesen |
| `FORGEJO__security__INTERNAL_TOKEN__FILE` | `/secrets/internal_token` | dito |

Der Dienst `forgejo` wartet auf `init-secrets` (`service_completed_successfully`) und bekommt nur `./secrets/forgejo` gemountet (read-only).

**Prüfen:**

```bash
docker compose logs init-secrets
sudo ls -l secrets/forgejo      # secret_key und internal_token, Besitzer 1000, Modus -rw-------
```

**Einzeln ausführen:** `docker compose run --rm init-secrets`. Vorhandene Schlüssel bleiben unverändert.

> **Wichtig:** Ändere oder lösche `secret_key` und `internal_token` nie bei einer laufenden Installation. Mit einem neuen `SECRET_KEY` lassen sich verschlüsselte Inhalte (z. B. 2FA-Geheimnisse) nicht mehr lesen.

### Schritt B: `bootstrap` (nach Forgejo)

**Aufgabe:** legt Benutzer an und erzeugt die Tokens, die Runner und Renovate brauchen.

**Voraussetzung:** Forgejo ist `healthy` (Healthcheck gegen `/api/healthz`). `bootstrap` nutzt das Forgejo-Image, aber nicht den Server: Die `forgejo`-CLI arbeitet direkt gegen die Datenbank (Netz `internal`) und liest `app.ini` aus `./data/forgejo`.

**Eingaben** (aus `.env`, vom Compose durchgereicht):

| Variable | Zweck |
| --- | --- |
| `FORGEJO_DOMAIN` | E-Mail-Adresse von `renovate-bot` (`renovate@<domain>`) |
| `FORGEJO_ADMIN_USER` | Name des Admins |
| `FORGEJO_ADMIN_PASSWORD` | Passwort des Admins |
| `FORGEJO_ADMIN_EMAIL` | E-Mail des Admins |

Fehlt eine davon, bricht das Skript sofort ab.

**Ablauf von `bootstrap.sh`:**

| Nr. | Schritt | Merker für „schon erledigt“ |
| --- | --- | --- |
| 1 | Admin anlegen (`forgejo admin user create --admin ...`, ohne Passwortwechsel-Zwang) | Benutzer steht in `forgejo admin user list` |
| 2 | Benutzer `renovate-bot` anlegen (normaler Benutzer, Zufallspasswort) | dito |
| 3 | Access-Token `renovate` für `renovate-bot` erzeugen, Scopes `write:repository,write:issue,read:user,read:organization`, Ausgabe nach `secrets/renovate/renovate_token` | Datei `renovate_token` existiert und ist nicht leer |
| 4 | Runner-Registrierungs-Token erzeugen (`forgejo forgejo-cli actions generate-runner-token`), Ausgabe nach `secrets/runner/runner_token` | Datei `runner_token` existiert und ist nicht leer |

Details, die das Skript beachtet:

- Die CLI läuft nie als root, sondern per `su-exec 1000:1000`.
- Tokens werden erst in eine `.tmp`-Datei geschrieben und dann atomar umbenannt. Bei leerer Ausgabe bricht das Skript ab, es bleibt keine halbe Datei zurück.
- Dateimodi: `runner_token` `600`, `renovate_token` `644` (siehe Schritt A).
- Das Passwort von `renovate-bot` wird nirgends gespeichert. Es zählt nur der Token.

**Prüfen:**

```bash
docker compose logs bootstrap
```

Beim ersten Lauf erwartest du `Lege Admin ... an`, `Lege Benutzer renovate-bot an`, `Erzeuge Access-Token ...`, `Erzeuge Runner-Registrierungs-Token` und `Fertig`. Bei jedem weiteren Lauf stattdessen `... existiert bereits` bzw. `... vorhanden`.

Token testen (liefert die Daten von `renovate-bot`):

```bash
curl -s -H "Authorization: token $(sudo cat secrets/renovate/renovate_token)" \
  https://git.example.com/api/v1/user
```

**Erneut ausführen:** `docker compose run --rm bootstrap`. Das ist gefahrlos, solange Benutzer und Token-Dateien existieren. Zum Neuerzeugen eines Tokens siehe [Token verloren oder neu erzeugen](#token-verloren-oder-neu-erzeugen).

### Schritt C: Runner und Renovate lesen die Tokens

| Dienst | Wartet auf | Token | Verwendung |
| --- | --- | --- | --- |
| `runner` | `dind` (gestartet), `bootstrap` (erfolgreich) | `/secrets/runner_token` (aus `./secrets/runner`) | wird nur beim ersten Start für `forgejo-runner register` benutzt, solange `./data/runner/.runner` fehlt |
| `renovate` | `bootstrap` (erfolgreich) | `/secrets/renovate_token` (aus `./secrets/renovate`) | wird beim Start als `RENOVATE_TOKEN` exportiert |

Die Tokens stehen damit weder in `.env` noch in `docker inspect` des Runners oder Renovates, sondern nur in den gemounteten Dateien.

### Anpassen

- **Andere Token-Rechte für Renovate:** Variable `RENOVATE_SCOPES` in `bootstrap.sh` ändern, dann den Token neu erzeugen (siehe oben). Scopes so eng wie möglich halten.
- **Weitere Benutzer oder Tokens:** in `bootstrap.sh` nach dem gleichen Muster ergänzen (Existenzprüfung per `user_exists` bzw. Token-Datei als Merker, `write_secret` zum Schreiben) und im `compose.yml` das passende Unterverzeichnis von `./secrets` in den Verbraucher mounten.
- **Neues Unterverzeichnis für einen weiteren Verbraucher:** in `init-secrets` per `mkdir -p` anlegen und Besitzer/Modus passend zur UID des Verbrauchers setzen.
- **Nach Änderungen:** `docker compose config -q`, dann `docker compose run --rm bootstrap` und `docker compose logs bootstrap`.

### Sicherheitshinweise

- `FORGEJO_ADMIN_PASSWORD` bleibt in der `.env` und ist im `bootstrap`-Container als Umgebungsvariable sichtbar (`docker inspect forgejo-bootstrap`). `.env` hat deshalb `600`. Nach dem ersten Login das Passwort in Forgejo ändern. Das Skript prüft das Passwort bei jedem Lauf nur auf Vorhandensein, es setzt es bei bestehendem Konto nicht zurück.
- `secrets/` gehört nicht ins Git (`.gitignore`) und nicht in `docker compose`-Ausgaben oder Tickets. Es wird auch nicht von restic gesichert, sichere es separat (Passwortmanager) oder stelle es mit `docker compose run --rm bootstrap` neu her.

### Fehlersuche bei den Init-Containern

| Symptom | Ursache und Lösung |
| --- | --- |
| `init-secrets` bricht ab: `forgejo: not found` oder Befehl unbekannt | Der Tag `FORGEJO_VERSION` liefert ein anderes Image-Layout. Image prüfen (`docker run --rm --entrypoint sh codeberg.org/forgejo/forgejo:<tag> -c 'which forgejo'`) |
| `forgejo` startet nicht, `permission denied` auf `/secrets/...` | Besitzer oder Modus der Schlüsseldateien stimmt nicht: `docker compose run --rm init-secrets` ausführen, danach `sudo ls -ln secrets/forgejo` (Besitzer 1000) |
| `forgejo` bleibt `unhealthy` | `docker compose logs forgejo`, siehe [Fehlersuche](#fehlersuche) (u. a. `curl` im Image, Datenbankpasswort) |
| `bootstrap` wartet ewig oder startet nicht | `forgejo` ist nicht `healthy`: `docker compose ps forgejo` |
| `bootstrap`: `su-exec`/`bash` nicht gefunden | Das Image enthält die Werkzeuge nicht. `bootstrap.sh` und `entrypoint` im Compose an die Werkzeuge des Images anpassen |
| `bootstrap`: Option oder Unterbefehl unbekannt | CLI der Forgejo-Version prüfen: `docker compose exec -u git forgejo forgejo admin user --help` und `... forgejo forgejo-cli actions --help` |
| `bootstrap`: Benutzername reserviert oder ungültig | `FORGEJO_ADMIN_USER` ändern (nicht `admin`), dann `docker compose run --rm bootstrap` |
| `bootstrap`: Token mit diesem Namen existiert schon | Token-Datei fehlt, der Token in Forgejo aber nicht. `renovate-bot` in Forgejo löschen und `bootstrap` erneut ausführen |
| `runner`/`renovate` starten nicht, „dependency failed“ | `bootstrap` ist fehlgeschlagen: `docker compose logs bootstrap`, Fehler beheben, `docker compose up -d` |
| Token-Datei leer | `bootstrap` bricht in diesem Fall ab. Datei löschen und `docker compose run --rm bootstrap` |

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

Uptime Kuma hängt im Netz `proxy`, hat aber im Basis-Stack **keinen** Proxy-Host. Bei Caddy ist er über `STATUS_DOMAIN` bereits konfiguriert, dann ab Punkt 2. Mit NPM:

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

Das Caddy-Volume `caddy-data` (Zertifikate) wird nicht gesichert; Caddy beschafft Zertifikate bei Verlust neu. Bei SFTP-Zielen (z. B. Storage Box) muss zusätzlich ein SSH-Key in den `restic`-Container gemountet werden; das ist in `compose.yml` nicht vorbereitet.

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
- Caddy: `Caddyfile` ist mit `caddy validate` (v2.10.0) geprüft, `compose.caddy.yml` mit `docker compose config`. Ein echter Lauf mit Domain, Zertifikat und Weiterleitung auf Forgejo ist ungetestet
- Menünamen in Forgejo und NPM sind sinngemäß beschrieben und können je nach Version und Sprache leicht abweichen

Weitere Entscheidungen und Hintergründe stehen in `CLAUDE.md`.
