from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from starlette.datastructures import FormData, UploadFile

from rby1_analyzer.core.security import exact_loopback_headers
from rby1_analyzer.ingest.quotas import Quotas, require_storage_limits
from rby1_analyzer.ingest.upload import ingest_upload, rollback_upload
from rby1_analyzer.jobs.manager import ActiveImportError
from rby1_analyzer.jobs.runner import ImportRef

from ..deps import Bearer
from ..schemas import CaseResponse, CaseUpdateRequest, JobResponse, SessionRequest, SessionResponse

router = APIRouter(prefix="/api")


@router.post("/session", response_model=SessionResponse)
def session(
    body: SessionRequest,
    request: Request,
    host: Annotated[str | None, Header()] = None,
    origin: Annotated[str | None, Header()] = None,
) -> SessionResponse:
    runtime = request.app.state.runtime
    if not exact_loopback_headers(host, origin, runtime.port):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "invalid loopback origin")
    token = runtime.authority.exchange(body.bootstrap_token)
    if token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid bootstrap proof")
    return SessionResponse(session_token=token)


@router.post("/cases", response_model=CaseResponse)
def create_case(request: Request, _token: Bearer) -> CaseResponse:
    return CaseResponse(case_id=request.app.state.runtime.cases.create())


@router.get("/cases")
def list_cases(request: Request, _token: Bearer) -> dict[str, list[dict[str, Any]]]:
    cases: list[dict[str, Any]] = []
    store = request.app.state.runtime.cases
    from rby1_analyzer.api.routes.csv_analysis import _format_case_timeline_stem
    for case_dir in sorted(store.root.iterdir(), reverse=True):
        if not case_dir.is_dir():
            continue
        try:
            db = store.open(case_dir.name)
            with db.connect() as connection:
                row = connection.execute("SELECT id,created_at,title FROM cases LIMIT 1").fetchone()
                if row is None:
                    continue
                case_id = row["id"]
                count_row = connection.execute(
                    "SELECT COUNT(*) as count FROM events WHERE artifact_id IN (SELECT id FROM artifacts WHERE case_id=?)",
                    (case_id,),
                ).fetchone()
                event_count = int(count_row["count"]) if count_row else 0
                stem, model_slug, period = _format_case_timeline_stem(connection, case_id)
                auto_name = f"{stem}.jsonl"
                custom_title = row["title"] if "title" in row.keys() and row["title"] else None
                display_name = custom_title or auto_name
                cases.append({
                    "case_id": case_id,
                    "created_at": row["created_at"],
                    "title": custom_title,
                    "display_name": display_name,
                    "filename_jsonl": auto_name,
                    "model": model_slug,
                    "period": period,
                    "event_count": event_count,
                })
        except Exception:
            continue
    return {"cases": cases}


@router.patch("/cases/{case_id}")
def update_case(
    case_id: str,
    body: CaseUpdateRequest,
    request: Request,
    _token: Bearer,
) -> dict[str, Any]:
    store = request.app.state.runtime.cases
    try:
        updated_title = store.update_title(case_id, body.title)
        return {"ok": True, "case_id": case_id, "title": updated_title}
    except (ValueError, FileNotFoundError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "case not found")


@router.delete("/cases/{case_id}")
def delete_case(
    case_id: str,
    request: Request,
    _token: Bearer,
) -> dict[str, bool]:
    store = request.app.state.runtime.cases
    try:
        deleted = store.delete(case_id)
        if not deleted:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "case not found")
        return {"ok": True}
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid case id")


