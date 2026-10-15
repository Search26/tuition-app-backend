# Deploying to a VPS

One Ubuntu 24.04 VPS runs the whole stack from `compose.prod.yml`. Caddy terminates HTTPS
(Let's Encrypt) and is the only thing the internet can reach; nginx, the API, the worker,
Postgres, Redis and MinIO stay on Docker's internal network. MinIO is also bound to the VPS's
loopback address, so an admin can upload videos through an SSH tunnel.

You need:

- a VPS: 2 vCPU, 4 GB RAM, 40 GB+ disk (videos and backups grow it), Ubuntu 24.04, ideally in
  Singapore or Vietnam. `compose.prod.yml`'s memory and CPU limits assume this size;
- a domain or subdomain, e.g. `api.yourdomain.com` (Let's Encrypt won't issue for a bare IP);
- SSH access as a sudo user.

Commands below run on the VPS unless they say "on your laptop".

## 1. DNS

Create an `A` record for `api.yourdomain.com` pointing at the VPS's IPv4 address (and `AAAA` if it
has IPv6). Check from your laptop: `dig +short api.yourdomain.com` prints the VPS IP.

## 2. Harden the VPS

```bash
sudo adduser deploy && sudo usermod -aG sudo deploy        # if you only have root
# on your laptop: ssh-copy-id deploy@api.yourdomain.com
sudo sed -i 's/^#\?PasswordAuthentication .*/PasswordAuthentication no/; s/^#\?PermitRootLogin .*/PermitRootLogin no/' /etc/ssh/sshd_config
sudo systemctl restart ssh
sudo ufw allow OpenSSH && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp && sudo ufw allow 443/udp
sudo ufw enable
sudo apt-get update && sudo apt-get install -y unattended-upgrades git make python3 netcat-openbsd
```

`ufw` does not see ports Docker publishes (Docker writes its own iptables rules). That is why
`compose.prod.yml` publishes only Caddy's 80/443 and MinIO on `127.0.0.1` — never add a `ports:`
line for anything else.

## 3. Install Docker

```bash
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list
sudo apt-get update && sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo usermod -aG docker deploy   # log out and back in
```

## 4. Get the code and generate secrets

```bash
sudo mkdir -p /opt/tuition && sudo chown deploy: /opt/tuition
git clone https://github.com/Search26/tuition-app-backend.git /opt/tuition
cd /opt/tuition
git checkout main
scripts/gen-prod-env.sh api.yourdomain.com
```

`.env.prod` now has a random value for every password and secret, `APP_ENV=prod` and
`LOG_FORMAT=json`. Keep it only on the VPS (it is git-ignored, mode 600). The API refuses to start
in prod while any secret still contains `change-me`.

## 5. Build and start

```bash
make prod-build    # builds the API image (prod target) and MinIO; MinIO compiles from source, ~5 min
make prod-up       # data services, then migrations, then everything else; waits until healthy
make prod-ps
```

Caddy gets the certificate on the first request to the domain. If it can't (DNS not pointing
here yet, port 80 blocked), `make prod-logs SERVICE=caddy` says why.

## 6. Admin account and content

```bash
curl -fsS -X POST https://api.yourdomain.com/auth/register -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"<a long password>","name":"Admin"}'
make prod-admin EMAIL=you@example.com
make prod-seed     # optional: the sample courses
```

**Uploading videos (on your laptop).** The upload goes straight to MinIO, which only listens on the
VPS's loopback, so open a tunnel first and leave it running:

```bash
ssh -N -L 9000:127.0.0.1:9000 deploy@api.yourdomain.com
# in another terminal, from your clone of the repo:
API_URL=https://api.yourdomain.com ADMIN_EMAIL=you@example.com make upload LESSON=<lesson id> FILE=lesson1.mp4
```

The presigned URL is signed for `localhost:9000` (`MINIO_PUBLIC_ENDPOINT`), which is the tunnel.

## 7. Backups

```bash
sudo cp deploy/cron/tuition-backup /etc/cron.d/tuition-backup
sudo scripts/backup.sh          # run one now; prints {"event":"backup","status":"ok",...}
ls -lh /var/backups/tuition/db
```

