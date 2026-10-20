#!/usr/bin/env bash
# Measure warm page latency of a running console (target T6 in docs/console-PLAN.md).
#
#   console/scripts/measure-latency.sh [base-url]     # default: the Azure deployment
#
# Prompts for a console account (any role can read the pages measured) and signs in once.
# The password is read silently, used for that one request and never stored; the session
# cookie lives in a temporary directory that is deleted on exit.
#
# Per page: 5 warm-up requests, then 50 timed ones. Two numbers are reported:
#   TTFB         time to first byte on a fresh connection (DNS + TCP + TLS + server)
#   server+RTT   TTFB minus connection setup: server time plus one network round trip
set -euo pipefail

BASE="${1:-https://siteops-console-coxh2t.azurewebsites.net}"
N=50
workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT
jar="$workdir/cookies.txt"

read -rp "Console email: " email
read -rsp "Password (not shown): " password; echo
body="$(python3 -c 'import json,sys; print(json.dumps({"email": sys.argv[1], "password": sys.argv[2]}))' "$email" "$password" )"
unset password
status="$(curl -s -c "$jar" -o /dev/null -w "%{http_code}" -H "Content-Type: application/json" -H "Origin: $BASE" \
  --data-binary @- "$BASE/api/auth/sign-in/email" <<<"$body" || true)"
unset body
[ "$status" = "200" ] || { echo "Sign-in failed (HTTP $status)."; exit 1; }

site="$(curl -s -b "$jar" "$BASE/sites?q=CATHROCK" | grep -o '/sites/c[a-z0-9]*' | head -1 || true)"
[ -n "$site" ] || { echo "Could not find the CATHROCK site page."; exit 1; }

# p50 / p95 of a column of milliseconds (portable: sort first, then index).
percentiles() { sort -n | awk '{ v[NR] = $1 } END { printf "%8.0f ms %8.0f ms", v[int(NR * 0.5)], v[int(NR * 0.95)] }'; }

# curl -s is silent on network errors, and set -e would then exit without a word.
failed() { echo "A request to $page failed (curl exit $1). Run the script again." >&2; exit 1; }

printf "%-22s %4s %22s %26s\n" page n "TTFB p50 / p95" "server+RTT p50 / p95"
for page in "$site" "/" "/status"; do
  for _ in 1 2 3 4 5; do curl -s -b "$jar" -o /dev/null "$BASE$page" || failed $?; done
  for _ in $(seq 1 "$N"); do
    curl -s -b "$jar" -o /dev/null -w "%{time_starttransfer} %{time_pretransfer} %{http_code}\n" "$BASE$page" || failed $?
  done > "$workdir/times.txt"
  # A redirect to /login would be fast and meaningless: only count real page renders.
  if awk '$3 != 200 { bad = 1 } END { exit !bad }' "$workdir/times.txt"; then
    echo "$page returned a non-200 status; is the session valid?"; exit 1
  fi
  ttfb="$(awk '{ print $1 * 1000 }' "$workdir/times.txt" | percentiles)"
  server="$(awk '{ print ($1 - $2) * 1000 }' "$workdir/times.txt" | percentiles)"
  printf "%-22s %4d %s     %s\n" "${page:0:22}" "$N" "$ttfb" "$server"
done
