from __future__ import annotations

from dataclasses import dataclass
import mimetypes
import os
from pathlib import Path
import sys

# Ensure Windows registry does not corrupt JavaScript / CSS MIME types
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("application/json", ".json")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("image/png", ".png")
mimetypes.add_type("font/woff2", ".woff2")

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from rby1_analyzer.api.routes import router
from rby1_analyzer.api.routes.incidents import router as incidents_router
from rby1_analyzer.api.routes.csv_analysis import router as csv_analysis_router
from rby1_analyzer.api.routes.dynamics import router as dynamics_router
from rby1_analyzer.charts import SQLiteChartRepository
from rby1_analyzer.core.config import Settings
from rby1_analyzer.core.security import SessionAuthority
from rby1_analyzer.jobs.manager import JobManager
from rby1_analyzer.jobs.runner import ProcessJobRunner
from rby1_analyzer.storage.cases import CaseStore


@dataclass(slots=True)
class RuntimeContext:
    port: int
    authority: SessionAuthority
    cases: CaseStore
    jobs: JobManager


def _resolve_frontend_dist() -> Path | None:
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.extend([
            Path(meipass) / "frontend" / "dist",
            Path(meipass) / "dist",
            Path(meipass),
        ])
    if getattr(sys, "executable", None):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.extend([
            exe_dir / "frontend" / "dist",
            exe_dir / "dist",
        ])
    file_dir = Path(__file__).resolve().parent
    candidates.extend([
        file_dir.parents[1] / "frontend" / "dist",  # repo_root/frontend/dist
        file_dir.parents[0] / "frontend" / "dist",
        Path.cwd() / "frontend" / "dist",
        Path.cwd() / "dist",
    ])
    for cand in candidates:
        if cand and cand.is_dir() and (cand / "index.html").is_file():
            return cand
    for cand in candidates:
        if cand and cand.is_dir():
            return cand
    return None


def create_app(runtime: RuntimeContext | None = None) -> FastAPI:
    if runtime is None:
        settings = Settings.default()
        runtime = RuntimeContext(0, SessionAuthority(), CaseStore(settings.data_root), JobManager())
    import_runner = ProcessJobRunner(runtime.cases)
    app = FastAPI(title="RB-Y1 CS Log Analyzer V5", version="5.0.0")
    app.add_event_handler("shutdown", import_runner.shutdown)
    app.state.runtime = runtime
    app.state.chart_repository = SQLiteChartRepository(runtime.cases)
    app.state.import_runner = import_runner

    # Strict MIME type enforcement middleware for Windows compatibility
    @app.middleware("http")
    async def enforce_mime_types(request: Request, call_next) -> Response:
        response = await call_next(request)
        path = request.url.path.lower()
        if path.endswith(".js") or path.endswith(".mjs"):
            response.headers["content-type"] = "application/javascript; charset=utf-8"
        elif path.endswith(".css"):
            response.headers["content-type"] = "text/css; charset=utf-8"
        elif path.endswith(".json"):
            response.headers["content-type"] = "application/json; charset=utf-8"
        elif path.endswith(".svg"):
            response.headers["content-type"] = "image/svg+xml"
        elif path.endswith(".png"):
            response.headers["content-type"] = "image/png"
        return response

    app.include_router(router)
    app.include_router(incidents_router)
    app.include_router(csv_analysis_router)
    app.include_router(dynamics_router)

    frontend_dist = _resolve_frontend_dist()
    if frontend_dist:
        print(f"[*] Serving Frontend UI from: {frontend_dist}", flush=True)
    else:
        print("[WARNING] Frontend UI directory not found!", flush=True)

    def _find_asset_file(file_path: str) -> Path | None:
        if not frontend_dist:
            return None
        # Sanitize filename to prevent directory traversal
        clean_name = Path(file_path).name
        # 1. Exact match in assets/
        cand = frontend_dist / "assets" / clean_name
        if cand.is_file():
            return cand
        # 2. Exact match in dist root
        cand = frontend_dist / clean_name
        if cand.is_file():
            return cand
        # 3. Case-insensitive match in assets/
        assets_dir = frontend_dist / "assets"
        if assets_dir.is_dir():
            target_lower = clean_name.lower()
            for entry in assets_dir.iterdir():
                if entry.is_file() and entry.name.lower() == target_lower:
                    return entry
        return None

    @app.get("/assets/{file_path:path}", include_in_schema=False)
    async def serve_asset(file_path: str) -> Response:
        target = _find_asset_file(file_path)
        if not target:
            return Response(status_code=404, content=f"Asset not found: {file_path}", media_type="text/plain")

        media_type = "application/javascript" if target.name.lower().endswith(".js") else (
            "text/css" if target.name.lower().endswith(".css") else (
                "image/svg+xml" if target.name.lower().endswith(".svg") else (
                    "image/png" if target.name.lower().endswith(".png") else None
                )
            )
        )
        return FileResponse(target, media_type=media_type)

    @app.get("/models/{file_path:path}", include_in_schema=False)
    async def serve_model(file_path: str) -> Response:
        if frontend_dist:
            target = frontend_dist / "models" / file_path
            if target.is_file():
                return FileResponse(target)
        return Response(status_code=404, media_type="text/plain")

    @app.get("/favicon.ico", include_in_schema=False)
    def serve_favicon() -> Response:
        if frontend_dist:
            fav = frontend_dist / "favicon.ico"
            if fav.is_file():
                return FileResponse(fav)
        return Response(status_code=204)

    @app.get("/", include_in_schema=False)
    def serve_index() -> Response:
        if frontend_dist and (frontend_dist / "index.html").is_file():
            return FileResponse(frontend_dist / "index.html", media_type="text/html")
        return HTMLResponse("<h1>RB-Y1 CS Analyzer V5</h1><p>Frontend build not found.</p>")

    @app.get("/{file_path:path}", include_in_schema=False)
    async def serve_root_fallback(file_path: str) -> Response:
        if not file_path or file_path == "/":
            if frontend_dist and (frontend_dist / "index.html").is_file():
                return FileResponse(frontend_dist / "index.html", media_type="text/html")
            return HTMLResponse("<h1>RB-Y1 CS Analyzer V5</h1><p>Frontend build not found.</p>")
        target = _find_asset_file(file_path)
        if target:
            media_type = "application/javascript" if target.name.lower().endswith(".js") else (
                "text/css" if target.name.lower().endswith(".css") else None
            )
            return FileResponse(target, media_type=media_type)
        if frontend_dist and (frontend_dist / "index.html").is_file():
            return FileResponse(frontend_dist / "index.html", media_type="text/html")
        return Response(status_code=404, content=f"Not found: {file_path}", media_type="text/plain")

    return app
