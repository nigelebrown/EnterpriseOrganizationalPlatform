#!/usr/bin/env python3
"""Bootstrap PMS/HRIS source settings WITHOUT committing SQL credentials to Git.

The non-secret defaults are from the supplied AD Self-Service API settings:
server 172.17.51.49, database HRIS_Duplicate, user HRISUser, port 1433.
Read password from the original AD Reset archive/config or standard input and
store it in root-only /etc/jcf-registry/pms-source.env for the existing importer.

Examples:
  sudo python3 scripts/configure_pms_source.py --reuse-existing
  sudo python3 scripts/configure_pms_source.py --from-zip /root/JcfAdReset-COMPLETE-2026-10-17.zip
  sudo python3 scripts/configure_pms_source.py --from-json /opt/JcfAdReset/Api/appsettings.json
  sudo python3 scripts/configure_pms_source.py --password-stdin <<'SECRET'
  <password>
  SECRET
No password is printed, embedded in source, or written to Git.
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from zipfile import ZipFile, BadZipFile

DEFAULT = {
    "Server": "172.17.51.49",
    "Port": 1433,
    "Database": "HRIS_Duplicate",
    "User": "HRISUser",
}
OUTPUT = Path("/etc/jcf-registry/pms-source.env")


def from_zip(path: Path) -> dict:
    with ZipFile(path) as archive:
        matches = [name for name in archive.namelist()
                   if name.replace("\\", "/").lower().endswith("/api/appsettings.json")]
        if len(matches) != 1:
            raise ValueError("Expected exactly one Api/appsettings.json in AD Reset archive")
        with archive.open(matches[0]) as stream:
            return json.load(stream).get("Hris", {})


def from_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")).get("Hris", {})


def from_existing(path: Path) -> dict:
    """Reuse a previously saved password; override nonsecret settings with
    the user's explicitly supplied HRIS_Duplicate connection string.
    No password is accepted or printed on the command line.
    """
    if not path.is_file() or path.is_symlink():
        raise ValueError("Existing protected PMS source config not found")
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value
    previous_password = values.get("PMS_SOURCE_PASSWORD", "")
    if not previous_password:
        raise ValueError("Existing PMS source password not found")
    return {**DEFAULT, "Password": previous_password}


def validate(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or any(ord(ch) < 32 for ch in value):
        raise ValueError(f"Invalid {label} value")
    if "=" in value and label != "Password":
        raise ValueError(f"Invalid {label} value")
    return value


def prepare(config: dict, trust_cert: bool = False) -> dict[str, str]:
    server = validate(str(config.get("Server", "")), "Server")
    database = validate(str(config.get("Database", "")), "Database")
    user = validate(str(config.get("User", "")), "User")
    password = validate(config.get("Password", ""), "Password")
    port = int(config.get("Port", 1433))
    if not 1 <= port <= 65535:
        raise ValueError("Invalid SQL port")
    if server.casefold() == "sqlserver" or database.casefold() == "jcfenterpriseregistry":
        raise ValueError("PMS source must be separate from JCF-EMP destination database")
    if any(ch in server + database + user for ch in ";"):
        raise ValueError("Unexpected delimiter in PMS connection settings")
    return {
        "PMS_SOURCE_HOST": f"{server},{port}",
        "PMS_SOURCE_DB": database,
        "PMS_SOURCE_USER": user,
        "PMS_SOURCE_PASSWORD": password,
        "PMS_SOURCE_TRUST_CERT": str(trust_cert).lower(),
    }


def write_secret(output: Path, values: dict[str, str]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.is_symlink():
        raise ValueError("Refusing to overwrite symlink")
    if output.exists() and not output.is_file():
        raise ValueError("Refusing non-file output")
    # Never write secret contents into a shell command, process argument,
    # GitHub file, container image, Docker command line, or ordinary logs.
    fd, path = tempfile.mkstemp(prefix=".pms-source.", dir=str(output.parent))
    try:
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            for key, value in values.items():
                stream.write(f"{key}={value}\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(path, output)
        os.chmod(output, 0o600)
    finally:
        if os.path.exists(path):
            os.unlink(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Save protected PMS source credentials from existing AD Reset config")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-zip", type=Path, help="Original AD Reset ZIP archive containing Api/appsettings.json")
    src.add_argument("--from-json", type=Path, help="Existing AD Reset Api/appsettings.json file")
    src.add_argument("--reuse-existing", action="store_true",
                     help="Reuse existing root-only SQL password; apply HRIS_Duplicate connection settings")
    src.add_argument("--password-stdin", action="store_true",
                     help="Use the known AD Reset host/database/user defaults; receive password from stdin")
    parser.add_argument("--trust-source-cert", action="store_true",
                        help="TEST only: bypass SQL Server source certificate validation")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        parser.error("Run using sudo/root to store credentials in a root-only file")
    if args.reuse_existing:
        source = from_existing(args.output)
    elif args.password_stdin:
        if sys.stdin.isatty():
            parser.error("Provide the password through a pipe or heredoc, not an interactive terminal")
        password = sys.stdin.read().strip("\r\n")
        source = {**DEFAULT, "Password": password}
    else:
        try:
            source = from_zip(args.from_zip) if args.from_zip else from_json(args.from_json)
        except (OSError, KeyError, ValueError, BadZipFile, json.JSONDecodeError) as err:
            parser.error(f"Unable to read AD Reset application settings: {err}")
    values = prepare(source, args.trust_source_cert or args.reuse_existing)
    write_secret(args.output, values)
    print("PMS/HRIS source settings saved, mode: SELECT-only importer")
    print(f"Server: {source.get('Server')}, database: {source.get('Database')}, user: {source.get('User')}")
    print(f"Protected file: {args.output} (owner only; password not displayed)")
    if args.reuse_existing:
        print("SQL TLS: Encrypt=True, TrustServerCertificate=True (development source only)")
    print("This config does not validate SQL connectivity or credentials until a preview is run.")


if __name__ == "__main__":
    main()
