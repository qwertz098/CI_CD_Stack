#!/usr/bin/env python3
"""Setup-UI für den Forgejo-Stack (nur Standardbibliothek).

Schreibt die .env, startet `docker compose up -d` und zeigt Fortschritt, Logs und
Status live an. Läuft in einem eigenen Container mit Docker-Socket (siehe setup.sh)
und ist nur mit dem beim Start ausgegebenen Token erreichbar.
"""
import hmac
import http.cookies
import json
import os
import re
import secrets
import string
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

STACK_DIR = os.environ.get("STACK_DIR", os.getcwd())
ENV_FILE = os.path.join(STACK_DIR, ".env")
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
HELP_FILE = os.path.join(STACK_DIR, "docs", "HELP.md")
PORT = int(os.environ.get("SETUP_PORT", "8080"))
TOKEN = os.environ.get("SETUP_TOKEN") or secrets.token_urlsafe(18)

# Reihenfolge entspricht der Abhängigkeitskette in compose.yml
STEP_ORDER = ["init-secrets", "db", "forgejo", "bootstrap", "dind", "runner", "renovate",
              "uptime-kuma", "db-dump", "restic", "caddy"]

# Feldbeschreibung: key -> (Vorgabe, geheim, automatisch erzeugen)
FIELDS = {
    "TZ": ("Europe/Berlin", False, False),
    "STACK_NAME": ("forgejo", False, False),
    "FORGEJO_DOMAIN": ("git.example.com", False, False),
    "FORGEJO_SSH_PORT": ("2222", False, False),
    "FORGEJO_VERSION": ("11", False, False),
    "POSTGRES_PASSWORD": ("", True, True),
    "FORGEJO_ADMIN_USER": ("forgejo-admin", False, False),
    "FORGEJO_ADMIN_PASSWORD": ("", True, True),
    "FORGEJO_ADMIN_EMAIL": ("admin@example.com", False, False),
    "PROXY_NETWORK": ("proxy", False, False),
    "PROXY": ("npm", False, False),  # npm | caddy, wird zu COMPOSE_FILE
    "STATUS_DOMAIN": ("", False, False),
    "ACME_EMAIL": ("", False, False),
    "RUNNER_VERSION": ("11", False, False),
    "RUNNER_NAME": ("runner-1", False, False),
    "RENOVATE_GITHUB_COM_TOKEN": ("", True, False),
    "BACKUP": ("false", False, False),  # true -> COMPOSE_PROFILES=backup
    "RESTIC_REPOSITORY": ("", False, False),
    "RESTIC_PASSWORD": ("", True, False),
    "AWS_ACCESS_KEY_ID": ("", True, False),
    "AWS_SECRET_ACCESS_KEY": ("", True, False),
}
# Schlüssel der .env, die nicht 1:1 aus FIELDS kommen
DERIVED = {"COMPOSE_FILE", "COMPOSE_PROFILES"}

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,30}$")
DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$")
TZ_RE = re.compile(r"^[A-Za-z0-9_+/-]{1,40}$")
BAD_VALUE = re.compile(r"[\s\"'$#\\`]")  # Zeichen, die in der .env Ärger machen

job_lock = threading.Lock()
job = {"running": False, "exit_code": None, "started": None, "lines": []}


