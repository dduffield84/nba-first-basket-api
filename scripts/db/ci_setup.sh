#!/usr/bin/env bash
# GitHub Actions helper: match the Postgres client and a throwaway scratch
# server to the major version of the database at $1, so pg_dump and
# pg_restore always work (pg_dump must be at least the server's version).
# Writes PATH and SCRATCH_URL to $GITHUB_ENV / $GITHUB_PATH.
set -euo pipefail
url="${1:?database url}"
num=$(psql "$url" -XAtq -c "show server_version_num")
major=$((num / 10000))
echo "Server major version: $major"
if ! [ -x "/usr/lib/postgresql/$major/bin/pg_dump" ]; then
  sudo /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y >/dev/null
  sudo apt-get install -y -qq "postgresql-client-$major" >/dev/null
fi
echo "/usr/lib/postgresql/$major/bin" >> "$GITHUB_PATH"
docker run -d --name scratch -e POSTGRES_PASSWORD=scratch -p 55432:5432 \
  "postgres:$major" >/dev/null
for _ in $(seq 60); do
  if docker exec scratch pg_isready -U postgres >/dev/null 2>&1; then break; fi
  sleep 1
done
sleep 2
echo "SCRATCH_URL=postgresql://postgres:scratch@localhost:55432/postgres" \
  >> "$GITHUB_ENV"