@router.post("/cases/{case_id}/imports", response_model=JobResponse)
async def create_import(
    case_id: str, request: Request, _token: Bearer,
) -> JobResponse:
    runtime = request.app.state.runtime
    try:
        db = runtime.cases.open(case_id)
    except (ValueError, FileNotFoundError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "case not found") from None
    content_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
    if content_type != "multipart/form-data":
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "imports require multipart/form-data",
        )
    try:
        job_id = runtime.jobs.create(db, case_id)
    except ActiveImportError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": exc.detail_code, "message": "이 분석에서 이미 가져오기가 진행 중입니다."},
        ) from exc
    quotas = Quotas()
    total = 0
    refs: list[ImportRef] = []
    try:
        async with request.form(
            max_files=quotas.members,
            max_fields=0,
            max_part_size=64 * 1024,
        ) as form:
            files = _import_files(form)
            total = _store_import_files(
                request,
                db,
                case_id,
                quotas,
                files,
                refs,
            )
        request.app.state.import_runner.start(case_id, job_id, refs)
    except Exception as exc:
        for ref in reversed(refs):
            rollback_upload(
                db,
                case_id,
                ref.filename,
                ref.as_stored(),
            )
        code = _import_failure_code(exc)
        with db.connect() as connection:
            connection.execute("UPDATE jobs SET state='failed',detail_code=? WHERE id=?", (code, job_id))
        runtime.jobs.append(db, job_id, {
            "state": "failed",
            "detail_code": code,
            "counters": {
                "received_bytes": total,
                "total_bytes": total,
                "processed_bytes": 0,
                "progress_percent": 0,
            },
            "warning_delta": [],
        })
    return JobResponse(job_id=job_id)


def _import_files(form: FormData) -> list[UploadFile]:
    files: list[UploadFile] = []
    for field_name, value in form.multi_items():
        if field_name != "files":
            raise ValueError("unexpected_import_field")
        if not isinstance(value, UploadFile):
            raise ValueError("import_requires_files")
        files.append(value)
    if not files:
        raise ValueError("empty_import")
    return files


def _store_import_files(
    request: Request,
    db,
    case_id: str,
    quotas: Quotas,
    files: list[UploadFile],
    refs: list[ImportRef],
) -> int:
    total = 0
    sizes = [upload.size if upload.size is not None else quotas.upload_file for upload in files]
    total_projected = sum(sizes)
    if total_projected > quotas.upload_batch:
        raise ValueError("import_batch_too_large")
    paths = request.app.state.runtime.cases.paths(case_id)
    require_storage_limits(
        paths.root,
        paths.root.parent,
        paths.temp,
        total_projected,
        quotas=quotas,
        temporary_write=max(sizes, default=0),
    )
    for upload in files:
        filename = Path(upload.filename or "upload").name or "upload"
        result = ingest_upload(
            db, request.app.state.runtime.cases.paths(case_id), case_id, filename, upload.file,
            content_length=upload.size,
            quotas=quotas,
        )
        total += result.size
        refs.append(ImportRef.from_stored(filename, result))
    return total


