from __future__ import annotations

import io
import json
import sqlite3
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

from maxwell_daemon.core.backup import (
    BackupManager,
    BackupManifest,
    RestoreError,
    _quote_sqlite_identifier,
    _validated_tar_members,
)


def test_restore_rejects_path_traversal_members(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.tar.gz"
    payload = tmp_path / "payload.txt"
    payload.write_text("unsafe", encoding="utf-8")

    with tarfile.open(archive, "w:gz") as tar:
        tar.add(payload, arcname="../escape.txt")

    manager = BackupManager(config_path=tmp_path / "config.yaml", data_dir=tmp_path / "data")
    with pytest.raises(RestoreError, match="escapes destination"):
        manager.restore(archive)


def test_export_json_quotes_sqlite_identifiers(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "ledger.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute('CREATE TABLE "table with space" (id INTEGER PRIMARY KEY, value TEXT)')
        conn.execute('INSERT INTO "table with space" (value) VALUES (?)', ("ok",))
        conn.commit()
    finally:
        conn.close()

    manager = BackupManager(config_path=tmp_path / "config.yaml", data_dir=data_dir)
    exported = manager.export_json("ledger")

    assert exported["component"] == "ledger"
    assert exported["tables"]["table with space"] == [{"id": 1, "value": "ok"}]


def test_validated_tar_members_rejects_absolute_path(tmp_path: Path) -> None:
    archive = tmp_path / "absolute.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("/absolute.txt")
        info.size = 0
        tar.addfile(info, io.BytesIO(b""))

    with (
        tarfile.open(archive, "r:gz") as tar,
        pytest.raises(RestoreError, match=r"absolute path|escapes destination"),
    ):
        _validated_tar_members(tar, tmp_path / "restore")


def test_validated_tar_members_rejects_symlink(tmp_path: Path) -> None:
    archive = tmp_path / "symlink.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("link")
        info.type = tarfile.SYMTYPE
        info.linkname = "target.txt"
        tar.addfile(info)

    with tarfile.open(archive, "r:gz") as tar, pytest.raises(RestoreError, match="not supported"):
        _validated_tar_members(tar, tmp_path / "restore")


def test_quote_sqlite_identifier_rejects_nul_byte() -> None:
    with pytest.raises(ValueError, match="NUL byte"):
        _quote_sqlite_identifier("bad\x00name")


def test_restore_extracts_valid_members(tmp_path: Path) -> None:
    root = tmp_path / "staging" / "maxwell-backup"
    for name in ("config", "data", "audit", "artifacts", "memory"):
        (root / name).mkdir(parents=True, exist_ok=True)

    manifest = BackupManifest.create({}, tmp_path / "config.yaml", tmp_path / "data").to_dict()
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    archive = tmp_path / "valid.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(root, arcname="maxwell-backup")

    manager = BackupManager(config_path=tmp_path / "config.yaml", data_dir=tmp_path / "data")
    with (
        patch.object(manager, "_verify_hashes") as verify_hashes,
        patch.object(manager, "_restore_config") as restore_config,
        patch.object(manager, "_restore_sqlite") as restore_sqlite,
        patch.object(manager, "_restore_audit") as restore_audit,
        patch.object(manager, "_restore_artifacts") as restore_artifacts,
        patch.object(manager, "_restore_memory") as restore_memory,
    ):
        manager.restore(archive, force=True)

    verify_hashes.assert_called_once()
    restore_config.assert_called_once()
    restore_sqlite.assert_called_once()
    restore_audit.assert_called_once()
    restore_artifacts.assert_called_once()
    restore_memory.assert_called_once()
