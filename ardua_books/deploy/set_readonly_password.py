#!/usr/bin/env python3
"""
Set, or rotate, the password of the books_readonly Postgres role.

Run from homer (or any machine that can SSH to books as jramsey):

    python3 ardua_books/deploy/set_readonly_password.py

What it does, in order:

1. Generates a random password locally.
2. Computes the SCRAM-SHA-256 verifier for it locally -- the same string
   Postgres would store -- and sends ONLY the verifier to the server, over SSH
   stdin, as `ALTER ROLE ... PASSWORD '<verifier>'`. The plaintext password
   never leaves this machine: not over the wire, not into Postgres' logs, not
   into any shell history.
3. Only once the server has accepted it, writes the password to ~/.pgpass
   (mode 0600) and a connection profile to ~/.pg_service.conf, replacing any
   previous entry for this role.

The password is never printed. Running it again rotates the password.

The role itself is created and kept in shape by deploy/readonly_role.sql,
which every deploy re-applies; this script only sets the password.
Standard library only, so it runs from any Python 3 without the project venv.
"""

import argparse
import base64
import hashlib
import hmac
import os
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path

ROLE = "books_readonly"
DATABASE = "ardua_books"
SSH_TARGET = "jramsey@books.ardua.lan"
REMOTE_DIR = "/opt/ardua_books"

# Local end of the SSH tunnel (ssh -L 15432:127.0.0.1:5432 ...). Not 5432, so
# it cannot collide with a Postgres running on this machine.
TUNNEL_HOST = "127.0.0.1"
TUNNEL_PORT = 15432
SERVICE_NAME = "ardua-books-ro"

# Postgres' own default (scram_iterations, PG16+; fixed at 4096 in PG15).
SCRAM_ITERATIONS = 4096
SALT_BYTES = 16


def scram_sha256_verifier(password, salt, iterations=SCRAM_ITERATIONS):
    """
    Return the SCRAM-SHA-256 verifier Postgres stores in pg_authid.rolpassword.

    Format: SCRAM-SHA-256$<iterations>:<salt>$<StoredKey>:<ServerKey>
    (RFC 5802 / RFC 7677). Postgres recognises a string in this format passed
    to ALTER ROLE ... PASSWORD and stores it as-is instead of hashing it again.

    Postgres applies SASLprep to the password first. That is the identity
    function for the ASCII passwords generate_password() produces, so it is not
    reimplemented here; do not feed this non-ASCII passwords.
    """
    if not password.isascii():
        raise ValueError("Only ASCII passwords are supported (SASLprep is not implemented).")
    salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()

    def b64(raw):
        return base64.b64encode(raw).decode("ascii")

    return f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


def generate_password():
    """43 URL-safe characters (256 bits). Contains no ':' or '\\', so it needs
    no escaping in .pgpass, and no quote, so it is safe inside SQL literals."""
    return secrets.token_urlsafe(32)


def _pgpass_escape(value):
    # .pgpass treats ':' as a field separator and '\' as the escape character.
    return value.replace("\\", "\\\\").replace(":", "\\:")


def _write_private(path, text):
    """Atomically replace `path` with `text`, mode 0600 from the moment it exists."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def update_pgpass(path, host, port, database, user, password):
    """
    Set the .pgpass entry for host:port:database:user, keeping every other line.

    Any existing line for the same host/port/database/user is replaced, so a
    rotation never leaves a stale password ahead of the new one (libpq uses the
    first matching line).
    """
    path = Path(path)
    prefix = f"{host}:{port}:{database}:{user}:"
    existing = path.read_text().splitlines() if path.exists() else []
    kept = [line for line in existing if not line.startswith(prefix)]
    kept.append(prefix + _pgpass_escape(password))
    _write_private(path, "\n".join(kept) + "\n")


def update_service_file(path, name, settings):
    """
    Set the [name] section of a pg_service.conf, keeping every other section.

    Edited line-by-line rather than with configparser, which would drop
    comments and reformat the rest of the user's file.
    """
    path = Path(path)
    lines = path.read_text().splitlines() if path.exists() else []
    header = f"[{name}]"

    kept, skipping = [], False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            skipping = stripped == header
        if not skipping:
            kept.append(line)

    while kept and not kept[-1].strip():
        kept.pop()
    if kept:
        kept.append("")
    kept.append(header)
    kept.extend(f"{key}={value}" for key, value in settings.items())
    _write_private(path, "\n".join(kept) + "\n")


def set_server_password(ssh_target, verifier):
    """Send `ALTER ROLE` with the verifier over SSH stdin. Raises on failure."""
    sql = f"ALTER ROLE {ROLE} PASSWORD '{verifier}';\n"
    # Single quotes keep $POSTGRES_* literal on books; the db container's own
    # sh expands them from its env_file.
    remote = (
        f"cd {REMOTE_DIR} && docker compose exec -T db "
        "sh -c 'psql -X -q -v ON_ERROR_STOP=1 -U \"$POSTGRES_USER\" -d \"$POSTGRES_DB\"'"
    )
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", ssh_target, remote],
        input=sql, text=True, capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Setting the password on the server failed (exit {result.returncode}).\n"
            f"{result.stderr.strip()}\n"
            f"If the role does not exist yet, deploy first: every deploy applies "
            f"deploy/readonly_role.sql, which creates it."
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip())
    parser.add_argument("--ssh-target", default=SSH_TARGET)
    parser.add_argument("--pgpass", type=Path, default=Path.home() / ".pgpass")
    parser.add_argument("--service-file", type=Path, default=Path.home() / ".pg_service.conf")
    args = parser.parse_args(argv)

    password = generate_password()
    verifier = scram_sha256_verifier(password, secrets.token_bytes(SALT_BYTES))

    try:
        set_server_password(args.ssh_target, verifier)
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1

    update_pgpass(args.pgpass, TUNNEL_HOST, TUNNEL_PORT, DATABASE, ROLE, password)
    update_service_file(
        args.service_file,
        SERVICE_NAME,
        {
            "host": TUNNEL_HOST,
            "port": TUNNEL_PORT,
            "dbname": DATABASE,
            "user": ROLE,
            # The SSH tunnel provides the encryption; Postgres in the container
            # has no TLS configured.
            "sslmode": "disable",
        },
    )

    print(f"Password for {ROLE} set on the server.")
    print(f"  stored in {args.pgpass} (mode 0600) -- not displayed")
    print(f"  profile [{SERVICE_NAME}] written to {args.service_file}")
    print(f"Connect: ssh -fN -L {TUNNEL_PORT}:127.0.0.1:5432 {args.ssh_target}")
    print(f"         psql service={SERVICE_NAME}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