Every night at 03:00 (VPS time): a `pg_dump` into `/var/backups/tuition/db` (newest 14 kept) and a
mirror of the bucket into `/var/backups/tuition/minio`. Results go to `/var/log/tuition-backup.log`.

These copies sit on the same disk as the data. For a copy off the VPS, add an rclone remote to
`deploy/rclone.conf` and name it in `.env.prod`, for example Backblaze B2:

```ini
# deploy/rclone.conf
[b2]
type = b2
account = <keyID>
key = <applicationKey>
```

```bash
# .env.prod
BACKUP_REMOTE=b2:your-bucket/tuition
```

## 8. Check it from outside

On your laptop:

```bash
for port in 22 80 443 5432 6379 8000 8080 9000 9001; do
  nc -z -w 3 api.yourdomain.com $port && echo "$port open" || echo "$port closed"
done
```

Only 22, 80 and 443 may be open.

Then the smoke test (on the VPS or your laptop; needs curl and python3):

```bash
make smoke URL=https://api.yourdomain.com
```

It checks health, registers and logs in a new user, enrolls, streams a ready lesson (master,
variant, init, segment, and a tampered URL refused), sends a progress batch as a phone coming back
online would and resends it (`applied`, then `skipped_stale`, course percentage up), and checks HSTS
and the http→https redirect. Push notifications are skipped until Sprint 7.

**On a real iPhone over 4G** (Wi-Fi off), with the app pointed at `https://api.yourdomain.com`:
log in, play a lesson and change quality, download it, turn on airplane mode, watch part of it
offline, turn airplane mode off, and check the progress shows up in `GET /me/courses`.

## Day to day

| Task | Command |
| --- | --- |
| Status | `make prod-ps` |
| Logs (JSON, one line each) | `make prod-logs SERVICE=api` (or `nginx`, `worker`, `caddy`) |
| Follow one request | `make prod-logs SERVICE= \| grep <X-Request-ID from the response>` |
| Transcode logs | `make prod-logs SERVICE=worker` |

**Updating:**

```bash
cd /opt/tuition
git pull
make prod-build
make prod-up       # runs new migrations before restarting the API
make smoke URL=https://api.yourdomain.com
```

**Rolling back:** if the bad release added a migration, undo it first, while that release is
still checked out: `docker compose --env-file .env.prod -f compose.prod.yml run --rm api alembic downgrade -1`.
Then `git checkout <previous commit>`, `make prod-build`, `make prod-up`.

## Rotating secrets

Edit the value in `.env.prod`, then restart what reads it:

| Secret | Also do | Effect |
| --- | --- | --- |
| `JWT_SECRET` | `make prod-up` | Everyone's access tokens stop working; refresh tokens keep working |
| `MEDIA_SIGNING_SECRET` | `make prod-up` | Every signed video URL stops working; apps fetch a new one |
| `POSTGRES_PASSWORD` | `$PROD exec postgres psql -U tuition -c "alter user tuition password '<new>'"` first, then update both it and `DATABASE_URL` | — |
| `REDIS_PASSWORD` | update it and `REDIS_URL`, then `make prod-up` | Queued jobs survive (AOF) |
| `MINIO_SECRET_KEY` | `make prod-up` (MinIO takes the new root password) | Unexpired presigned uploads fail |

(`PROD` = `docker compose --env-file .env.prod -f compose.prod.yml`.)

## Restore drill (monthly) and real restores

```bash
sudo scripts/restore.sh /var/backups/tuition/db/<newest>.dump
```

It restores into a new database, `tuition_restore`, never the live one, and prints each table's
row count next to the live count. Drop it afterwards:
`$PROD exec postgres dropdb -U tuition tuition_restore`.

To really switch production to a restored database:

```bash
$PROD stop api worker
$PROD exec postgres psql -U tuition -d postgres -c "alter database tuition rename to tuition_broken"
$PROD exec postgres psql -U tuition -d postgres -c "alter database tuition_restore rename to tuition"
make prod-up
```

To put files back into MinIO from the mirror (copies only, never deletes):
`sudo scripts/restore.sh <dump> --bucket-from /var/backups/tuition`.
