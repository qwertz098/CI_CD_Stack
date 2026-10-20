# Forgejo Stack Help

## What this stack is

A self-hosted software forge for small teams and single developers, run with Docker Compose on one Linux server. It replaces a hosted Git service plus a hosted CI service plus a hosted package registry.

You get:

- Git hosting with pull requests, issues and a web UI (Forgejo)
- A container registry and an npm registry, built into Forgejo
- CI with Forgejo Actions. Workflow files are GitHub-Actions-compatible, so `uses: actions/checkout@v4` works
- Automatic pull requests for outdated dependencies (Renovate)
- Uptime monitoring (Uptime Kuma)
- Optional nightly database dump and encrypted offsite backup (restic)

Why it exists: you keep code, built images and CI under your own control, and an LLM agent or a human can work against one URL for all of it: clone, push, open a pull request, watch CI, publish an image.

Design decisions that matter when you work with it:

- **Deploy is manual.** A release happens when you push a Git tag like `v1.2.3`. There is no auto-deploy and no Watchtower.
- **CI builds run in Docker-in-Docker** (`dind`). Jobs never touch the host Docker socket.
- **The runner reaches Forgejo through the public URL**, so the reverse proxy has to be up before the runner works.
- **TLS is terminated by a separate reverse proxy** (an existing Nginx Proxy Manager, or the included Caddy).

## Components

| Service | Purpose |
| --- | --- |
| `forgejo` | Git, web UI, registries, Actions server |
| `db` | Postgres for Forgejo |
| `init-secrets` | one-shot: creates `SECRET_KEY` and `INTERNAL_TOKEN` |
| `bootstrap` | one-shot: creates the admin, the `renovate-bot` user and the tokens |
| `dind` | Docker daemon used by CI jobs (privileged container) |
| `runner` | Forgejo Actions runner, registers itself on first start |
| `renovate` | opens dependency update PRs every 6 hours |
| `uptime-kuma` | monitoring UI |
| `db-dump`, `restic` | optional backup, profile `backup` |
| `caddy` | optional reverse proxy instead of Nginx Proxy Manager |

Container names start with the stack name (default `forgejo`), for example `forgejo-db`.

## Set up the stack

You need a Linux server with Docker and the Compose plugin, a domain pointing at it, ports 80 and 443 reachable, and either a running Nginx Proxy Manager or the included Caddy.

### With the setup UI (recommended)

1. On the server, in the stack directory, run `./setup.sh`.
2. Open the printed URL. It contains a one-time token. From your own machine use an SSH tunnel: `ssh -L 8080:127.0.0.1:8080 user@server`.
3. **Configure**: fill in domain, admin, proxy and optionally backup. Save. A generated admin password is shown once.
4. **Setup**: the preflight checks Docker, files and the proxy network. Create the network if it is missing, then press *Start setup*. Progress and logs update live.
5. **Status**: shows every container with health, CPU and memory.
6. Stop the setup UI with `Ctrl+C`. It has full Docker access and is only needed for setup and changes.

### Without the UI

```
cp .env.example .env     # fill in the values
docker network create proxy   # only if your proxy network does not exist yet
docker compose up -d
```

Both ways do the same thing: the UI writes the same `.env` and runs the same `docker compose up -d`.

### After the first start

1. Create the proxy host: your domain to container `<stack name>`, port `3000`, websockets on, request size unlimited (`client_max_body_size 0;` in Nginx). With Caddy this is already configured.
2. Log in to Forgejo with the admin account.
3. Add the user `renovate-bot` as collaborator to each repository, or as member of an organization, otherwise Renovate finds nothing.
4. Optional: add Uptime Kuma monitors for your Forgejo URL.

## Work with the stack day to day

| Task | How |
| --- | --- |
| Change configuration | edit `.env` or use the setup UI, then `docker compose up -d` |
| See what runs | `docker compose ps` or the *Status* tab |
| Read logs | `docker compose logs -f <service>` |
| Update the stack | raise `FORGEJO_VERSION` / `RUNNER_VERSION` in `.env`, then `docker compose pull && docker compose up -d` |
| Lost a token | delete the file in `./secrets/...`, then `docker compose run --rm bootstrap` (for the Renovate token delete the user `renovate-bot` in Forgejo first) |
| Stop everything | `docker compose down` (data stays in `./data`) |

Never commit `.env`, `secrets/`, `data/` or `backup/`. They are in `.gitignore`.

## Connect a project (CI)

CI runs on every push to `main` and on every pull request.

1. Create the repository in Forgejo and push your code.
2. Copy `workflow-ci.yml` from this stack into the project as `.forgejo/workflows/ci.yml`.
3. Push. The run shows up in the repository under *Actions*.

The example workflow checks out the code, installs Node 22, then runs `npm ci`, lint, tests and build (each only if the script exists). Adapt it for other languages: any step is a normal shell command, and any image with the needed tooling can be used.

Runner labels: `ubuntu-latest` (a Ubuntu-like image) and `docker` (Node 22 image). Jobs run in containers. The runner runs two jobs at the same time (`capacity: 2` in `runner-config.yml`).

### What a green CI means

A pull request is mergeable when the *CI* check is green. Keep CI fast and deterministic: no interactive prompts, no network services that are not started inside the job, no secrets in logs.

## Release and deploy (CD)

Release is a Git tag. Deploy is a manual pull on the target server.

### One-time setup per project

1. In Forgejo create an access token for a user who may push packages, with the scope `write:package`.
2. In the repository open *Settings > Actions > Secrets* and add `REGISTRY_USER` (the Forgejo username) and `REGISTRY_TOKEN` (the token).
3. Copy `workflow-release.yml` into the project as `.forgejo/workflows/release.yml`.
4. The project needs a `Dockerfile` in its root.

