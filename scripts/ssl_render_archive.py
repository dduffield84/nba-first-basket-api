"""Manual SSL archive helper. Never imports or starts application code.

Export: read-only Render snapshot -> encrypted artifact.
Verify: downloaded encrypted artifact -> empty LOCAL scratch database only.
All private manifests stay inside encryption. Never print database exceptions.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import time

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

EXPECTED_DB = "nba_first_basket_db"
SCRATCH_DB = "ssl_render_restore"
APPROVAL = "EXPORT-OLD-RENDER-READ-ONLY"
MEMBERS = {"database.dump", "source.json"}


class SafetyError(Exception):
    """Only deliberately non-sensitive messages may use this exception."""


def passphrase():
    value = os.environ.get("SSL_RENDER_ARCHIVE_PASSPHRASE", "")
    if len(value) < 32 or "\n" in value or "\r" in value:
        raise SafetyError("Use a separately saved passphrase of 32+ characters, without newlines.")
    return value


def source_options():
    if os.environ.get("SSL_RENDER_APPROVAL") != APPROVAL:
        raise SafetyError("Explicit read-only export confirmation is missing.")
    raw = os.environ.get("SSL_RENDER_ARCHIVE_SOURCE", "")
    try:
        parsed = conninfo_to_dict(raw) if raw else {}
    except Exception:
        raise SafetyError("Source secret is not a valid PostgreSQL connection string.") from None
    # Do not accept a service file, alternate hosts, libpq options, or URL overrides.
    if set(parsed) - {"host", "port", "dbname", "user", "password", "sslmode"}:
        raise SafetyError("Source contains unsupported connection options; review securely.")
    host = parsed.get("host", "")
    if not host.endswith(".render.com") or any(c in host for c in "/, \n\r"):
        raise SafetyError("Source must be one approved external Render hostname.")
    if parsed.get("dbname") != EXPECTED_DB or parsed.get("port", "5432") != "5432":
        raise SafetyError("Source database name or port does not match the approved old database.")
    if not parsed.get("user") or not parsed.get("password"):
        raise SafetyError("Source user or password is missing.")
    return {
        **{k: v for k, v in parsed.items() if k != "sslmode"},
        "sslmode": "verify-full",
        "sslrootcert": os.environ.get("SSL_RENDER_CA_FILE", "/etc/ssl/certs/ca-certificates.crt"),
        "connect_timeout": "5",
        "application_name": "ssl_approved_readonly_archive",
        "options": "-c default_transaction_read_only=on -c lock_timeout=10000 "
                   "-c statement_timeout=600000 -c idle_in_transaction_session_timeout=1800000 "
                   "-c row_security=off -c timezone=UTC -c datestyle=ISO,YMD "
                   "-c intervalstyle=postgres -c extra_float_digits=3 -c bytea_output=hex",
    }


def pg_environment(options):
    # Do not inherit DATABASE_URL, service files, or other source/scratch PG settings.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("PG") and k not in {
               "DATABASE_URL", "SSL_RENDER_ARCHIVE_SOURCE", "SSL_RENDER_ARCHIVE_PASSPHRASE"}}
    mapping = {"host": "PGHOST", "port": "PGPORT", "dbname": "PGDATABASE",
               "user": "PGUSER", "password": "PGPASSWORD", "sslmode": "PGSSLMODE",
               "sslrootcert": "PGSSLROOTCERT", "options": "PGOPTIONS",
               "connect_timeout": "PGCONNECT_TIMEOUT", "application_name": "PGAPPNAME"}
    for key, value in options.items():
        env[mapping[key]] = str(value)
    return env


def run_private(command, *, env=None, data=None, timeout=1200):
    # Capture output; upstream errors may include hosts, usernames, or row content.
    result = subprocess.run(command, env=env, input=data, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=timeout)
    if result.returncode:
        raise SafetyError("A database or encryption command failed; do not publish its raw output.")
    return result.stdout


def digest_file(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def table_manifest(conn):
    """Fingerprints use the caller's snapshot, not a later source transaction."""
    foreign = conn.execute("""
        SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE c.relkind='f' AND n.nspname NOT LIKE 'pg_%'
          AND n.nspname <> 'information_schema'
    """).fetchone()[0]
    if foreign:
        raise SafetyError("Foreign tables require a separate preservation plan.")
    tables = conn.execute("""
        SELECT n.nspname,c.relname,c.relkind,c.relispopulated
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE c.relkind IN ('r','p','m') AND n.nspname NOT LIKE 'pg_%'
          AND n.nspname <> 'information_schema' ORDER BY n.nspname,c.relname
    """).fetchall()
    if not tables:
        raise SafetyError("No application tables were visible; database identity or access needs review.")
    result = []
    for schema, name, kind, populated in tables:
        entry = {"schema": schema, "table": name, "kind": kind, "populated": populated}
        if populated:
            relation = sql.Identifier(schema, name)
            entry["rows"] = conn.execute(
                sql.SQL("SELECT count(*) FROM {}").format(relation)).fetchone()[0]
            h = hashlib.sha256()
            query = sql.SQL(
                'COPY (SELECT row_to_json(t)::text FROM {} AS t '
                'ORDER BY row_to_json(t)::text COLLATE "C") TO STDOUT'
            ).format(relation)
            with conn.cursor() as cursor:
                with cursor.copy(query) as copy:
                    for block in copy:
                        h.update(block)
            entry["sha256_copy"] = h.hexdigest()
        result.append(entry)
    return result


