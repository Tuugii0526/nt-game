"""FastAPI app factory.

Loads the SQLite schema and the on-disk problems at startup, mounts
static files and templates, and wires session middleware so we can
identify a participant by a signed cookie.
"""
from __future__ import annotations

import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import db, problems
from .judge import queue_client
from .routes import router

ROOT = Path(__file__).resolve().parent.parent

# Paths are env-driven so the same code runs in dev (relative to the repo)
# and inside the container (under bind/volume mounts).
PROBLEMS_DIR = Path(os.environ.get("JUDGE_PROBLEMS_DIR", str(ROOT.parent / "problems")))
STATIC_DIR = ROOT / "static"

# Air-gapped LAN: a stable per-install secret is fine. Override via env
# if you want sessions to survive across reinstalls on the same Pi.
SECRET_KEY = os.environ.get("JUDGE_SECRET", secrets.token_hex(32))


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    db.init()
    problems.load_all(PROBLEMS_DIR)
    queue_client.ensure_dirs()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Coding Judge", lifespan=_lifespan)
    app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, same_site="lax")
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(router)
    return app


app = create_app()
