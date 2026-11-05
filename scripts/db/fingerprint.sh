#!/usr/bin/env bash
# Print one line per table in the public schema: "<table> <row count> <md5>".
# The md5 covers every row's full text, sorted, so two databases with the
# same lines hold the same data. Reads only; never writes.
# Usage: scripts/db/fingerprint.sh "$DATABASE_URL"
set -euo pipefail
url="${1:?usage: fingerprint.sh DATABASE_URL}"
tables=$(psql "$url" -XAtq -v ON_ERROR_STOP=1 -c \
  "select tablename from pg_tables where schemaname = 'public' order by tablename")
if [ -z "$tables" ]; then
  echo "(no tables)"
  exit 0
fi
for t in $tables; do
  psql "$url" -XAtq -v ON_ERROR_STOP=1 -F ' ' -c \
    "select '$t', count(*), md5(coalesce(string_agg(r::text, E'\n' order by r::text), ''))
     from public.\"$t\" r"
done