def sequence_definitions(conn):
    # last_value is nontransactional: do not falsely claim snapshot equality for it.
    return conn.execute("""
        SELECT schemaname,sequencename,start_value,min_value,max_value,increment_by,cycle,cache_size
        FROM pg_sequences WHERE schemaname NOT LIKE 'pg_%'
        ORDER BY schemaname,sequencename
    """).fetchall()


def large_object_manifest(conn):
    count = conn.execute("SELECT count(*) FROM pg_largeobject_metadata").fetchone()[0]
    if not count:
        return {"objects": 0}
    # Requires access; fail rather than silently omitting inaccessible large objects.
    h = hashlib.sha256()
    with conn.cursor() as cursor:
        with cursor.copy("COPY (SELECT loid,pageno,encode(data,'hex') "
                         "FROM pg_largeobject ORDER BY loid,pageno) TO STDOUT") as copy:
            for block in copy:
                h.update(block)
    return {"objects": count, "sha256_pages": h.hexdigest()}


def make_export(options, output, encryption_secret):
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ssl-export-") as temp:
        work = Path(temp)
        with psycopg.connect(**options, autocommit=True) as conn:
            conn.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
            identity = conn.execute(
                "SELECT current_database(),current_setting('server_version_num')::int").fetchone()
            if identity[0] != EXPECTED_DB or not 160000 <= identity[1] < 170000:
                raise SafetyError("Source is not the approved PostgreSQL 16 database.")
            snapshot = conn.execute("SELECT pg_export_snapshot()").fetchone()[0]
            print("Reading the approved snapshot; source data is not modified.", flush=True)
            run_private(["pg_dump", "--format=custom", "--no-owner", "--no-privileges",
                         "--snapshot", snapshot, "--file", str(work / "database.dump")],
                        env=pg_environment(options))
            manifest = {
                "format": 1, "database": identity[0], "server_version_num": identity[1],
                "tables": table_manifest(conn),
                "sequence_definitions": sequence_definitions(conn),
                "large_objects": large_object_manifest(conn),
                "dump_sha256": digest_file(work / "database.dump"),
                "role_inventory": conn.execute(
                    "SELECT rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin "
                    "FROM pg_roles ORDER BY rolname").fetchall(),
                "limitations": [
                    "Role passwords, original ownership and grants are not restored.",
                    "Sequence last_value is nontransactional; only definitions are compared.",
                    "Foreign services, files and scheduler configuration require separate backups.",
                    "This archive is not an application correctness or account-login test.",
                ],
            }
            # The same transaction stays open for dump AND source table evidence.
            conn.execute("ROLLBACK")
        (work / "source.json").write_text(json.dumps(manifest, default=str))
        with tarfile.open(work / "payload.tar", "w") as archive:
            for name in sorted(MEMBERS):
                archive.add(work / name, arcname=name)
        encrypted = output / "ssl-render-archive.tar.gpg"
        if encrypted.exists():
            raise SafetyError("Refusing to overwrite an existing export.")
        run_private(["gpg", "--batch", "--no-symkey-cache", "--pinentry-mode", "loopback", "--passphrase-fd", "0",
                     "--symmetric", "--cipher-algo", "AES256", "--output", str(encrypted),
                     str(work / "payload.tar")], data=(encryption_secret + "\n").encode())
        (output / "SHA256SUMS").write_text(f"{digest_file(encrypted)}  {encrypted.name}\n")
    print("Encrypted archive created. Restore verification has NOT run yet.", flush=True)