# ----------------------------------------------------------------------- .env
def read_env():
    values = {}
    if not os.path.isfile(ENV_FILE):
        return values
    with open(ENV_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip()
    return values


def current_config():
    """Konfiguration für die UI: Geheimnisse werden nur als gesetzt/leer gemeldet."""
    env = read_env()
    cfg = {}
    for key, (default, secret, _auto) in FIELDS.items():
        if key == "PROXY":
            value = "caddy" if "compose.caddy.yml" in env.get("COMPOSE_FILE", "") else default
        elif key == "BACKUP":
            value = "true" if "backup" in env.get("COMPOSE_PROFILES", "") else "false"
        else:
            value = env.get(key, default)
        cfg[key] = {"set": bool(value)} if secret else {"value": value}
    return {"exists": os.path.isfile(ENV_FILE), "config": cfg}


def random_secret(n=24):
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


def validate(data):
    errors = {}

    def get(key):
        return str(data.get(key, "")).strip()

    def check(key, ok, message):
        if not ok:
            errors[key] = message

    check("FORGEJO_DOMAIN", DOMAIN_RE.match(get("FORGEJO_DOMAIN")), "Enter a valid domain, e.g. git.example.com")
    port = get("FORGEJO_SSH_PORT")
    check("FORGEJO_SSH_PORT", port.isdigit() and 1 <= int(port) <= 65535, "Port must be 1-65535")
    check("STACK_NAME", NAME_RE.match(get("STACK_NAME")), "Lowercase letters, digits, - and _ only")
    check("PROXY_NETWORK", NAME_RE.match(get("PROXY_NETWORK").lower()) or re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$", get("PROXY_NETWORK")), "Enter the Docker network name of your proxy")
    check("TZ", TZ_RE.match(get("TZ")), "Enter a time zone such as Europe/Berlin")
    user = get("FORGEJO_ADMIN_USER")
    check("FORGEJO_ADMIN_USER", re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,38}$", user) and user.lower() != "admin",
          "2-39 characters; \"admin\" is reserved by Forgejo")
    check("FORGEJO_ADMIN_EMAIL", re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", get("FORGEJO_ADMIN_EMAIL")), "Enter a valid e-mail address")
    check("FORGEJO_VERSION", re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,40}$", get("FORGEJO_VERSION")), "Enter an image tag, e.g. 11")
    check("RUNNER_VERSION", re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,40}$", get("RUNNER_VERSION")), "Enter an image tag, e.g. 11")
    check("RUNNER_NAME", NAME_RE.match(get("RUNNER_NAME")), "Lowercase letters, digits, - and _ only")
    check("PROXY", get("PROXY") in ("npm", "caddy"), "Choose npm or caddy")
    if get("PROXY") == "caddy":
        check("STATUS_DOMAIN", DOMAIN_RE.match(get("STATUS_DOMAIN")), "Caddy needs a domain for Uptime Kuma")
        check("ACME_EMAIL", re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", get("ACME_EMAIL")), "Caddy needs an e-mail for Let's Encrypt")
    for key in ("POSTGRES_PASSWORD", "FORGEJO_ADMIN_PASSWORD", "RESTIC_PASSWORD",
                "RENOVATE_GITHUB_COM_TOKEN", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        v = get(key)
        check(key, not v or not BAD_VALUE.search(v), "No spaces, quotes, $, # or backslashes")
    for key in ("FORGEJO_ADMIN_PASSWORD", "POSTGRES_PASSWORD"):
        v = get(key)
        check(key, not v or len(v) >= 12, "At least 12 characters (or leave empty to generate one)")
    if get("BACKUP") == "true":
        check("RESTIC_REPOSITORY", get("RESTIC_REPOSITORY") and not BAD_VALUE.search(get("RESTIC_REPOSITORY")),
              "Backup needs a restic repository, e.g. s3:https://host/bucket")
    return errors


def save_config(data):
    """Validiert, ergänzt Geheimnisse und schreibt die .env atomar (Rechte 600)."""
    errors = validate(data)
    if errors:
        return {"ok": False, "errors": errors}
    old = read_env()
    new, generated = {}, {}
    for key, (default, secret, auto) in FIELDS.items():
        value = str(data.get(key, "")).strip()
        if secret and not value:
            value = old.get(key, "")  # leer = vorhandenen Wert behalten
        if secret and not value and auto:
            value = random_secret()
            generated[key] = value
        new[key] = value
    backup = new["BACKUP"] == "true"
    if backup and not new["RESTIC_PASSWORD"]:
        new["RESTIC_PASSWORD"] = random_secret(32)
        generated["RESTIC_PASSWORD"] = new["RESTIC_PASSWORD"]

    lines = ["# Von der Setup-UI geschrieben. Per Hand änderbar; die UI behält unbekannte Schlüssel.", ""]
    for key in FIELDS:
        if key in ("PROXY", "BACKUP"):
            continue
        if key in ("RESTIC_REPOSITORY", "RESTIC_PASSWORD", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY") and not backup:
            continue
        lines.append(f"{key}={new[key]}")
    lines.append("COMPOSE_FILE=" + ("compose.yml:compose.caddy.yml" if new["PROXY"] == "caddy" else "compose.yml"))
    if backup:
        lines.append("COMPOSE_PROFILES=backup")
    known = set(FIELDS) | DERIVED
    extras = [f"{k}={v}" for k, v in old.items() if k not in known]
    if extras:
        lines += ["", "# weitere Schlüssel"] + extras

    if os.path.isfile(ENV_FILE):
        os.replace(ENV_FILE, ENV_FILE + ".bak")
        os.chmod(ENV_FILE + ".bak", 0o600)
    tmp = ENV_FILE + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, ENV_FILE)
    return {"ok": True, "generated": generated}


# --------------------------------------------------------------------- Docker
def run(cmd, timeout=30):
    try:
        p = subprocess.run(cmd, cwd=STACK_DIR, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except FileNotFoundError:
        return 127, f"{cmd[0]} not found"
    except subprocess.TimeoutExpired:
        return 124, "timeout"


def preflight():
    checks = []
    rc, out = run(["docker", "version", "--format", "{{.Server.Version}}"])
    checks.append({"name": "Docker daemon", "ok": rc == 0, "detail": out if rc == 0 else
                   "Not reachable. Is /var/run/docker.sock mounted? " + out[:200]})
    rc, out = run(["docker", "compose", "version", "--short"])
    checks.append({"name": "Docker Compose v2", "ok": rc == 0, "detail": out[:100]})
    ok = all(os.path.isfile(os.path.join(STACK_DIR, f)) for f in ("compose.yml", "bootstrap.sh"))
    checks.append({"name": "Stack files", "ok": ok, "detail": STACK_DIR if ok else f"compose.yml/bootstrap.sh missing in {STACK_DIR}"})
    exists = os.path.isfile(ENV_FILE)
    checks.append({"name": ".env", "ok": exists, "detail": "present" if exists else "Save the configuration first"})
    net = read_env().get("PROXY_NETWORK", "proxy")
    rc, _ = run(["docker", "network", "inspect", net])
    checks.append({"name": f"Proxy network \"{net}\"", "ok": rc == 0, "detail":
                   "exists" if rc == 0 else "missing", "fix": None if rc == 0 else "create_network"})
    return checks


def compose_ps():
    rc, out = run(["docker", "compose", "ps", "-a", "--format", "json"])
    if rc != 0 or not out:
        return []
    items = []
    try:
        parsed = json.loads(out)
        items = parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        for line in out.splitlines():
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    rows = []
    for it in items:
        rows.append({"service": it.get("Service", ""), "name": it.get("Name", ""),
                     "state": it.get("State", ""), "health": it.get("Health", ""),
                     "exit_code": it.get("ExitCode", 0), "status": it.get("Status", "")})
    rows.sort(key=lambda r: STEP_ORDER.index(r["service"]) if r["service"] in STEP_ORDER else 99)
    return rows


def compose_stats(names):
    if not names:
        return {}
    rc, out = run(["docker", "stats", "--no-stream", "--format", "{{json .}}"] + names, timeout=20)
    stats = {}
    if rc == 0:
        for line in out.splitlines():
            try:
                s = json.loads(line)
                stats[s["Name"]] = {"cpu": s.get("CPUPerc", ""), "mem": s.get("MemUsage", "").split(" / ")[0]}
            except (json.JSONDecodeError, KeyError):
                pass
    return stats


def start_job(cmd):
    with job_lock:
        if job["running"]:
            return False
        job.update(running=True, exit_code=None, started=time.time(), lines=[])

    def worker():
        try:
            p = subprocess.Popen(cmd, cwd=STACK_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for line in p.stdout:
                with job_lock:
                    job["lines"].append(line.rstrip())
            p.wait()
            code = p.returncode
        except Exception as exc:  # noqa: BLE001 - Fehler in der UI anzeigen
            with job_lock:
                job["lines"].append(f"error: {exc}")
            code = 1
        with job_lock:
            job.update(running=False, exit_code=code)

    threading.Thread(target=worker, daemon=True).start()
    return True


# ------------------------------------------------------------------- HTTP-Teil
class Handler(BaseHTTPRequestHandler):
    server_version = "stack-setup"

    def log_message(self, fmt, *args):  # kein Zugriffslog mit Token in der URL
        pass

    # --- Hilfen
    def authed(self):
        header = self.headers.get("X-Setup-Token", "")
        cookie = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
        value = header or (cookie["setup_token"].value if "setup_token" in cookie else "")
        return hmac.compare_digest(value, TOKEN)

    def send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, ctype):
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            return self.send_json({"error": "not found"}, 404)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def deny(self):
        self.send_json({"error": "unauthorized: open the URL with ?token=... printed at startup"}, 401)

    def sse_start(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

    def sse(self, event, payload):
        self.wfile.write(f"event: {event}\ndata: {json.dumps(payload)}\n\n".encode())
        self.wfile.flush()

    # --- GET
    def do_GET(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if url.path == "/" and "token" in query:
            if not hmac.compare_digest(query["token"][0], TOKEN):
                return self.deny()
            self.send_response(302)
            self.send_header("Set-Cookie", f"setup_token={TOKEN}; HttpOnly; SameSite=Strict; Path=/")
            self.send_header("Location", "/")
            self.end_headers()
            return
        if url.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if not self.authed():
            return self.deny()
        if url.path == "/":
            return self.send_file(os.path.join(STATIC_DIR, "index.html"), "text/html; charset=utf-8")
        if url.path == "/api/config":
            return self.send_json(current_config())
        if url.path == "/api/preflight":
            return self.send_json({"checks": preflight()})
        if url.path == "/api/networks":
            rc, out = run(["docker", "network", "ls", "--format", "{{.Name}}"])
            return self.send_json({"networks": sorted(out.split()) if rc == 0 else []})
        if url.path == "/api/status":
            return self.send_json({"services": compose_ps(), "job": {"running": job["running"], "exit_code": job["exit_code"]}})
        if url.path == "/api/stats":
            names = [r["name"] for r in compose_ps() if r["state"] == "running"]
            return self.send_json({"stats": compose_stats(names)})
        if url.path == "/api/help":
            return self.send_file(HELP_FILE, "text/markdown; charset=utf-8")
        if url.path == "/api/events":
            return self.events()
        if url.path == "/api/logs":
            return self.logs(query.get("service", [""])[0])
        self.send_json({"error": "not found"}, 404)

    def events(self):
        """SSE: Ausgabe des laufenden Jobs und alle 2 s ein Status-Schnappschuss."""
        self.sse_start()
        sent, last_status = 0, 0.0
        try:
            while True:
                with job_lock:
                    new = job["lines"][sent:]
                    state = {"running": job["running"], "exit_code": job["exit_code"]}
                sent += len(new)
                if new:
                    self.sse("log", new)
                if time.time() - last_status > 2:
                    self.sse("status", {"services": compose_ps(), "job": state})
                    last_status = time.time()
                else:
                    self.sse("job", state)
                time.sleep(0.7)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def logs(self, service):
        if service and not re.match(r"^[a-z0-9_-]+$", service):
            return self.send_json({"error": "bad service"}, 400)
        self.sse_start()
        cmd = ["docker", "compose", "logs", "-f", "--no-color", "--tail", "100"] + ([service] if service else [])
        p = subprocess.Popen(cmd, cwd=STACK_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            for line in p.stdout:
                self.sse("log", [line.rstrip()])
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            p.kill()

    # --- POST
    def do_POST(self):
        if not self.authed():
            return self.deny()
        if self.headers.get("X-Requested-With") != "stack-setup":
            return self.send_json({"error": "missing header"}, 400)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self.send_json({"error": "invalid json"}, 400)
        path = urlparse(self.path).path
        if path == "/api/config":
            return self.send_json(save_config(data))
        if path == "/api/network":
            name = str(data.get("name", ""))
            if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$", name):
                return self.send_json({"ok": False, "error": "bad name"}, 400)
            rc, out = run(["docker", "network", "create", name])
            return self.send_json({"ok": rc == 0, "detail": out})
        if path == "/api/start":
            if not os.path.isfile(ENV_FILE):
                return self.send_json({"ok": False, "error": "Save the configuration first"}, 400)
            started = start_job(["docker", "compose", "up", "-d", "--remove-orphans"])
            return self.send_json({"ok": started, "error": None if started else "A run is already in progress"})
        self.send_json({"error": "not found"}, 404)


def main():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Stack setup UI on port {PORT}, stack directory {STACK_DIR}", flush=True)
    print(f"Open: http://127.0.0.1:{os.environ.get('SETUP_PUBLISHED_PORT', PORT)}/?token={TOKEN}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
