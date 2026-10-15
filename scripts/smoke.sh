#!/usr/bin/env bash
# Post-deploy smoke test, through the public URL only:
#   health -> register + login -> enroll -> stream (master, variant, init, segment)
#   -> offline progress sync (applied, then skipped_stale on resend) -> course percentage
#   -> HSTS and the http->https redirect.
# Usage: scripts/smoke.sh https://api.yourdomain.com
# Needs a published course with a ready lesson (upload one first). Uses curl and python3.
# For Caddy's local CA (CI), point CURL_CA_BUNDLE at its root certificate.
set -euo pipefail

base=${1:?usage: scripts/smoke.sh <https://host>}
base=${base%/}
step() { printf '\n== %s\n' "$*"; }
ok() { printf '   ok: %s\n' "$*"; }
fail() { printf '   FAIL: %s\n' "$*" >&2; exit 1; }
json() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }
req() { curl -sS --fail-with-body "$@"; }

step "health"
req "$base/health" | json "d['status'], d['checks']['database']['status'], d['checks']['redis']['status'], d['checks']['minio']['status']" \
  | grep -qx "ok ok ok ok" || fail "health is not ok"
ok "database, redis and minio up"

step "register and log in"
email="smoke-$(date +%s)-$RANDOM@example.com"
password="smoke-$(openssl rand -hex 8)"
req -o /dev/null -X POST "$base/auth/register" -H 'Content-Type: application/json' \
  -d "{\"email\":\"$email\",\"password\":\"$password\",\"name\":\"Smoke test\"}"
token=$(req -X POST "$base/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"$email\",\"password\":\"$password\"}" | json "d['access_token']")
auth="Authorization: Bearer $token"
ok "$email"

step "find a ready lesson and enroll"
found=$(req "$base/courses?page_size=100" | json "' '.join(c['id'] for c in d['items'])")
course='' lesson='' duration=''
for c in $found; do
  pick=$(req "$base/courses/$c" | json "next((l['id'] + ' ' + str(l['duration_seconds']) for l in d['lessons'] if l['status'] == 'ready'), '')")
  if [ -n "$pick" ]; then course=$c; read -r lesson duration <<< "$pick"; break; fi
done
[ -n "$lesson" ] || fail "no published course has a ready lesson; upload a video first"
req -o /dev/null -X POST -H "$auth" "$base/courses/$course/enroll"
ok "course $course, lesson $lesson (${duration}s)"

step "stream"
url=$(req -H "$auth" "$base/lessons/$lesson/stream" | json "d['url']")
case $url in https://*) ;; *) fail "stream URL is not https: $url" ;; esac
dir=${url%master.m3u8}
variant=$(req "$url" | grep -v '^#' | head -1)
playlist=$(req "$dir$variant")
vdir=$dir$(dirname "$variant")/
init=$(grep -o 'URI="[^"]*"' <<< "$playlist" | head -1 | cut -d'"' -f2)
segment=$(grep -v '^#' <<< "$playlist" | head -1)
for part in "$vdir$init" "$vdir$segment"; do
  code=$(curl -sS -o /dev/null -w '%{http_code}' "$part")
  [ "$code" = 200 ] || fail "$part -> $code"
done
ok "master, $variant, $init and $segment all 200"
code=$(curl -sS -o /dev/null -w '%{http_code}' "${url/\/v\//\/v\/1}")
[ "$code" = 403 ] || fail "a tampered media URL gave $code, expected 403"
ok "tampered URL refused (403)"

step "offline, then sync"
batch="{\"items\":[{\"lesson_id\":\"$lesson\",\"position_seconds\":$duration,\"client_updated_at\":\"$(date -u -d '-10 minutes' +%Y-%m-%dT%H:%M:%SZ)\"}]}"
first=$(req -X PUT -H "$auth" -H 'Content-Type: application/json' "$base/me/progress" -d "$batch" | json "d['results'][0]['status']")
again=$(req -X PUT -H "$auth" -H 'Content-Type: application/json' "$base/me/progress" -d "$batch" | json "d['results'][0]['status']")
[ "$first $again" = "applied skipped_stale" ] || fail "sync gave '$first' then '$again'"
percent=$(req -H "$auth" "$base/me/courses" | json "[c['percent_complete'] for c in d['items'] if c['id'] == '$course'][0]")
[ "$percent" -gt 0 ] || fail "course still at 0% after finishing a lesson"
ok "applied, then skipped_stale on resend; course at $percent%"

step "TLS"
curl -sS -I "$base/health" | grep -qi '^strict-transport-security: max-age=' || fail "no HSTS header"
host=${base#https://}
code=$(curl -sS -o /dev/null -w '%{http_code}' "http://$host/health")
case $code in 301|302|307|308) ;; *) fail "http:// gave $code, expected a redirect" ;; esac
ok "HSTS present, http redirects ($code)"

echo
echo "Smoke test passed. Push notifications are skipped until Sprint 7."
