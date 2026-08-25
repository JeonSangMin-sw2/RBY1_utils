from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import BinaryIO

from rby1_analyzer.storage.cases import CasePaths
from rby1_analyzer.storage.content import StoredContent, retain_stream
from rby1_analyzer.storage.database import Database

from .quotas import Quotas, require_storage_limits


def ingest_upload(
    db: Database, paths: CasePaths, case_id: str, filename: str, stream: BinaryIO,
    *, content_length: int | None, quotas: Quotas = Quotas(),
) -> StoredContent:
    if content_length is not None and content_length > quotas.upload_file:
        raise ValueError("upload_too_large")
    projected = min(content_length or quotas.upload_file, quotas.upload_file)
    require_storage_limits(
        paths.root,
        paths.root.parent,
        paths.temp,
        projected,
        quotas=quotas,
    )
    stored = retain_stream(stream, lambda d: paths.content("sources", d), paths.temp, limit=quotas.upload_file)
    try:
        with db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT id FROM artifacts WHERE case_id=? AND kind='source' AND sha256=?",
                (case_id, stored.digest),
            ).fetchone()
            if row is None:
                cursor = connection.execute(
                    "INSERT INTO artifacts(case_id,kind,sha256,size,stored_path,original_name,status) "
                    "VALUES (?, 'source', ?, ?, ?, ?, 'stored')",
                    (case_id, stored.digest, stored.size, str(stored.path), Path(filename).name),
                )
                artifact_id = cursor.lastrowid
            else:
                artifact_id = row["id"]
            connection.execute(
                "INSERT INTO provenance(artifact_id,original_name) VALUES (?, ?)",
                (artifact_id, Path(filename).name),
            )
            connection.execute("COMMIT")
    except sqlite3.Error:
        if not stored.duplicate:
            stored.path.unlink(missing_ok=True)
        raise
    return stored


def rollback_upload(
    db: Database,
    case_id: str,
    filename: str,
    stored: StoredContent,
) -> None:
                                                                                 
    stored_path: Path | None = None
    delete_artifact = False
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        artifact = connection.execute(
            "SELECT id,stored_path FROM artifacts "
            "WHERE case_id=? AND kind='source' AND sha256=?",
            (case_id, stored.digest),
        ).fetchone()
        if artifact is not None:
            provenance = connection.execute(
                "SELECT id FROM provenance WHERE artifact_id=? AND original_name=? "
                "ORDER BY id DESC LIMIT 1",
                (artifact["id"], Path(filename).name),
            ).fetchone()
            if provenance is not None:
                connection.execute("DELETE FROM provenance WHERE id=?", (provenance["id"],))
            remaining = connection.execute(
                "SELECT 1 FROM provenance WHERE artifact_id=? LIMIT 1", (artifact["id"],)
            ).fetchone()
            if remaining is None:
                stored_path = Path(artifact["stored_path"])
                connection.execute("DELETE FROM artifacts WHERE id=?", (artifact["id"],))
                delete_artifact = True
        connection.execute("COMMIT")
    if delete_artifact and stored_path is not None:
        stored_path.unlink(missing_ok=True)
