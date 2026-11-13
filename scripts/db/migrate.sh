#!/usr/bin/env bash
# Copy every table from SOURCE to TARGET and prove nothing was lost.
#
#   1. pg_dump SOURCE (one consistent snapshot) -> $OUT/source.dump
#   2. restore that dump into SCRATCH (a throwaway local Postgres) and
#      fingerprint it: this is exactly what the dump holds
#   3. refuse if TARGET already has any of the dumped tables
#   4. pg_restore into TARGET, fingerprint TARGET
#   5. PASS only if TARGET == dump, line for line (row counts and checksums)
#
# SOURCE is only read. Nothing is deleted anywhere.
# Usage: scripts/db/migrate.sh SOURCE_URL TARGET_URL SCRATCH_URL OUT_DIR
set -euo pipefail
src="${1:?source url}"; dst="${2:?target url}"; scratch="${3:?scratch url}"
out="${4:?output dir}"
here="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$out"

echo "== 1. Dump source"
pg_dump "$src" --format=custom --no-owner --no-privileges --table='public.*' \
  --file="$out/source.dump"
pg_restore --list "$out/source.dump" > "$out/source.toc"
echo "dump size: $(wc -c < "$out/source.dump") bytes"

echo "== 2. Fingerprint the dump (restored into scratch)"
pg_restore --no-owner --no-privileges --exit-on-error -d "$scratch" \
  "$out/source.dump"
"$here/fingerprint.sh" "$scratch" > "$out/dump.fingerprint"
cat "$out/dump.fingerprint"

echo "== 3. Check target is empty"
dumped=$(awk '{print $1}' "$out/dump.fingerprint")
existing=$(psql "$dst" -XAtq -v ON_ERROR_STOP=1 -c \
  "select tablename from pg_tables where schemaname = 'public'")
clash=$(comm -12 <(echo "$dumped" | sort) <(echo "$existing" | sort) || true)
if [ -n "$clash" ]; then
  echo "REFUSED: target already has these tables: $clash"
  echo "Point nothing at the target before migrating, or empty it by hand."
  exit 2
fi

echo "== 4. Restore into target"
pg_restore --no-owner --no-privileges --exit-on-error -d "$dst" \
  "$out/source.dump"
"$here/fingerprint.sh" "$dst" | grep -Fwf <(echo "$dumped") \
  > "$out/target.fingerprint"
cat "$out/target.fingerprint"

echo "== 5. Verify"
if diff -u "$out/dump.fingerprint" "$out/target.fingerprint"; then
  echo "PASS: target matches the dump exactly (row counts and checksums)."
else
  echo "FAIL: target differs from the dump."
  exit 1
fi

echo "== Source drift since the dump (information only)"
"$here/fingerprint.sh" "$src" > "$out/source_now.fingerprint"
if diff -u "$out/dump.fingerprint" "$out/source_now.fingerprint"; then
  echo "Source unchanged since the dump."
else
  echo "NOTE: source changed during the run; rerun verify after cut-over."
fi