Image names must be lowercase: `git.example.com/<owner>/<repo>`.

### Cutting a release

```
git checkout main && git pull
git tag v1.2.3
git push origin v1.2.3
```

The release workflow runs the tests, builds the image, and pushes `:1.2.3` and `:latest` to the Forgejo registry. Watch it under *Actions*. If it fails, fix the cause and publish a new tag (`v1.2.4`). Do not move an existing tag.

### Deploying on the target server

Log in once with a token that has `read:package`:

```
docker login git.example.com -u <user>
```

Reference the image in the compose file of the target server, with the version pinned:

```
services:
  app:
    image: git.example.com/<owner>/<repo>:1.2.3
    restart: unless-stopped
```

Then, whenever you release:

```
# set the new version in compose.yml, then
docker compose pull && docker compose up -d
```

Rollback is the same step with the previous version number.

## Dependency updates

Copy `renovate.json` into each repository. Renovate opens pull requests for outdated npm packages and Docker image tags, bundled as minor/patch groups, before 6 am on Mondays. CI runs on those PRs like on any other. Merging them is a human decision.

## Backup (optional)

Enable it with the *Backup* section in the setup UI, or set `COMPOSE_PROFILES=backup` plus `RESTIC_REPOSITORY`, `RESTIC_PASSWORD` (and `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` for S3) in `.env`.

- `db-dump` writes a Postgres dump to `./backup/db` every night at 03:00 and keeps three days.
- `restic` backs up the dumps, `data/forgejo` and `data/kuma` encrypted to your repository at 03:30.
- The running `data/postgres` directory is deliberately not backed up. Restore the database from a dump.
- Store the restic password somewhere else. Without it the backup cannot be read.
- Initialize the repository once: `docker compose exec restic restic init`.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| `docker compose up -d` stops with "dependency failed" | `docker compose logs bootstrap` or `init-secrets`, fix the error, run `docker compose up -d` again |
| Runner restarts in a loop | the proxy host is missing or the domain does not resolve from the server; `docker compose logs runner` |
| Workflow cannot pull `actions/checkout` | the server cannot reach github.com |
| `docker build` fails in a job with "cannot connect to the Docker daemon" | `dind` is not running; `docker compose ps dind` |
| Push of an image fails with `unauthorized` | `REGISTRY_USER` and `REGISTRY_TOKEN` secrets, token scope `write:package`, lowercase image name |
| Large image push fails with 413 | proxy host needs an unlimited request size |
| Name conflict on `docker compose up` | another container uses the same name; change `STACK_NAME` |
| Renovate opens nothing | `renovate-bot` is not a collaborator of the repository |

The full German documentation is in the [README](https://github.com/qwertz098/CI_CD_Stack/blob/main/README.md).

## For LLM agents that implement changes

This section is written for an LLM agent working on a project that lives in this forge. Follow it literally.

### Facts to rely on

- Forgejo URL: `https://<FORGEJO_DOMAIN>`. API base: `https://<FORGEJO_DOMAIN>/api/v1`. Authenticate with the header `Authorization: token <TOKEN>`. Ask the human for a token with the scopes you need (`write:repository` for pushing and pull requests, `read:repository` for reading). Never print or commit the token.
- Git remote: `https://<FORGEJO_DOMAIN>/<owner>/<repo>.git` over HTTPS with the token, or SSH on the configured SSH port.
- CI definitions live in `.forgejo/workflows/*.yml` and follow GitHub Actions syntax. Actions are fetched from github.com.
- CI jobs run in containers. `docker build` and `docker push` work because `DOCKER_HOST` points at the `dind` service. Jobs have no access to the host.
- The registry host is the Forgejo domain. Image names are lowercase.
- Release is a pushed tag `v<major>.<minor>.<patch>`. Deploy is never automatic.

### Workflow for a change

1. Create a branch from `main`. Never push to `main` directly.
2. Make the smallest change that solves the task. Run lint, tests and build locally first, using the same commands the CI workflow runs.
3. Commit with a message that says what changed and why. Do not commit `.env`, tokens, keys or generated build output.
4. Push the branch and open a pull request:

```
curl -sS -X POST "https://<FORGEJO_DOMAIN>/api/v1/repos/<owner>/<repo>/pulls" \
  -H "Authorization: token $TOKEN" -H "Content-Type: application/json" \
  -d '{"title":"...","head":"<branch>","base":"main","body":"..."}'
```

5. Wait for CI and read the result instead of assuming it:

```
curl -sS -H "Authorization: token $TOKEN" \
  "https://<FORGEJO_DOMAIN>/api/v1/repos/<owner>/<repo>/commits/<sha>/status"
```

   `state` is `success`, `pending` or `failure`. On failure open the run under *Actions*, read the failing step's log, fix the cause and push again. Do not disable, skip or weaken a test to get green.
6. Merging and tagging are human decisions unless the human asked you to do them. Releasing means pushing a tag; ask before you do it.

### Rules

- Verify claims. If you say a check passed, you have the check result in front of you.
- Do not invent version tags, image names or paths. Read them from the repository, the compose file or the API.
- Keep secrets out of logs, commits, PR descriptions and workflow files. Use repository secrets (`${{ secrets.NAME }}`).
- Workflow changes (`.forgejo/workflows/`) change what runs with your credentials. Keep them small and explain them in the PR.
- Prefer idempotent steps. A workflow that is re-run must not break.
- This stack has not been fully tested on a real server. Points not yet verified are listed in the README under "Offene Punkte". If you hit one of them, report it with the exact command and output instead of working around it silently.

### Definition of done

A change is done when: the branch builds, lint and tests pass locally, the pull request is open with a clear description, CI is green on the latest commit, and no secret is part of the diff.
