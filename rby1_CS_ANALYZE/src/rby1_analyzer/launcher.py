from __future__ import annotations

import argparse
import multiprocessing
import shutil
import socket
import subprocess
import tempfile
import webbrowser
from pathlib import Path

import uvicorn

from rby1_analyzer.core.config import Settings
from rby1_analyzer.core.security import SessionAuthority
from rby1_analyzer.jobs.manager import JobManager
from rby1_analyzer.main import RuntimeContext, create_app
from rby1_analyzer.storage.cases import CaseStore


def _server_config(app: object, host: str, port: int) -> uvicorn.Config:
                                                                                          
    return uvicorn.Config(
        app,
        host=host,
        port=port,
        access_log=False,
        log_config=None,
    )


def open_standalone_ui(url: str) -> bool:
                                                                                        
    for browser_name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        executable = shutil.which(browser_name)
        if executable is None:
            continue
        try:
            subprocess.Popen(  # noqa: S603
                [executable, f"--app={url}", "--window-size=1500,950"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            continue
        return True
    webbrowser.open(url)
    return False


def main() -> None:
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-open-browser", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--bootstrap-token", help=argparse.SUPPRESS)
    parser.add_argument("--data-root", type=Path)
    args = parser.parse_args()
    default_settings = Settings.default()
    settings = Settings(args.data_root or default_settings.data_root)
    if args.self_test:
        with tempfile.TemporaryDirectory(prefix="rby1-analyzer-self-test-") as directory:
            authority = SessionAuthority()
            runtime = RuntimeContext(
                43123,
                authority,
                CaseStore(Path(directory)),
                JobManager(),
            )
            app = create_app(runtime)
            paths = {route.path for route in app.routes}
            required = {
                "/api/session",
                "/api/cases",
                "/api/v2/cases/{case_id}/incidents",
            }
            if not required.issubset(paths):
                raise SystemExit("self-test failed: API routes missing")
        print("PASS: RB-Y1 CS Analyzer V4 launcher self-test")
        return
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((settings.host, args.port))
    sock.listen(128)
    port = sock.getsockname()[1]
    authority = (
        SessionAuthority(settings.bootstrap_ttl_seconds, bootstrap_token=args.bootstrap_token)
        if args.bootstrap_token
        else SessionAuthority(settings.bootstrap_ttl_seconds)
    )
    runtime = RuntimeContext(port, authority, CaseStore(settings.data_root), JobManager())
    url = f"http://127.0.0.1:{port}/#bootstrap={authority.bootstrap_token}"
    if not args.no_open_browser:
        open_standalone_ui(url)
    else:
        print(url, flush=True)
    config = _server_config(create_app(runtime), settings.host, port)
    try:
        uvicorn.Server(config).run(sockets=[sock])
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
