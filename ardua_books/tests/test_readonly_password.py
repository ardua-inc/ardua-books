"""
Tests for deploy/set_readonly_password.py -- the tool that sets the
books_readonly Postgres password from homer.

The tool runs outside Django and is excluded from the Docker image, so it is
loaded here by path rather than imported as a package.
"""

import base64
import hashlib
import hmac
import importlib.util
import stat
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "set_readonly_password.py"
_spec = importlib.util.spec_from_file_location("set_readonly_password", _SCRIPT)
tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool)


# --- SCRAM verifier ---------------------------------------------------------

def _parse_verifier(verifier):
    mechanism, rest = verifier.split("$", 1)
    iter_salt, keys = rest.split("$")
    iterations, salt = iter_salt.split(":")
    stored_key, server_key = keys.split(":")
    return (
        mechanism,
        int(iterations),
        base64.b64decode(salt),
        base64.b64decode(stored_key),
        base64.b64decode(server_key),
    )


def test_verifier_has_postgres_format():
    salt = bytes(range(16))
    mechanism, iterations, parsed_salt, stored_key, server_key = _parse_verifier(
        tool.scram_sha256_verifier("anything", salt)
    )

    assert mechanism == "SCRAM-SHA-256"
    assert iterations == 4096
    assert parsed_salt == salt
    assert len(stored_key) == len(server_key) == 32  # SHA-256 digests


def test_verifier_completes_the_rfc7677_example_exchange():
    """
    Checks the verifier against published values, not against itself.

    RFC 7677 section 3 gives a full SCRAM-SHA-256 exchange for the password
    "pencil". A server holding only our StoredKey/ServerKey must be able to
    (a) accept the client's published ClientProof and (b) produce the
    published ServerSignature. Both only hold if the keys are exactly right.
    """
    salt = base64.b64decode("W22ZaJ0SNY7soEsUEjb6gQ==")
    _, _, _, stored_key, server_key = _parse_verifier(
        tool.scram_sha256_verifier("pencil", salt, iterations=4096)
    )

    nonce = "rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0"
    auth_message = ",".join([
        "n=user,r=rOprNGfwEbeRWgbNEkqO",                      # client-first-bare
        f"r={nonce},s=W22ZaJ0SNY7soEsUEjb6gQ==,i=4096",        # server-first
        f"c=biws,r={nonce}",                                   # client-final w/o proof
    ]).encode()

    # (a) Server-side proof check: ClientKey = ClientProof XOR ClientSignature,
    #     and H(ClientKey) must equal StoredKey.
    client_proof = base64.b64decode("dHzbZapWIk4jUhN+Ute9ytag9zjfMHgsqmmiz7AndVQ=")
    client_signature = hmac.new(stored_key, auth_message, hashlib.sha256).digest()
    client_key = bytes(a ^ b for a, b in zip(client_proof, client_signature))
    assert hashlib.sha256(client_key).digest() == stored_key

    # (b) ServerSignature = HMAC(ServerKey, AuthMessage)
    server_signature = hmac.new(server_key, auth_message, hashlib.sha256).digest()
    assert base64.b64encode(server_signature).decode() == (
        "6rriTRBi23WpRR/wtup+mMhUZUn/dB5nLTJRsjl95G4="
    )


def test_verifier_rejects_non_ascii_passwords():
    """SASLprep is not implemented, so a non-ASCII password would silently
    produce a verifier Postgres disagrees with. Refuse rather than mislead."""
    with pytest.raises(ValueError):
        tool.scram_sha256_verifier("pässword", b"0" * 16)


def test_generated_password_needs_no_escaping():
    for _ in range(200):
        password = tool.generate_password()
        assert len(password) >= 43
        assert password.isascii()
        assert not set(password) & set(":\\'\"")


# --- ~/.pgpass ----------------------------------------------------------------

def test_pgpass_is_created_private(tmp_path):
    pgpass = tmp_path / ".pgpass"

    tool.update_pgpass(pgpass, "127.0.0.1", 15432, "ardua_books", "books_readonly", "s3cret")

    assert pgpass.read_text() == "127.0.0.1:15432:ardua_books:books_readonly:s3cret\n"
    assert stat.S_IMODE(pgpass.stat().st_mode) == 0o600  # libpq ignores it otherwise


def test_pgpass_rotation_replaces_entry_and_keeps_others(tmp_path):
    pgpass = tmp_path / ".pgpass"
    pgpass.write_text(
        "# my comment\n"
        "otherhost:5432:otherdb:someone:keepme\n"
        "127.0.0.1:15432:ardua_books:books_readonly:OLD\n"
        "127.0.0.1:15432:ardua_books:another_user:keepme-too\n"
    )

    tool.update_pgpass(pgpass, "127.0.0.1", 15432, "ardua_books", "books_readonly", "NEW")

    lines = pgpass.read_text().splitlines()
    assert "# my comment" in lines
    assert "otherhost:5432:otherdb:someone:keepme" in lines
    assert "127.0.0.1:15432:ardua_books:another_user:keepme-too" in lines
    ours = [line for line in lines if line.startswith("127.0.0.1:15432:ardua_books:books_readonly:")]
    assert ours == ["127.0.0.1:15432:ardua_books:books_readonly:NEW"]
    assert stat.S_IMODE(pgpass.stat().st_mode) == 0o600


def test_pgpass_escapes_separator_and_escape_characters(tmp_path):
    pgpass = tmp_path / ".pgpass"

    tool.update_pgpass(pgpass, "h", 1, "d", "u", r"a:b\c")

    assert pgpass.read_text() == "h:1:d:u:a\\:b\\\\c\n"


# --- ~/.pg_service.conf -------------------------------------------------------

SETTINGS = {"host": "127.0.0.1", "port": 15432, "dbname": "ardua_books", "user": "books_readonly"}


def test_service_file_is_created(tmp_path):
    service = tmp_path / ".pg_service.conf"

    tool.update_service_file(service, "ardua-books-ro", SETTINGS)

    assert service.read_text() == (
        "[ardua-books-ro]\n"
        "host=127.0.0.1\n"
        "port=15432\n"
        "dbname=ardua_books\n"
        "user=books_readonly\n"
    )


def test_service_file_replaces_only_its_own_section(tmp_path):
    service = tmp_path / ".pg_service.conf"
    service.write_text(
        "# personal comment\n"
        "[before]\n"
        "host=a\n"
        "\n"
        "[ardua-books-ro]\n"
        "host=stale\n"
        "port=1\n"
        "\n"
        "[after]\n"
        "host=b\n"
    )

    tool.update_service_file(service, "ardua-books-ro", SETTINGS)
    text = service.read_text()

    assert "# personal comment" in text
    assert "[before]\nhost=a\n" in text
    assert "[after]\nhost=b\n" in text
    assert "stale" not in text
    assert text.count("[ardua-books-ro]") == 1
    assert text.endswith("[ardua-books-ro]\nhost=127.0.0.1\nport=15432\ndbname=ardua_books\nuser=books_readonly\n")


def test_service_file_rewrite_is_stable(tmp_path):
    """Running the tool twice must not accumulate blank lines or sections."""
    service = tmp_path / ".pg_service.conf"
    service.write_text("[other]\nhost=x\n")

    tool.update_service_file(service, "ardua-books-ro", SETTINGS)
    first = service.read_text()
    tool.update_service_file(service, "ardua-books-ro", SETTINGS)

    assert service.read_text() == first
