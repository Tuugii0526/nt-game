"""HTTP routes. All endpoints are sync `def` so FastAPI runs them in
its threadpool, which keeps the blocking grader off the event loop."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from . import db, problems, timer
from .judge import grader

ROOT = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(ROOT / "templates"))

MAX_NAME_LEN = 40
MAX_CODE_BYTES = 64 * 1024

router = APIRouter()


def _participant(request: Request, conn) -> db.sqlite3.Row | None:
    pid = request.session.get("participant_id")
    return db.get_participant(conn, pid)


def _ctx(request: Request, participant, **extra) -> dict:
    """Common template context: participant + remaining seconds."""
    return {
        "participant": participant,
        "seconds_left": timer.seconds_left(participant["joined_at"]) if participant else 0,
        **extra,
    }


@router.get("/", response_class=HTMLResponse)
def index(request: Request, conn=Depends(db.get_conn)):
    target = "/problems" if _participant(request, conn) else "/join"
    return RedirectResponse(target, status_code=303)


@router.get("/join", response_class=HTMLResponse)
def join_form(request: Request):
    return templates.TemplateResponse(request=request, name="join.html", context={})


@router.post("/join")
def join(
    request: Request,
    name: str = Form(...),
    conn=Depends(db.get_conn),
):
    cleaned = (name or "").strip()
    if not cleaned:
        return templates.TemplateResponse(
            request=request,
            name="join.html",
            context={"error": "Name is required"},
            status_code=400,
        )
    if len(cleaned) > MAX_NAME_LEN:
        return templates.TemplateResponse(
            request=request,
            name="join.html",
            context={"error": f"Name must be {MAX_NAME_LEN} characters or fewer"},
            status_code=400,
        )
    p = db.get_or_create_participant(conn, cleaned)
    request.session["participant_id"] = p["id"]
    return RedirectResponse("/problems", status_code=303)


@router.get("/problems", response_class=HTMLResponse)
def problems_page(request: Request, conn=Depends(db.get_conn)):
    p = _participant(request, conn)
    if p is None:
        return RedirectResponse("/join", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="problems.html",
        context=_ctx(
            request, p,
            problems=problems.all_problems(),
            best=db.best_score_per_problem(conn, p["id"]),
        ),
    )


@router.get("/problem/{problem_id}", response_class=HTMLResponse)
def problem_page(problem_id: str, request: Request, conn=Depends(db.get_conn)):
    p = _participant(request, conn)
    if p is None:
        return RedirectResponse("/join", status_code=303)
    problem = problems.get(problem_id)
    if problem is None:
        raise HTTPException(404, "Problem not found")
    return templates.TemplateResponse(
        request=request,
        name="problem.html",
        context=_ctx(
            request, p,
            problem=problem,
            history=db.submissions_for(conn, p["id"], problem_id),
        ),
    )


@router.post("/submit")
def submit(
    request: Request,
    problem_id: str = Form(...),
    code: str = Form(...),
    conn=Depends(db.get_conn),
):
    p = _participant(request, conn)
    if p is None:
        raise HTTPException(401, "Not joined")
    if timer.seconds_left(p["joined_at"]) == 0:
        raise HTTPException(423, "Time is up")
    if len(code.encode("utf-8")) > MAX_CODE_BYTES:
        raise HTTPException(413, "Code too large")

    result = grader.grade(problem_id, code)
    db.insert_submission(
        conn, p["id"], problem_id, code,
        result.verdict, result.tests_passed, result.tests_total, result.runtime_ms,
    )
    return JSONResponse(result.as_dict())


@router.get("/leaderboard", response_class=HTMLResponse)
def leaderboard(request: Request, conn=Depends(db.get_conn)):
    p = _participant(request, conn)
    return templates.TemplateResponse(
        request=request,
        name="leaderboard.html",
        context=_ctx(request, p, rows=db.leaderboard(conn)),
    )
