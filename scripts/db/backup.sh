#!/usr/bin/env bash
# Back up a database and prove the backup restores.
#   1. pg_dump URL -> $OUT/backup.dump
#   2. restore it into SCRATCH (a throwaway local Postgres) and
#      fingerprint both; PASS only if the restored copy matches the
#      source fingerprint taken right after the dump
# URL is only read.
# Usage: scripts/db/backup.sh URL SCRATCH_URL OUT_DIR
set -euo pipefail
src="${1:?database url}"; scratch="${2:?scratch url}"; out="${3:?output dir}"
here="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$out"
pg_dump "$src" --format=custom --no-owner --no-privileges --table='public.*' \
  --file="$out/backup.dump"
"$here/fingerprint.sh" "$src" > "$out/source.fingerprint"
pg_restore --no-owner --no-privileges --exit-on-error -d "$scratch" \
  "$out/backup.dump"
"$here/fingerprint.sh" "$scratch" > "$out/restored.fingerprint"
cat "$out/restored.fingerprint"
if diff -u "$out/source.fingerprint" "$out/restored.fingerprint"; then
  echo "PASS: backup restores to an exact copy."
else
  echo "WARN: restored copy differs from the source fingerprint."
  echo "      (Usually a write landed between dump and fingerprint; rerun.)"
  exit 1
fi