def restore_verify(download, scratch, encryption_secret):
    # No configurable remote restore destination, and no destructive cleanup options.
    if scratch.get("host") != "127.0.0.1" or scratch.get("dbname") != SCRATCH_DB:
        raise SafetyError("Restore destination must be the dedicated local scratch database.")
    encrypted = download / "ssl-render-archive.tar.gpg"
    checksum = (download / "SHA256SUMS").read_text().split()
    if len(checksum) != 2 or checksum[1] != encrypted.name or checksum[0] != digest_file(encrypted):
        raise SafetyError("Downloaded encrypted artifact checksum does not match.")
    with tempfile.TemporaryDirectory(prefix="ssl-restore-") as temp:
        work = Path(temp)
        run_private(["gpg", "--batch", "--no-symkey-cache", "--pinentry-mode", "loopback", "--passphrase-fd", "0",
                     "--output", str(work / "payload.tar"), "--decrypt", str(encrypted)],
                    data=(encryption_secret + "\n").encode())
        with tarfile.open(work / "payload.tar", "r") as archive:
            members = archive.getmembers()
            if {m.name for m in members} != MEMBERS or len(members) != len(MEMBERS):
                raise SafetyError("Archive member list is unexpected.")
            for member in members:
                if not member.isfile():
                    raise SafetyError("Archive contains an unexpected member type.")
                source = archive.extractfile(member)
                with source, open(work / member.name, "xb") as dest:
                    while block := source.read(1024 * 1024):
                        dest.write(block)
        manifest = json.loads((work / "source.json").read_text())
        if manifest.get("format") != 1 or manifest.get("database") != EXPECTED_DB:
            raise SafetyError("Archive source identity is unexpected.")
        if digest_file(work / "database.dump") != manifest["dump_sha256"]:
            raise SafetyError("Decrypted database dump checksum does not match.")
        with psycopg.connect(**scratch, autocommit=True) as conn:
            if conn.execute("SELECT current_database()").fetchone()[0] != SCRATCH_DB:
                raise SafetyError("Scratch database identity did not match.")
            objects = conn.execute("""
                SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
            """).fetchone()[0]
            if objects:
                raise SafetyError("Scratch database is not empty; refusing to restore.")
        print("Restoring the downloaded encrypted artifact into local scratch only.", flush=True)
        run_private(["pg_restore", "--exit-on-error", "--no-owner", "--no-privileges",
                     "--dbname", SCRATCH_DB, str(work / "database.dump")],
                    env=pg_environment(scratch))
        with psycopg.connect(**scratch, autocommit=True) as conn:
            conn.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
            # Set the same deterministic rendering options used on the source.
            for setting in ("SET timezone='UTC'", "SET datestyle='ISO,YMD'",
                            "SET intervalstyle='postgres'", "SET extra_float_digits=3",
                            "SET bytea_output='hex'", "SET row_security=off"):
                conn.execute(setting)
            actual = {
                "tables": table_manifest(conn),
                "sequence_definitions": sequence_definitions(conn),
                "large_objects": large_object_manifest(conn),
            }
            expected = {k: manifest[k] for k in actual}
            # JSON normalizes tuples/lists and sequence numeric values.
            if json.dumps(actual, default=str, sort_keys=True) != json.dumps(expected, sort_keys=True):
                raise SafetyError("Restored snapshot evidence differs; do not call this backup verified.")
            conn.execute("ROLLBACK")
    print("PASS: downloaded encrypted archive decrypted, restored, and matched snapshot table data, "
          "large objects, and sequence definitions. Limitations in the private manifest still apply.",
          flush=True)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["export", "verify"])
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    secret = passphrase()
    if args.mode == "export":
        options = source_options()
        print("Waiting up to 20 minutes for the approved narrow network access rule.", flush=True)
        deadline = time.monotonic() + 1200
        while True:
            try:
                with psycopg.connect(**options, autocommit=True) as conn:
                    conn.execute("SELECT 1")
                break
            except psycopg.Error:
                if time.monotonic() >= deadline:
                    raise SafetyError("Access check timed out. Recheck secrets, TLS and the exact runner IP; "
                                      "do not broaden access to all IPs.") from None
                time.sleep(10)
        make_export(options, args.directory, secret)
    else:
        restore_verify(args.directory, {
            "host": "127.0.0.1", "port": "5432", "dbname": SCRATCH_DB,
            "user": "postgres", "password": "local-disposable-restore-only",
            "sslmode": "disable", "connect_timeout": "5",
        }, secret)


if __name__ == "__main__":
    try:
        main()
    except SafetyError as error:
        print(f"STOP: {error}", file=sys.stderr)
        sys.exit(1)
    except Exception:
        print("STOP: unexpected private operation failure. Do not publish raw exceptions or credentials.",
              file=sys.stderr)
        sys.exit(1)
