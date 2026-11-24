# Advanced Speech to Text ("Verbatim")

Self-hosted speech-to-text. Upload or record audio and get a transcript with a timestamp on every word. Then:

- **Highlight text to hear it.** Select any words and press *Play* to hear exactly that span.
- **Click a word to jump there.** The current word is highlighted while the audio plays (karaoke style).
- **Fix mistakes inline.** Word timings are kept wherever the words still match.
- **Save highlights with notes**, and download a highlight as an audio clip.
- **Search every transcript.** Postgres full-text search with stemming. Results deep-link to the moment.
- **Export** as TXT, SRT or VTT.
- **Multi-user.** The admin creates accounts. Users see only their own recordings, and admins see everything.

Transcription runs locally with [faster-whisper](https://github.com/SYSTRAN/faster-whisper) on CPU. No audio leaves your server.

## Architecture

```
            ┌──────────── reverse proxy (TLS) ────────────┐
browser ───▶│                                             │
            └──────────────┬──────────────────────────────┘
                           ▼
                    ┌─────────────┐   jobs (SKIP LOCKED)   ┌──────────────┐
                    │ web         │◀──────── Postgres ────▶│ worker       │
                    │ FastAPI +   │                        │ ffmpeg +     │
                    │ static UI   │                        │ faster-whisper│
                    └──────┬──────┘                        └──────┬───────┘
                           └──────────── ./media ─────────────────┘
```

- **web** serves the API and the zero-dependency HTML/CSS/JS frontend.
- **worker** converts each upload into a browser-friendly M4A, then transcribes that same file, so timestamps match playback exactly.
- **Postgres** stores users, sessions, transcripts (word timings as JSONB), highlights and the job queue. No Redis is needed.
- **Media** goes in a plain folder on the host (`MEDIA_PATH`), with one sub-folder per recording.

Web and worker use the same image and just run different commands.

## Deploy

The host never builds anything. Every compose file for deployment **pulls the pre-built image** from GHCR, which GitHub Actions builds on each release. Each file is pinned to that release's image digest.

| File | Use it for |
|---|---|
| `docker-compose.yml` (repo root) | The current release. Updated automatically by each release. |
| `deploy/compose/docker-compose.vX.Y.Z.yml` | Pinning or rolling back to a specific version. |
| `docker-compose.dev.yml` | Local development only. Builds from source. |

On the host you only need two files: `docker-compose.yml` and `.env`.

```bash
mkdir verbatim && cd verbatim
# download docker-compose.yml and .env.example from the latest GitHub Release:
#   https://github.com/ashleycollinge1/advanced-speech-to-text/releases/latest
cp .env.example .env          # set POSTGRES_PASSWORD and ADMIN_PASSWORD (12+ chars)
docker login ghcr.io          # only while the package is private (token needs read:packages)
docker compose pull
docker compose up -d
```

Then point your reverse proxy at `127.0.0.1:8000` and sign in as `ADMIN_USERNAME`.

**No model is downloaded automatically.** On first sign-in, the library asks you to download a transcription model. Click **Download** on a model, confirm, and watch the progress bar. Uploads and recording unlock as soon as it finishes. Each model is downloaded once into the `models` volume (about 1.6 GB for the default). After that, transcription runs **fully offline**.

### Reverse proxy

The app speaks plain HTTP and trusts `X-Forwarded-*` headers. Your proxy must allow large uploads and long requests.

**Caddy**

```caddy
stt.example.com {
    request_body {
        max_size 2GB
    }
    reverse_proxy 127.0.0.1:8000
}
```

**nginx**

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    client_max_body_size 2g;
    proxy_request_buffering off;   # stream uploads straight through
    proxy_read_timeout 600s;
}
```

**Traefik:** remove the `ports:` mapping, attach `web` to Traefik's network, and add the usual router labels. Traefik doesn't limit body size by default.

Browser recording needs HTTPS (or `localhost`), so it works once the app is behind your TLS proxy.

### Network access & login cookies

The web container is published as `APP_BIND:APP_PORT → 8000`, and `COOKIE_SECURE` must match how people open the app. Set these in `.env` and run `docker compose up -d` after changing them.

| How users reach the app | `APP_BIND` | `COOKIE_SECURE` |
|---|---|---|
| `https://…` via a reverse proxy **on the same host** (not in Docker) | `127.0.0.1` *(default)* | `true` *(default)* |
| `https://…` via a reverse proxy **on another machine** | `0.0.0.0`, or that host's LAN IP | `true` |
| `https://…` via a proxy **in Docker** on the same host | Not needed: put `web` on the proxy's network and target `web:8000` (you can remove `ports:`) | `true` |
| `http://<host>:<port>` directly (LAN or testing only) | `0.0.0.0`, or the LAN IP | **`false`** |

**`APP_BIND`** is the network address on the host that Docker listens on:
- `127.0.0.1` accepts connections only from the host itself.
- `0.0.0.0` accepts them on every interface.
- A specific IP (e.g. `192.168.1.50`) accepts them only on that interface.

