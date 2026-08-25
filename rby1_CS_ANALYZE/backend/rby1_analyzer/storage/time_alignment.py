from __future__ import annotations

from datetime import datetime

from .database import Database


def align_naive_log_wall_times(database: Database) -> None:
                                                                                   
    with database.connect() as connection:
        raw_fault_times = connection.execute(
            "SELECT DISTINCT raw FROM time_observations "
            "WHERE basis='fault_wall' AND timezone_known=1 AND parse_status='parsed'"
        ).fetchall()

    offsets: set[float] = set()
    for row in raw_fault_times:
        try:
            parsed = datetime.fromisoformat(str(row["raw"]).replace("Z", "+00:00"))
        except ValueError:
            continue
        offset = parsed.utcoffset()
        if offset is not None:
            offsets.add(offset.total_seconds())
    if len(offsets) != 1:
        return

    offset_seconds = offsets.pop()
    with database.connect() as connection:
        observations = connection.execute(
            "SELECT id,event_id,value FROM time_observations "
            "WHERE basis='log_wall' AND timezone_known=0 "
            "AND parse_status='parsed' AND value IS NOT NULL"
        ).fetchall()
        if not observations:
            return
        aligned = [
            (float(row["value"]) - offset_seconds, int(row["id"]))
            for row in observations
        ]
        event_times = [
            (float(row["value"]) - offset_seconds, int(row["event_id"]))
            for row in observations
            if row["event_id"] is not None
        ]
        connection.execute("BEGIN IMMEDIATE")
        connection.executemany(
            "UPDATE time_observations SET value=?,parse_status='timezone_inferred' WHERE id=?",
            aligned,
        )
        connection.executemany(
            "UPDATE events SET time_value=? WHERE id=? AND time_basis='log_wall'",
            event_times,
        )
        connection.execute("COMMIT")