def _import_failure_code(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        detail = str(exc.detail)
        if detail.startswith("Too many files."):
            return "import_batch_too_large"
        if detail.startswith("Too many fields."):
            return "import_form_not_supported"
        return detail
    return str(getattr(exc, "detail_code", str(exc)))


def _case_db(request: Request, case_id: str):
    try:
        return request.app.state.runtime.cases.open(case_id)
    except (ValueError, FileNotFoundError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "case not found") from None


def _time_observations(connection, event_ids: list[int]) -> dict[int, list[dict[str, object]]]:
    grouped: dict[int, list[dict[str, object]]] = {}
    for start in range(0, len(event_ids), 500):
        chunk = event_ids[start : start + 500]
        placeholders = ",".join("?" for _ in chunk)
        rows = connection.execute(
            "SELECT event_id,basis,value,raw,source_sequence,precision,timezone_known,"
            "parse_status,discontinuity_group,monotonic FROM time_observations "
            f"WHERE event_id IN ({placeholders}) ORDER BY source_sequence,id",
            chunk,
        ).fetchall()
        for row in rows:
            item = dict(row)
            event_id = int(item.pop("event_id"))
            item["timezone_known"] = bool(item["timezone_known"])
            item["monotonic"] = bool(item["monotonic"])
            grouped.setdefault(event_id, []).append(item)
    return grouped


def _failed_pair_event_ids(connection, event_ids: list[int]) -> set[int]:
    related: set[int] = set()
    for start in range(0, len(event_ids), 500):
        chunk = event_ids[start : start + 500]
        placeholders = ",".join("?" for _ in chunk)
        rows = connection.execute(
            "SELECT c.request_event_id,c.result_event_id FROM correlations c "
            "JOIN events result ON result.id=c.result_event_id "
            f"WHERE (c.request_event_id IN ({placeholders}) "
            f"OR c.result_event_id IN ({placeholders})) AND ("
            "LOWER(result.severity) IN ('error','critical') OR "
            "LOWER(result.category) IN ('failure','timeout','minorfault','majorfault') OR "
            "LOWER(COALESCE(result.result,'')) IN "
            "('failed','failure','timeout','cancelled','canceled'))",
            [*chunk, *chunk],
        ).fetchall()
        for row in rows:
            related.add(int(row["request_event_id"]))
            related.add(int(row["result_event_id"]))
    return related


@router.get("/cases/{case_id}/summary")
def case_summary(case_id: str, request: Request, _token: Bearer) -> dict[str, object]:
    db = _case_db(request, case_id)
    with db.connect() as connection:
        artifacts = connection.execute(
            "SELECT kind,status,COUNT(*) AS count,SUM(size) AS bytes FROM artifacts "
            "GROUP BY kind,status ORDER BY kind,status"
        ).fetchall()
        events = connection.execute(
            "SELECT severity,category,COUNT(*) AS count FROM events "
            "GROUP BY severity,category ORDER BY severity,category"
        ).fetchall()
        warning_count = connection.execute("SELECT COUNT(*) AS count FROM warnings").fetchone()
        metadata = connection.execute(
            "SELECT excerpt FROM events WHERE category='metadata' ORDER BY id LIMIT 20"
        ).fetchall()
    return {
        "case_id": case_id,
        "artifacts": [dict(row) for row in artifacts],
        "events": [dict(row) for row in events],
        "warning_count": int(warning_count["count"] if warning_count else 0),
        "metadata": [row["excerpt"] for row in metadata],
    }


@router.get("/cases/{case_id}/events")
def case_events(
    case_id: str,
    request: Request,
    _token: Bearer,
    q: Annotated[str | None, Query(max_length=200)] = None,
    severity: Annotated[str | None, Query(max_length=32)] = None,
    category: Annotated[str | None, Query(max_length=64)] = None,
    component: Annotated[str | None, Query(max_length=160)] = None,
    joint: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=10_000)] = 5000,
) -> dict[str, object]:
    db = _case_db(request, case_id)
    clauses: list[str] = []
    values: list[object] = []
    for field, value in (
        ("severity", severity),
        ("category", category),
        ("component", component),
        ("joint", joint),
    ):
        if value:
            clauses.append(f"{field}=?")
            values.append(value)
    if q:
        clauses.append("(excerpt LIKE ? OR signature LIKE ? OR component LIKE ? OR command LIKE ?)")
        needle = f"%{q}%"
        values.extend((needle, needle, needle, needle))
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    values.append(limit)
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT id,source_name,member_name,line,byte_offset,raw_digest,excerpt,severity,"
            "category,signature,component,joint,command,result,time_value,time_basis,time_raw "
            f"FROM events{where} ORDER BY id LIMIT ?",
            values,
        ).fetchall()
        event_ids = [int(row["id"]) for row in rows]
        observations = _time_observations(connection, event_ids)
        failed_pair_ids = _failed_pair_event_ids(connection, event_ids)
    return {
        "events": [
            {
                **dict(row),
                "id": str(row["id"]),
                "time": float(row["time_value"] if row["time_value"] is not None else row["id"]),
                "time_observations": observations.get(int(row["id"]), []),
                "failed_pair": int(row["id"]) in failed_pair_ids,
            }
            for row in rows
        ]
    }


