import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
import uvicorn

import database
from scrapers import interactive_login_and_save_session, run_collection_cycle

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"

scheduler = BackgroundScheduler()
_collection_lock = threading.Lock()
_is_collecting = False


def _scheduled_job() -> None:
    global _is_collecting
    if _is_collecting:
        return
    with _collection_lock:
        _is_collecting = True
        try:
            run_collection_cycle(trigger_type="scheduled")
        finally:
            _is_collecting = False


def _reconfigure_scheduler() -> None:
    criteria = database.get_criteria()
    minutes = max(10, int(criteria.get("schedule_minutes", 180)))
    enabled = bool(criteria.get("auto_schedule_enabled", 1))

    if scheduler.get_job("auto_collect"):
        scheduler.remove_job("auto_collect")

    if enabled:
        scheduler.add_job(
            _scheduled_job,
            "interval",
            minutes=minutes,
            id="auto_collect",
            replace_existing=True,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_db()
    scheduler.start()
    _reconfigure_scheduler()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(
    title="맞춤 일자리 탐색·관리 에이전트",
    version="0.1.0",
    lifespan=lifespan,
)


class JobUpdatePayload(BaseModel):
    status: Optional[str] = None
    memo: Optional[str] = None


class CriteriaPayload(BaseModel):
    include_keywords: str = ""
    exclude_keywords: str = ""
    preferred_locations: str = ""
    preferred_career: str = "무관"
    min_score: int = 0
    max_age_days: int = 7
    ai_prompt: str = ""
    schedule_minutes: int = 180
    auto_schedule_enabled: bool = True
    gemini_model: str = ""


class SourcePayload(BaseModel):
    id: str
    name: str
    url: str
    scraper_type: str = "generic"
    requires_login: bool = False
    login_url: str = ""
    enabled: bool = True
    max_items: int = 100


class SourceUpdatePayload(BaseModel):
    max_items: Optional[int] = None
    enabled: Optional[bool] = None


@app.get("/", response_class=HTMLResponse)
def index_page():
    html_file = TEMPLATES_DIR / "index.html"
    return FileResponse(html_file)


@app.get("/api/overview")
def api_overview():
    database.recalculate_existing_jobs()
    return {
        "stats": database.get_stats(),
        "criteria": database.get_criteria(),
        "sources": database.list_sources(only_enabled=False),
        "logs": database.list_run_logs(limit=10),
        "is_collecting": _is_collecting,
    }


@app.get("/api/jobs")
def api_list_jobs(
    status: str = Query("all"),
    source: str = Query("all"),
    career: str = Query("all"),
    min_score: int = Query(0),
    search: str = Query(""),
):
    return {
        "jobs": database.list_jobs(
            status=status,
            source=source,
            career=career,
            min_score=min_score,
            search=search,
        )
    }


@app.patch("/api/jobs/{job_id}")
def api_update_job(job_id: int, payload: JobUpdatePayload):
    updated = database.update_job_status_or_memo(
        job_id=job_id, status=payload.status, memo=payload.memo
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job": updated, "stats": database.get_stats()}


@app.put("/api/criteria")
def api_save_criteria(payload: CriteriaPayload):
    updated = database.update_criteria(payload.model_dump())
    recalculated = database.recalculate_existing_jobs()
    _reconfigure_scheduler()
    return {"criteria": updated, "recalculated": recalculated}


@app.post("/api/sources")
def api_upsert_source(payload: SourcePayload):
    saved = database.upsert_source(payload.model_dump())
    return {"source": saved, "sources": database.list_sources()}


@app.patch("/api/sources/{source_id}")
def api_patch_source(source_id: str, payload: SourceUpdatePayload):
    updated = database.update_source_settings(
        source_id=source_id,
        max_items=payload.max_items,
        enabled=payload.enabled,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Source not found")
    return {"source": updated, "sources": database.list_sources()}


@app.delete("/api/sources/{source_id}")
def api_delete_source(source_id: str):
    database.delete_source(source_id)
    return {"sources": database.list_sources()}


@app.post("/api/collect")
def api_trigger_collect(source_id: Optional[str] = Query(None)):
    global _is_collecting
    if _is_collecting:
        return {"ok": False, "message": "이미 수집 작업이 진행 중입니다."}

    def _bg():
        global _is_collecting
        with _collection_lock:
            _is_collecting = True
            try:
                run_collection_cycle(
                    trigger_type="manual", target_source_id=source_id
                )
            finally:
                _is_collecting = False

    threading.Thread(target=_bg, daemon=True).start()
    return {
        "ok": True,
        "message": "백그라운드에서 일자리 수집 및 조건 필터링을 시작했습니다.",
    }


@app.post("/api/reset")
def api_reset_db(recollect: bool = Query(True)):
    global _is_collecting
    if _is_collecting:
        return {
            "ok": False,
            "message": "현재 수집이 진행 중입니다. 수집 완료 후 다시 시도해 주세요.",
        }
    res = database.reset_jobs_db()
    if recollect:
        def _bg():
            global _is_collecting
            with _collection_lock:
                _is_collecting = True
                try:
                    run_collection_cycle(trigger_type="manual")
                finally:
                    _is_collecting = False

        threading.Thread(target=_bg, daemon=True).start()
    return {
        "ok": True,
        "deleted": res,
        "message": "DB(공고 및 수집이력)가 모두 초기화되었으며, 새로 수집을 시작합니다.",
    }


@app.post("/api/sources/{source_id}/login")
def api_source_login(source_id: str):
    result = interactive_login_and_save_session(source_id=source_id)
    return result


def main():
    port = int(os.environ.get("PORT", "8088"))
    uvicorn.run("app:app", host="127.0.0.1", port=port, reload=True)


if __name__ == "__main__":
    main()
