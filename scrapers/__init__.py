from typing import Any, Dict, List, Optional, Set
import database
from matcher import evaluate_job
from scrapers.date_filter import should_skip_by_date
from scrapers.gojobs import scrape_gojobs
from scrapers.jobkorea import scrape_jobkorea
from scrapers.generic import scrape_generic_portal


def _get_known_external_ids(source_id: str) -> Set[str]:
    with database.get_conn() as conn:
        rows = conn.execute(
            "SELECT external_id FROM jobs WHERE source = ?", (source_id,)
        ).fetchall()
        return {r["external_id"] for r in rows}


def run_collection_cycle(
    trigger_type: str = "manual", target_source_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    모든 활성 구직 사이트(또는 지정된 사이트)를 순회하며:
    1) 마감일이 지난 기존 공고들을 '마감(expired)' 상태로 자동 전환
    2) 사이트별 스크래퍼 실행 (중복 ID 제외)
    3) 신규 공고에 대해 규칙 기반 조건 매칭(직무 키워드·근무지역·경력) 수행
    4) SQLite DB에 저장 및 실행 이력 기록
    """
    run_id = database.create_run_log(trigger_type=trigger_type)
    expired_cnt = database.mark_expired_jobs()
    criteria = database.get_criteria()
    max_age_days = int(criteria.get("max_age_days", 7))

    if target_source_id:
        src = database.get_source(target_source_id)
        sources = [src] if src else []
    else:
        sources = database.list_sources(only_enabled=True)

    total_scanned = 0
    new_saved = 0
    messages: List[str] = []

    for src in sources:
        if not src:
            continue
        source_id = src["id"]
        source_name = src["name"]
        scraper_type = src.get("scraper_type", "generic")
        known_ids = _get_known_external_ids(source_id)

        try:
            if scraper_type == "gojobs":
                raw_jobs = scrape_gojobs(src, known_ids, max_age_days=max_age_days)
            elif scraper_type == "jobkorea":
                raw_jobs = scrape_jobkorea(
                    src, known_ids, database.DATA_DIR, max_age_days=max_age_days
                )
            else:
                raw_jobs = scrape_generic_portal(src, known_ids, database.DATA_DIR)

            scanned_here = len(raw_jobs)
            new_here = 0
            date_skipped_here = 0
            total_scanned += scanned_here

            for item in raw_jobs:
                ext_id = item["external_id"]
                if ext_id in known_ids:
                    # 기존 공고는 마감일 등만 가볍게 갱신
                    item["source"] = source_id
                    item["source_name"] = source_name
                    database.insert_or_update_job(item)
                    continue

                # 1. 수집 단계 날짜 필터:
                #    - 이미 마감된 공고(deadline < 오늘) 즉시 제외
                #    - 등록일(posted_at)이 최근 max_age_days(기본 7일) 초과 시 즉시 제외
                skip_date, _ = should_skip_by_date(
                    item.get("posted_at", ""),
                    item.get("deadline", ""),
                    max_age_days=max_age_days,
                )
                if skip_date:
                    date_skipped_here += 1
                    continue

                # 2. 신규 공고 규칙 기반 조건 매칭 (조건 불일치 공고는 DB에 저장하지 않고 즉시 스킵)
                eval_res = evaluate_job(
                    {**item, "source_name": source_name}, criteria
                )
                if eval_res.get("hard_excluded"):
                    continue

                item["source"] = source_id
                item["source_name"] = source_name
                item["ai_score"] = eval_res["score"]
                item["ai_reason"] = eval_res["reason"]
                item["ai_tags"] = eval_res["tags"]
                item["status"] = "new"

                if database.insert_or_update_job(item):
                    new_here += 1
                    known_ids.add(ext_id)

            auth_note = ""
            if src.get("requires_login"):
                auth_note = (
                    " (로그인 세션 적용)"
                    if src.get("has_auth_session")
                    else " (비로그인 수집 - 세션 연결 권장)"
                )
            skip_note = (
                f" / 날짜기준 스킵 {date_skipped_here}건" if date_skipped_here else ""
            )
            if scanned_here == 0:
                status_msg = (
                    "수집 실패: 공고 0건 감지 "
                    "(사이트 차단·URL 오류·로그인 세션 만료 여부를 확인하세요)"
                )
            else:
                status_msg = f"정상 ({scanned_here}건 스캔 / 신규 {new_here}건{skip_note}){auth_note}"
            database.update_source_run_status(source_id, status_msg)
            messages.append(f"[{source_name}] {status_msg}")
            new_saved += new_here
        except Exception as exc:
            err_msg = f"수집 실패 (오류): {type(exc).__name__} - {exc}"
            database.update_source_run_status(source_id, err_msg[:140])
            messages.append(f"[{source_name}] {err_msg[:140]}")

    summary_str = " | ".join(messages) if messages else "활성화된 사이트 없음"
    database.finish_run_log(
        run_id=run_id,
        total_scanned=total_scanned,
        new_saved=new_saved,
        expired_updated=expired_cnt,
        status="completed",
        summary=summary_str,
    )
    return {
        "run_id": run_id,
        "total_scanned": total_scanned,
        "new_saved": new_saved,
        "expired_updated": expired_cnt,
        "summary": summary_str,
    }


def interactive_login_and_save_session(
    source_id: str, timeout_seconds: int = 180
) -> Dict[str, Any]:
    """
    로그인이 필요한 사이트(잡코리아 등)를 위해 실제 브라우저 창(Headed)을 띄워
    사용자가 직접 로그인(캡차/소셜로그인 포함)을 마치면 쿠키/세션(storage_state)을
    data/{source_id}_auth.json에 저장합니다.
    """
    src = database.get_source(source_id)
    if not src:
        return {"ok": False, "message": f"사이트('{source_id}')를 찾을 수 없습니다."}

    login_url = src.get("login_url") or src["url"]
    auth_file = src.get("auth_state_file") or f"{source_id}_auth.json"
    auth_path = database.DATA_DIR / auth_file

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()
            page.goto(login_url)
            # 사용자가 브라우저 창에서 로그인 후 창을 닫거나 timeout까지 대기
            try:
                page.wait_for_event("close", timeout=timeout_seconds * 1000)
            except Exception:
                pass
            context.storage_state(path=str(auth_path))
            try:
                context.close()
                browser.close()
            except Exception:
                pass

        database.update_source_run_status(source_id, "로그인 세션 저장 완료")
        return {
            "ok": True,
            "message": f"'{src['name']}' 로그인 세션이 {auth_file}에 저장되었습니다. 이제 자동 수집 시 로그인 상태가 유지됩니다.",
            "auth_file": str(auth_path),
        }
    except Exception as exc:
        return {
            "ok": False,
            "message": f"브라우저 실행 중 오류 발생: {exc} (`playwright install chromium` 실행 여부를 확인하세요)",
        }
