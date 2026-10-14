#!/usr/bin/env bash
# Limite interne fixe : même lancé par sudo, le groupe de sauvegarde expire.
set -euo pipefail
exec /usr/bin/timeout --signal=TERM --kill-after=5 280 /usr/bin/python3 - <<'PY'
"""Sauvegarde de fichiers et SQLite ; configuration uniquement dans le fichier root."""
import contextlib
import json
import os
import shlex
import signal
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

CONFIG_PATH = Path("/etc/noc-dashboard.env")
EXCLUDED_DIRECTORIES = frozenset({"venv", ".venv", "__pycache__", ".git"})
BROAD_SOURCES = frozenset({"/", "/opt", "/var", "/var/www", "/var/lib", "/var/log", "/var/backups", "/home", "/srv", "/usr", "/etc", "/mnt", "/media", "/root", "/tmp"})
MAX_ENTRIES = 100000
MAX_DATABASES = 128


class BackupError(ValueError):
    pass


def read_configuration(path=CONFIG_PATH):
    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_mode & 0o022 or metadata.st_size > 65536:
            raise BackupError("La configuration doit être un fichier root, non modifiable par le groupe ou les autres utilisateurs.")
        result = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key not in {"NOC_BACKUP_SOURCE", "NOC_BACKUP_PATH"}:
                continue
            fields = shlex.split(value, comments=False, posix=True)
            if len(fields) > 1:
                raise BackupError("Un chemin contenant des espaces doit être entre guillemets dans la configuration.")
            result[key] = fields[0] if fields else ""
        return result
    except (OSError, UnicodeError, ValueError) as exc:
        if isinstance(exc, BackupError):
            raise
        raise BackupError("Configuration de sauvegarde absente ou invalide.") from None


def source_path(config):
    value = config.get("NOC_BACKUP_SOURCE", "")
    if not value:
        try:
            result = subprocess.run(["/usr/bin/systemctl", "show", "babamarket.service", "--property=WorkingDirectory", "--value"], capture_output=True, text=True, timeout=5, check=False)
            value = result.stdout.strip() if result.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired):
            value = ""
    if not value:
        raise BackupError("Source inconnue : renseignez NOC_BACKUP_SOURCE dans /etc/noc-dashboard.env.")
    candidate = Path(value)
    if not candidate.is_absolute():
        raise BackupError("NOC_BACKUP_SOURCE doit être un chemin absolu.")
    try:
        source = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        raise BackupError("Le dossier source de BabaMarket est indisponible.") from None
    if not source.is_dir() or source.parent == source or source.as_posix() in BROAD_SOURCES or source.parent.as_posix() == "/home":
        raise BackupError("La source doit être le dossier précis de BabaMarket, pas un dossier système global.")
    return source


def different_root_device(destination):
    # ReadWritePaths de systemd peut créer un bind mount même sans carte SD.
    return destination.stat().st_dev != Path("/").stat().st_dev


def destination_path(config, source):
    candidate = Path(config.get("NOC_BACKUP_PATH") or "/mnt/babamarket-backups")
    if not candidate.is_absolute() or candidate.is_symlink():
        raise BackupError("Le volume de sauvegarde doit être un chemin absolu sans lien symbolique.")
    try:
        destination = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        raise BackupError("Volume de sauvegarde absent ; aucune copie vers le disque racine.") from None
    if destination != candidate or not destination.is_dir() or not os.path.ismount(destination) or destination.as_posix() == "/":
        raise BackupError("NOC_BACKUP_PATH doit désigner directement un volume réellement monté.")
    if not different_root_device(destination):
        raise BackupError("Le volume de sauvegarde utilise encore le filesystem racine ; carte SD absente ou bind mount non adapté.")
    if source == destination or source in destination.parents or destination in source.parents:
        raise BackupError("Les dossiers source et destination doivent être distincts et séparés.")
    return destination


def sqlite_databases(source):
    databases = []
    entries = 0

    def visit(directory, depth):
        nonlocal entries
        if depth > 64:
            raise BackupError("Arborescence trop profonde pour une sauvegarde bornée.")
        with os.scandir(directory) as iterator:
            for entry in iterator:
                entries += 1
                if entries > MAX_ENTRIES:
                    raise BackupError("Source trop volumineuse en nombre de fichiers ; utilisez un script adapté.")
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if entry.name not in EXCLUDED_DIRECTORIES:
                        visit(Path(entry.path), depth + 1)
                elif entry.is_file(follow_symlinks=False) and not entry.name.endswith(("-wal", "-shm")):
                    # Le contenu, pas l'extension, identifie SQLite (y compris .DB/.sqlite3).
                    with open(entry.path, "rb") as handle:
                        header = handle.read(16)
                    if header == b"SQLite format 3\x00":
                        databases.append(Path(entry.path))
                        if len(databases) > MAX_DATABASES:
                            raise BackupError("Plus de 128 bases SQLite ; utilisez un script adapté.")
    visit(source, 0)
    return databases


