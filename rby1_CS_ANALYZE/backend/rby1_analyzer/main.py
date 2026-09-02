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

    # Multi-path frontend dist directory resolution
    meipass = getattr(sys, "_MEIPASS", None)
    candidates = [
        Path(meipass) / "frontend" / "dist" if meipass else None,
        Path(meipass) / "dist" if meipass else None,
        Path(meipass) if (meipass and (Path(meipass) / "index.html").is_file()) else None,
        Path(__file__).resolve().parents[2] / "frontend" / "dist",
        Path(__file__).resolve().parents[1] / "frontend" / "dist",
        Path.cwd() / "frontend" / "dist",
        Path.cwd() / "dist",
    ]
    frontend_dist = next((p for p in candidates if p and p.is_dir() and (p / "index.html").is_file()), None)

    if frontend_dist is not None:
        index_file = frontend_dist / "index.html"
        assets_dir = frontend_dist / "assets"

        if assets_dir.is_dir():
            @app.get("/assets/{file_path:path}", include_in_schema=False)
            async def serve_asset(file_path: str):
                target = assets_dir / file_path
                if not target.is_file():
                    return Response(status_code=404)
                if file_path.lower().endswith(".js"):
                    return FileResponse(target, media_type="application/javascript")
                if file_path.lower().endswith(".css"):
                    return FileResponse(target, media_type="text/css")
                return FileResponse(target)

        @app.get("/", include_in_schema=False)
        def serve_index() -> FileResponse:
            return FileResponse(index_file, media_type="text/html")

        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
    else:
        @app.get("/", include_in_schema=False, response_class=HTMLResponse)
        def development_index() -> str:
            return (
                "<h1>RB-Y1 CS Analyzer V5</h1>"
                "<p>Frontend build not found. Run npm --prefix frontend run build.</p>"
            )
    return app
