# NAS deployment plan — yt-knowledge

Status: proposed, not executed. Everything below was measured on 2026-11-01.

## What is already true

| Fact | Evidence |
|---|---|
| NAS is x86_64, Docker 29.6.2 | `uname -m`, `docker --version` over SSH |
| Ports 8484-8491 are all in use | `ss -ltn`, including 8488 held by `printdeck-web` |
| Ports 8480-8483, 8492-8499 are free | same |
| No reverse proxy runs on the NAS | no caddy/nginx/traefik container |
| Data is already there | `/volume1/docker/yt-knowledge/data`, 6.7 MB, `ytk.sqlite` verified with 1,934 videos |
| Tailscale is up on the NAS | `100.76.52.49`, direct route |
| The app has its own auth gate | one shared secret, session cookie, bearer for `/import` and `/mcp` |

The compose file currently publishes `192.168.178.40:8488:8488`, which cannot
start: `printdeck-web` owns that port.

## Decisions

1. **Port 8499.** Free, memorable, away from the 8484-8491 cluster that other
   services occupy. Recorded in `.env` and `compose.yaml`.
2. **No TLS, no reverse proxy.** One user, LAN plus Tailscale, and Tailscale
   already encrypts the off-LAN path. A Caddy container would add a certificate
   workflow and a second thing to keep alive to protect nothing that is currently
   exposed. Revisit only if this is ever reachable from the open internet.
3. **Bind `0.0.0.0` inside the container, restrict with the port mapping.** A
   bridge container does not own the host's LAN address; the mapping is what
   limits exposure.
4. **The NAS holds the only copy.** After cutover the laptop keeps source code and
   the venv, no data. `scripts/push_data.sh` stops being a sync and becomes a
   backup in the other direction, or is deleted.
5. **The image is built on the NAS.** Same architecture, no buildx, no registry.

## Steps

1. **Change the port.** `compose.yaml` and `.env.example` to 8499; `YTK_BIND`
   inside the container stays `0.0.0.0:8499`.
2. **Ship the source.** `tar czf - --exclude .venv --exclude data --exclude .git .`
   piped over SSH into `/volume1/docker/yt-knowledge/src`. Same transfer method
   that worked for the data; `rsync` is rejected by the NAS daemon ("invalid path").
3. **Write `.env` on the NAS** with a generated `YTK_SECRET`
   (`openssl rand -hex 32`), `YTK_DB=/data/ytk.sqlite`, `YTK_THUMBS=/data/thumbs`,
   `YTK_BIND=0.0.0.0:8499`, `YTK_SLEEP=8`, and the LLM trio pointing at the
   Hermes gateway. The secret must not be the test value used on the Mac.
4. **Build and start.** `docker compose build && docker compose up -d` in
   `/volume1/docker/yt-knowledge/src`. The volume maps
   `/volume1/docker/yt-knowledge/data:/data`.
5. **Verify, in this order:** `/healthz` returns `ok` and `total=1934`; the login
   page renders; `/queue` shows the same counts; `/v/<id>` renders a transcript;
   `POST /import` from the laptop collector returns 200; `/mcp` answers
   `initialize` with a bearer token.
6. **Cut over.** Point the collector at `http://192.168.178.40:8499`. Delete
   `data/` on the laptop only after step 5 passes and a fresh push has been
   confirmed on the NAS.
7. **Keep it alive.** `restart: unless-stopped` is already in the compose file.
   Nothing else supervises it; the healthcheck in the Dockerfile is what a
   future watchdog would read.

## Risks

- **Image build on a 2-core N100** will take a few minutes; `pip install yt-dlp`
  compiles nothing, so this is download time, not CPU.
- **The transcript backfill is still IP-blocked.** Moving to the NAS changes the
  source IP to the same home connection, so the block follows. Port the change,
  not the crawl.
- **No backup exists** once the laptop copy is deleted. The plan should add a
  nightly `sqlite3 .backup` to `/volume1/backup/ytk/` before step 6, or accept
  that the NAS volume is a single point of failure.
- **The `.env` secret is the only thing between the LAN and the archive.** If the
  NAS is reachable on the LAN by guests, keep the session cookie and do not add a
  "remember me".

## Explicitly not in this plan

Frames, vector search, a chat UI, multi-user, TLS, a domain name, CI. Each one is
a decision the design already declined, and none is needed to make the archive
reachable from the LAN and from Tailscale.