def copy_sqlite(database, destination, deadline):
    if database.is_symlink():
        raise BackupError("Une base SQLite a été remplacée par un lien symbolique.")
    temporary = destination.with_name(destination.name + ".sqlite-backup-" + uuid.uuid4().hex)

    def progress(_status, _remaining, _total):
        if time.monotonic() >= deadline:
            raise BackupError("La copie SQLite a dépassé sa durée autorisée.")

    with contextlib.closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)) as incoming:
        with contextlib.closing(sqlite3.connect(temporary, timeout=2)) as outgoing:
            incoming.backup(outgoing, pages=256, progress=progress, sleep=0.05)
            outgoing.execute("PRAGMA journal_mode=DELETE")
            if outgoing.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise BackupError("Contrôle SQLite non valide ; sauvegarde finale non publiée.")
    os.chmod(temporary, 0o600)
    os.replace(temporary, destination)


def copy_files(source, staging, timeout):
    command = ["/usr/bin/rsync", "-a", "--no-owner", "--no-group", "--chmod=Du=rwx,Dgo=,Fu=rwX,Fgo="]
    command.extend("--exclude=" + name + "/" for name in sorted(EXCLUDED_DIRECTORIES))
    command.extend(["--exclude=*-wal", "--exclude=*-shm", "--", str(source) + "/", str(staging) + "/"])
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise BackupError("Copie de fichiers indisponible ou expirée ; vérifier rsync et l'espace du volume.") from None
    if result.returncode != 0:
        raise BackupError("La copie de fichiers a échoué ; aucune sauvegarde finale n'a été publiée.")


def backup(config):
    deadline = time.monotonic() + 260
    source = source_path(config)
    destination = destination_path(config, source)
    databases = sqlite_databases(source)
    staging = Path(tempfile.mkdtemp(prefix=".babamarket-staging-", dir=destination))
    os.chmod(staging, 0o700)
    metadata = staging.stat()
    if metadata.st_mode & 0o077 or metadata.st_uid != 0:
        raise BackupError("Le volume doit permettre un dossier de sauvegarde privé appartenant à root.")
    try:
        copy_files(source, staging, max(1, deadline - time.monotonic()))
        # Inclut également une base apparue pendant la copie des fichiers.
        copied_databases = sqlite_databases(staging)
        databases = list(dict.fromkeys([*databases, *(source / item.relative_to(staging) for item in copied_databases)]))
        if len(databases) > MAX_DATABASES:
            raise BackupError("Plus de 128 bases SQLite ; utilisez un script adapté.")
        for database in databases:
            target = staging / database.relative_to(source)
            if target.is_symlink() or target.parent.resolve() != target.parent:
                raise BackupError("Destination SQLite contenant un lien symbolique.")
            copy_sqlite(database, target, deadline)
        manifest = {"created_at": datetime.now(timezone.utc).isoformat(), "source": str(source), "sqlite_databases": len(databases), "consistency": "Fichiers copiés en fonctionnement ; SQLite sauvegardé en ligne. Autres moteurs : script personnalisé nécessaire."}
        (staging / ".noc-backup-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(staging / ".noc-backup-manifest.json", 0o600)
        final = destination / ("babamarket-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
        os.rename(staging, final)
        return final
    except Exception:
        # Aucune suppression récursive : la copie partielle reste privée et identifiable.
        with contextlib.suppress(OSError):
            (staging / ".error").write_text("Sauvegarde incomplète ; ne pas restaurer ce dossier.\n", encoding="utf-8")
            os.chmod(staging / ".error", 0o600)
        raise


def main():
    if getattr(os, "geteuid", lambda: -1)() != 0:
        print("Cette sauvegarde nécessite le compte root.", file=sys.stderr)
        return 1
    def terminated(_signal, _frame):
        raise BackupError("Délai interne maximal de sauvegarde atteint.")
    signal.signal(signal.SIGTERM, terminated)
    try:
        final = backup(read_configuration())
    except (BackupError, OSError, sqlite3.Error):
        error = sys.exc_info()[1]
        message = str(error) if isinstance(error, BackupError) else "Erreur de fichiers ou SQLite ; sauvegarde finale non publiée."
        print("Sauvegarde échouée : " + message, file=sys.stderr)
        return 1
    print("Sauvegarde créée : " + str(final))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
PY