Docker-published ports bypass `ufw` on most Linux hosts. With `0.0.0.0` on an internet-facing machine, restrict access at your router or cloud firewall.

**`COOKIE_SECURE`** marks the session cookie `Secure`, so browsers only send it over HTTPS. Browsers refuse to store a Secure cookie on a plain-`http://` page, so with `true` over HTTP the login succeeds and is immediately forgotten.

**Troubleshooting**

| Symptom | Cause | Fix |
|---|---|---|
| Containers healthy and logs fine, but the page won't load from another machine | `APP_BIND=127.0.0.1` (the default) | Set `APP_BIND=0.0.0.0` (or the LAN IP). `docker compose ps` should then show `0.0.0.0:<port>->8000/tcp`. |
| *"Your session ended. Please sign in again."* straight after every login | `COOKIE_SECURE=true` but browsing over `http://` | Use your `https://` proxy address, or set `COOKIE_SECURE=false` for plain-HTTP LAN use |
| Same message even over `https://` | Mixing addresses (proxy hostname vs raw IP:port), or a proxy that strips cookies | Always use the proxy hostname, and check the proxy passes `Set-Cookie` and `Cookie` headers through unchanged |

Plain HTTP sends passwords and session cookies unencrypted. Keep `COOKIE_SECURE=false` to trusted networks.

### Configuration

All settings are environment variables. See [`.env.example`](.env.example) for the full annotated list.