@router.get("/cases/{case_id}/diagnostics/{diagnostic_id}")
def case_diagnostic(
    case_id: str, diagnostic_id: int, request: Request, _token: Bearer
) -> dict[str, object]:
    db = _case_db(request, case_id)
    with db.connect() as connection:
        row = connection.execute(
            "SELECT e.*,a.sha256 FROM events e JOIN artifacts a ON a.id=e.artifact_id WHERE e.id=?",
            (diagnostic_id,),
        ).fetchone()
        provenance = connection.execute(
            "SELECT p.original_name,p.member_name FROM provenance p "
            "JOIN events e ON e.artifact_id=p.artifact_id WHERE e.id=? ORDER BY p.id",
            (diagnostic_id,),
        ).fetchall()
        observations = _time_observations(connection, [diagnostic_id]).get(diagnostic_id, [])
        correlations = connection.execute(
            "SELECT c.id,c.request_event_id,c.result_event_id,c.basis,c.delta,c.confidence,"
            "c.explanation,c.causal,request.line AS request_line,request.excerpt AS request_excerpt,"
            "result.line AS result_line,result.excerpt AS result_excerpt "
            "FROM correlations c "
            "JOIN events request ON request.id=c.request_event_id "
            "JOIN events result ON result.id=c.result_event_id "
            "WHERE c.request_event_id=? OR c.result_event_id=? ORDER BY c.id",
            (diagnostic_id, diagnostic_id),
        ).fetchall()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "diagnostic not found")
    diagnostics = json.loads(row["diagnostic_json"])
    return {
        "event_id": str(row["id"]),
        "observed": [row["excerpt"]],
        "diagnostics": diagnostics,
        "time_observations": observations,
        "correlations": [
            {
                **dict(item),
                "request_event_id": str(item["request_event_id"]),
                "result_event_id": str(item["result_event_id"]),
                "causal": bool(item["causal"]),
            }
            for item in correlations
        ],
        "evidence": {
            "source": row["source_name"],
            "member": row["member_name"],
            "line": row["line"],
            "byte_offset": row["byte_offset"],
            "raw_digest": row["raw_digest"],
            "artifact_sha256": row["sha256"],
            "provenance": [dict(item) for item in provenance],
        },
    }


@router.get("/cases/{case_id}/warnings")
def case_warnings(case_id: str, request: Request, _token: Bearer) -> dict[str, object]:
    db = _case_db(request, case_id)
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT id,code,message,member_name FROM warnings ORDER BY id"
        ).fetchall()
    return {"warnings": [dict(row) for row in rows]}


def _job(request: Request, job_id: str):
    for case_dir in request.app.state.runtime.cases.root.iterdir():
        try:
            db = request.app.state.runtime.cases.open(case_dir.name)
            return db, request.app.state.runtime.jobs.get(db, job_id)
        except (KeyError, ValueError, FileNotFoundError):
            continue
    raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")


@router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request, _token: Bearer):
    _db, job = _job(request, job_id)
    return job


@router.get("/jobs/{job_id}/stream")
def stream_job(job_id: str, request: Request, _token: Bearer, after_seq: int = 0):
    if after_seq < 0:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "after_seq must be non-negative")
    db, _job_record = _job(request, job_id)

    def records():
        for record in request.app.state.runtime.jobs.stream(db, job_id, after_seq):
            yield json.dumps(record, separators=(",", ":")) + "\n"

    return StreamingResponse(records(), media_type="application/x-ndjson")


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request, _token: Bearer):
    db, job_record = _job(request, job_id)
    changed = request.app.state.runtime.jobs.request_cancel(db, job_id)
    stopped = False
    if changed:
        stopped = request.app.state.import_runner.cancel(job_record["case_id"], job_id)
    return {
        "job_id": job_id,
        "state": "cancelled" if stopped else "cancel_requested",
        "changed": changed,
    }
