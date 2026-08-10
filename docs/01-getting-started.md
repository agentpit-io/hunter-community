# Getting started

## Prerequisites

- Docker Engine 25+ · Docker Compose v2
- 2 CPU · 4 GB RAM · 10 GB disk (minimum for dev)

## Install

```bash
git clone https://github.com/agentpit-io/hunter-community
cd hunter-community
cp .env.example .env
```

## Configure

Edit `.env`:

- **`JWT_SECRET`** · rotate it before you expose the instance to the
  internet. Generate one:
  ```bash
  openssl rand -base64 48 | tr -d '=/+' | head -c 60
  ```
- `REGISTRATION_MODE` · `open` (anyone can register · first user is admin) ·
  `invite` (needs code from admin · first user still admin) · `closed`
- Everything else has sensible defaults.

## Start

```bash
docker compose up -d --build
```

Wait ~30 s for all four containers to become healthy:

```bash
docker compose ps
# NAME                          STATUS
# hunter-community-postgres-1   Up (healthy)
# hunter-community-redis-1      Up (healthy)
# hunter-community-api-1        Up (healthy)
# hunter-community-web-1        Up (healthy)
```

Open [http://localhost:3100](http://localhost:3100). You'll be redirected
to `/register?setup=1` to create the initial admin account.

## Behind a reverse proxy (optional)

For a public deployment put nginx (or caddy · traefik) in front, terminate
TLS, and reverse-proxy to `:3100` for the app and `:8100` for the API.

```nginx
server {
    server_name hunter.example.com;
    listen 443 ssl;
    # ... cert config ...

    location /api/ {
        proxy_pass http://127.0.0.1:8100;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 600s;
        proxy_buffering off;   # SSE-friendly
    }

    location / {
        proxy_pass http://127.0.0.1:3100;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 600s;
    }
}
```

## Upgrading

```bash
git pull
docker compose up -d --build
```

Migrations are idempotent — the API applies them on boot. To reset the
database entirely:

```bash
docker compose down -v   # ⚠ nukes users + all state
docker compose up -d --build
```

## Common issues

- **`docker compose up` hangs on `web` build** · Next.js 15 compile is
  ~2 min on first build · subsequent builds hit the cache.
- **`web` container returns 401 without any UI** · That's your nginx or
  reverse proxy sending basic-auth. Community has none by default.
- **`api` restarts continuously** · Check `docker compose logs api` —
  usually a missing env var. `HUNTER_MINIMAL_BOOT=1` (default) tolerates
  most missing config.
- **Register form 400 "该邮箱已注册"** · The email exists. Log in via
  `/login` or reset via `docker compose exec postgres psql -U hunter ...`.