| Variable | Default | Notes |
|---|---|---|
| `POSTGRES_PASSWORD` | *(required)* | |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | `admin` / — | Used **only on first start** to create the admin. Changing them later has no effect. |
| `COOKIE_SECURE` | `true` | `true` when users browse via `https://`; must be `false` for plain `http://`. See [Network access & login cookies](#network-access--login-cookies). |
| `APP_BIND` / `APP_PORT` | `127.0.0.1` / `8000` | Host address and port for the web UI. `127.0.0.1` = this host only; `0.0.0.0` = reachable from other machines. See below. |
| `MEDIA_PATH` | `./media` | Host folder for audio. |
| `MAX_UPLOAD_MB` | `2048` | |
| `WHISPER_MODELS` | `small.en,medium.en,large-v3-turbo,large-v3` | Models users can choose, fastest first. |
| `WHISPER_MODEL` | `large-v3-turbo` | Preselected default. See [Choosing a model](#choosing-a-model). |
| `WHISPER_THREADS` | `0` (all cores) | |
| `WHISPER_COMPUTE_TYPE` | `int8` | `int8` is fastest on CPU. |

### Choosing a model

Users pick a model per upload, and can re-transcribe any finished recording with a different one. Highlights are kept, but manual edits are replaced. The choices come from `WHISPER_MODELS`, and the preselected one is `WHISPER_MODEL`.

| Tier | Model | Download | Measured speed* |
|---|---|---|---|
| Fast | `small.en` | ~460 MB | 0.15× real time |
| Balanced | `medium.en` | ~1.4 GB | 0.4× |
| Accurate *(default)* | `large-v3-turbo` | ~1.6 GB | 0.5× |
| Most accurate | `large-v3` | ~3.1 GB | 0.7× |

\* Measured on a 16-core CPU with int8 on a short clip. For example, 0.7× means a 1-hour recording takes about 42 minutes. Speed scales with core count, so expect it to be slower on smaller machines.

The worker keeps **one model in memory at a time**. Switching models takes a few seconds to load from disk. Models must be downloaded first: see [Model downloads & offline operation](#model-downloads--offline-operation). large-v3 needs about 3–4 GB of RAM while it runs. Jobs run one after another, so a long large-v3 job delays the ones behind it. If you have RAM to spare, `docker compose up -d --scale worker=2` runs two jobs in parallel.

### Model downloads & offline operation

- **Only on request:** models are downloaded only when a signed-in user clicks **Download** in the model picker (in the library or the re-transcribe dialog) and confirms. Progress is shown live, and the worker downloads in the background, so transcription jobs keep running.
- **Offline transcription:** jobs can only use downloaded models. The worker loads them with `local_files_only=True`, so a running server never needs the internet. This was tested with all outbound traffic blocked.
- **No telemetry:** the image sets `HF_HUB_DISABLE_TELEMETRY=1` and `ORT_DISABLE_TELEMETRY=1`. Without the second, onnxruntime (used for silence detection) uploads usage telemetry to Microsoft. With both set, the worker's only network connection while transcribing is to Postgres.
- **Startup scan:** on start, the worker checks which models are already on disk. Upgrading an install where models were downloaded by an older version marks them ready, with no re-download. If files go missing, the model shows as not downloaded again.
- **Disk space:** a download is refused if the volume doesn't have enough free space.

### Vocabulary hints

Each upload (and each re-transcribe) can include **Names & terms**: a short comma-separated list such as `Claude, Anthropic, Siobhán, OKRs`. These are passed to faster-whisper as `hotwords`, which are re-sent for every 30-second window, so they help across the whole of a long recording. (`initial_prompt` would only affect the first 30 seconds with our settings.)

- **Limits:** up to 400 characters after cleanup, at most 100 terms, each 60 characters or less. The worker also checks the model's real token budget (223 tokens) and logs any terms it has to drop, so nothing is cut off silently.
- **Formatting:** hints are always sent ending with a full stop. Whisper copies the style of its prompt, and an unpunctuated list made it drop punctuation from the transcript.
- **What to expect:** hints nudge the model when the audio is ambiguous; they don't override what it clearly hears. In testing, a `Claude` hint fixed "cloud-based" → "Claude-based" on `small.en`, but `large-v3` stuck with "cloud". Keep lists short and specific: unrelated terms can get "heard" in noise.

### Backups

Back up two things: the Postgres database and the media folder.

```bash
docker compose exec -T db pg_dump -U stt stt | gzip > stt-$(date +%F).sql.gz
tar czf media-$(date +%F).tgz media/
```

### Upgrading

Replace `docker-compose.yml` with the newer one from the [latest Release](https://github.com/ashleycollinge1/advanced-speech-to-text/releases/latest). The file is pinned to an exact image, so `docker compose pull` alone won't upgrade an old copy. Then run `docker compose pull && docker compose up -d`. Database migrations run automatically when the web container starts. To roll back, use the matching `deploy/compose/docker-compose.vX.Y.Z.yml`.

## Releases (CI/CD)

- **Every push and PR** runs lint, tests against a real Postgres, and a test Docker build ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)).
- **Pushing a tag** such as `git tag v1.2.0 && git push origin v1.2.0` triggers [`.github/workflows/release.yml`](.github/workflows/release.yml), which:
  1. runs the tests,
  2. builds the image and pushes it to `ghcr.io/ashleycollinge1/advanced-speech-to-text` with the tags `1.2.0`, `1.2` and `sha-…`,
  3. renders [`deploy/compose.template.yml`](deploy/compose.template.yml) into `deploy/compose/docker-compose.v1.2.0.yml`, pinned to the image **digest**, updates `docker-compose.latest.yml` and the root `docker-compose.yml`, and commits them to `main`.
  4. publishes a GitHub **Release** for the tag, with notes (changes since the previous tag, the image digest and upgrade steps) and that version's `docker-compose.yml` and `.env.example` attached.

Pre-release tags (`v1.2.0-rc.1`) get their own compose file but don't change `latest` or the root file. The commit step pushes to `main` with the built-in `GITHUB_TOKEN`. If `main` is branch-protected, allow GitHub Actions to bypass the protection.

To change what gets deployed, edit `deploy/compose.template.yml`, not the generated files.

## Development

```bash
docker compose -f docker-compose.dev.yml up --build   # http://localhost:8000  (admin / change-me-please)
```

The dev compose builds from source, sets `COOKIE_SECURE=false` for plain HTTP, and uses throwaway credentials.

Tests need a Postgres instance:

```bash
docker run -d --rm --name stt-test-pg -e POSTGRES_USER=stt -e POSTGRES_PASSWORD=stt -e POSTGRES_DB=stt -p 55432:5432 postgres:17-alpine
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest            # uses postgresql+psycopg://stt:stt@localhost:55432/stt by default
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

To create a migration after changing `app/models.py`:

```bash
DATABASE_URL=postgresql+psycopg://stt:stt@localhost:55432/stt .venv/bin/alembic revision --autogenerate -m "describe change"
```

### Layout

```
app/                 FastAPI app, worker, models, security
  routers/           auth, users, recordings (+ transcript, highlights, search)
  transcript.py      word re-alignment after edits; SRT/VTT/TXT export
alembic/             database migrations (run automatically on start)
frontend/            index.html, css/, js/ (ES modules), fonts/ (self-hosted)
deploy/              compose template + generated per-version compose files
docker/entrypoint.sh web | worker | migrate
```

## Security notes

- **Passwords** are hashed with **Argon2id** (`argon2-cffi`) and rehashed automatically when the parameters change. The minimum length is 12. New users and password resets get a one-time temporary password that must be changed at first sign-in.
- **Sessions** are random 256-bit tokens in an `HttpOnly`, `SameSite=Lax` cookie (and `Secure` by default). Only a SHA-256 of each token is stored. Changing your password signs out your other sessions, and deactivating a user signs them out everywhere.
- **CSRF:** every state-changing request needs a per-session `X-CSRF-Token` header.
- **Login throttling:** 5 failures per username (or 20 per IP) within 15 minutes triggers a temporary lockout. Unknown usernames take the same time to reject as wrong passwords.
- **Strict CSP** (`default-src 'self'`, no inline scripts or styles) plus `nosniff`, `frame-ancestors 'none'` and `Referrer-Policy`. No third-party requests are made, and the fonts are self-hosted.
- **Access control:** media is only served by authenticated endpoints. Another user's recording returns 404, so recording IDs can't be probed.
- **Containers:** run as an unprivileged user (uid 1000). The entrypoint briefly starts as root only to fix ownership of the mounted folders.
