#!/usr/bin/env bash
# End-to-end test of scripts/db/*.sh against three local databases.
# Usage: tests/db/run.sh BASE_URL   (a server where we may create databases;
#        BASE_URL without a database name, e.g. postgresql://u:p@host:5432)
set -uo pipefail
base="${1:?base url}"
cd "$(dirname "$0")/../.."
fail=0
# Exit 0 if two databases have identical fingerprints, 1 if they differ,
# 3 if either fingerprint could not be read (so an outage never "passes").
same() {
  a=$(scripts/db/fingerprint.sh "$1") || return 3
  b=$(scripts/db/fingerprint.sh "$2") || return 3
  [ -n "$a" ] && [ -n "$b" ] || return 3
  [ "$a" = "$b" ]
}
check() { if [ "$1" = "$2" ]; then echo "PASSED: $3"; else echo "FAILED: $3 (got $1, want $2)"; fail=1; fi; }
for d in t_src t_dst t_scratch t_scratch2 t_scratch3; do
  psql "$base/postgres" -XAtq -c "drop database if exists $d" -c "create database $d"
done
src="$base/t_src"; dst="$base/t_dst"
psql "$src" -Xq -v ON_ERROR_STOP=1 -f tests/db/seed.sql
cols=$(head -1 data/first_baskets.csv)
psql "$src" -Xq -c "\copy first_baskets($cols) from 'data/first_baskets.csv' csv header"
cols=$(head -1 data/game_starters.csv)
psql "$src" -Xq -c "\copy game_starters($cols) from 'data/game_starters.csv' csv header"
out=$(mktemp -d)

scripts/db/migrate.sh "$src" "$dst" "$base/t_scratch" "$out/m1" > "$out/m1.log"
check $? 0 "migrate copies every table"
grep -q "^PASS:" "$out/m1.log"; check $? 0 "migrate verifies row counts and checksums"
same "$src" "$dst"
check $? 0 "target fingerprint equals source"

scripts/db/migrate.sh "$src" "$dst" "$base/t_scratch2" "$out/m2" > "$out/m2.log" 2>&1
check $? 2 "migrate refuses a target that already has the tables"
same "$src" "$dst"
check $? 0 "refused run left target unchanged"

id=$(psql "$dst" -XAtq -c "insert into prediction_sets(game_id, game_date, model, created_at) values ('x','2026-11-05','m','t') returning id" | head -1)
check "$id" 41 "id sequence continues after migration"

psql "$dst" -Xq -c "update prediction_items set probability = 0.5 where set_id = 1 and player_id = 'p1'"
same "$src" "$dst"
check $? 1 "fingerprint detects a changed value"

scripts/db/backup.sh "$src" "$base/t_scratch3" "$out/b1" > "$out/b1.log"
check $? 0 "backup restores to an exact copy"
echo "$(wc -l < "$out/b1/restored.fingerprint") tables in backup"
exit $fail
