from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
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
    app.include_router(router)
    app.include_router(incidents_router)
    app.include_router(csv_analysis_router)
    app.include_router(dynamics_router)
    runtime_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    frontend_dist = runtime_root / "frontend" / "dist"
    if frontend_dist.is_dir():
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
    else:
        @app.get("/", include_in_schema=False, response_class=HTMLResponse)
        def development_index() -> str:
            return (
                "<h1>RB-Y1 CS Analyzer V5</h1>"
                "<p>Frontend build not found. Run npm --prefix frontend run build.</p>"
            )
    return app
