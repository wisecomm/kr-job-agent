import json
import sqlite3
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "jobs.db"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                source_name TEXT NOT NULL,
                external_id TEXT NOT NULL,
                title TEXT NOT NULL,
                company TEXT NOT NULL DEFAULT '',
                location TEXT NOT NULL DEFAULT '',
                employment_type TEXT NOT NULL DEFAULT '',
                salary TEXT NOT NULL DEFAULT '',
                deadline TEXT NOT NULL DEFAULT '',
                posted_at TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                ai_score INTEGER NOT NULL DEFAULT 0,
                ai_reason TEXT NOT NULL DEFAULT '',
                ai_tags TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'new',
                memo TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(source, external_id)
            );

            CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
            CREATE INDEX IF NOT EXISTS idx_jobs_score ON jobs(ai_score DESC);
            CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source);

            CREATE TABLE IF NOT EXISTS criteria (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                include_keywords TEXT NOT NULL DEFAULT '',
                exclude_keywords TEXT NOT NULL DEFAULT '',
                preferred_locations TEXT NOT NULL DEFAULT '서울, 경기, 인천',
                preferred_career TEXT NOT NULL DEFAULT '무관',
                min_score INTEGER NOT NULL DEFAULT 60,
                max_age_days INTEGER NOT NULL DEFAULT 7,
                ai_prompt TEXT NOT NULL DEFAULT '',
                schedule_minutes INTEGER NOT NULL DEFAULT 180,
                auto_schedule_enabled INTEGER NOT NULL DEFAULT 1,
                gemini_model TEXT NOT NULL DEFAULT 'gemini-2.5-flash',
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sources (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                scraper_type TEXT NOT NULL DEFAULT 'generic',
                requires_login INTEGER NOT NULL DEFAULT 0,
                login_url TEXT NOT NULL DEFAULT '',
                auth_state_file TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                max_items INTEGER NOT NULL DEFAULT 100,
                last_run_at TEXT NOT NULL DEFAULT '',
                last_status TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS run_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL DEFAULT '',
                trigger_type TEXT NOT NULL DEFAULT 'manual',
                total_scanned INTEGER NOT NULL DEFAULT 0,
                new_saved INTEGER NOT NULL DEFAULT 0,
                expired_updated INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'running',
                summary TEXT NOT NULL DEFAULT ''
            );
            """
        )

        now = datetime.now().isoformat(timespec="seconds")

        # 기존 DB에 max_age_days, preferred_career 컬럼이 없을 경우 자동 마이그레이션
        try:
            conn.execute(
                "ALTER TABLE criteria ADD COLUMN max_age_days INTEGER NOT NULL DEFAULT 7"
            )
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute(
                "ALTER TABLE criteria ADD COLUMN preferred_career TEXT NOT NULL DEFAULT '무관'"
            )
        except sqlite3.OperationalError:
            pass

        # 이전 기본값('서울, 경기, 인천, 세종, 전국')에서 '전국'을 정리
        conn.execute(
            """
            UPDATE criteria
               SET preferred_locations = '서울, 경기, 인천'
             WHERE preferred_locations = '서울, 경기, 인천, 세종, 전국'
            """
        )

        # 기존 수집 공고의 employment_type에 섞인 '학력무관' 제거 ('경력무관'과 혼동 방지)
        conn.execute(
            """
            UPDATE jobs
               SET employment_type = TRIM(REPLACE(employment_type, '학력무관', ''))
             WHERE employment_type LIKE '%학력무관%'
            """
        )

        # 사이트 표시 이름을 깔끔하게 '나라일터', '잡코리아'로 정리
        conn.execute("UPDATE sources SET name = '나라일터' WHERE id = 'gojobs'")
        conn.execute("UPDATE sources SET name = '잡코리아' WHERE id = 'jobkorea'")
        conn.execute("UPDATE jobs SET source_name = '나라일터' WHERE source = 'gojobs'")
        conn.execute("UPDATE jobs SET source_name = '잡코리아' WHERE source = 'jobkorea'")

        # 기본 매칭 조건 시드
        cur = conn.execute("SELECT COUNT(*) as cnt FROM criteria")
        if cur.fetchone()["cnt"] == 0:
            conn.execute(
                """
                INSERT INTO criteria (
                    id, include_keywords, exclude_keywords, preferred_locations,
                    min_score, max_age_days, ai_prompt, schedule_minutes,
                    auto_schedule_enabled, gemini_model, updated_at
                ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "전산, 데이터, 기획, 행정, IT, 개발, 분석, 연구",
                    "",
                    "서울, 경기, 인천",
                    0,
                    7,
                    "",
                    180,
                    1,
                    "",
                    now,
                ),
            )

        # 기본 구직 사이트 2곳 시드 (최초 1회만 시드하여 사용자가 삭제한 사이트가 재시작 시 되살아나지 않도록 함)
        sources_seeded_flag = DATA_DIR / ".sources_seeded_v1.flag"
        src_cnt = conn.execute("SELECT COUNT(*) as cnt FROM sources").fetchone()["cnt"]
        if not sources_seeded_flag.exists() and src_cnt == 0:
            default_sources = [
                {
                    "id": "gojobs",
                    "name": "나라일터",
                    "url": "https://www.gojobs.go.kr/apmList.do?menuNo=401&mngrMenuYn=N&selMenuNo=400&upperMenuNo=",
                    "scraper_type": "gojobs",
                    "requires_login": 0,
                    "login_url": "",
                    "auth_state_file": "",
                    "enabled": 1,
                    "max_items": 100,
                },
                {
                    "id": "jobkorea",
                    "name": "잡코리아",
                    "url": "https://www.jobkorea.co.kr/recruit/joblist?menucode=local&localorder=1",
                    "scraper_type": "jobkorea",
                    "requires_login": 1,
                    "login_url": "https://www.jobkorea.co.kr/Login/Login_Tot.asp",
                    "auth_state_file": "jobkorea_auth.json",
                    "enabled": 1,
                    "max_items": 100,
                },
            ]
            for src in default_sources:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO sources (
                        id, name, url, scraper_type, requires_login, login_url,
                        auth_state_file, enabled, max_items, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        src["id"],
                        src["name"],
                        src["url"],
                        src["scraper_type"],
                        src["requires_login"],
                        src["login_url"],
                        src["auth_state_file"],
                        src["enabled"],
                        src["max_items"],
                        now,
                    ),
                )
        if not sources_seeded_flag.exists():
            try:
                sources_seeded_flag.write_text(now, encoding="utf-8")
            except Exception:
                pass
    recalculate_existing_jobs()


def get_criteria() -> Dict[str, Any]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM criteria WHERE id = 1").fetchone()
        return dict(row) if row else {}


def update_criteria(data: Dict[str, Any]) -> Dict[str, Any]:
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE criteria
               SET include_keywords = ?,
                   exclude_keywords = ?,
                   preferred_locations = ?,
                   preferred_career = ?,
                   min_score = ?,
                   max_age_days = ?,
                   ai_prompt = ?,
                   schedule_minutes = ?,
                   auto_schedule_enabled = ?,
                   gemini_model = ?,
                   updated_at = ?
             WHERE id = 1
            """,
            (
                data.get("include_keywords", ""),
                data.get("exclude_keywords", ""),
                data.get("preferred_locations", ""),
                data.get("preferred_career", "무관"),
                int(data.get("min_score", 60)),
                int(data.get("max_age_days", 7)),
                data.get("ai_prompt", ""),
                int(data.get("schedule_minutes", 180)),
                1 if data.get("auto_schedule_enabled", True) else 0,
                data.get("gemini_model", "gemini-2.5-flash"),
                now,
            ),
        )
    return get_criteria()


def list_sources(only_enabled: bool = False) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        query = "SELECT * FROM sources"
        if only_enabled:
            query += " WHERE enabled = 1"
        query += " ORDER BY created_at ASC"
        rows = conn.execute(query).fetchall()
        result = []
        for r in rows:
            item = dict(r)
            auth_file = item.get("auth_state_file", "")
            item["has_auth_session"] = bool(
                auth_file and (DATA_DIR / auth_file).exists()
            )
            result.append(item)
        return result


def get_source(source_id: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM sources WHERE id = ?", (source_id,)
        ).fetchone()
        if not row:
            return None
        item = dict(row)
        auth_file = item.get("auth_state_file", "")
        item["has_auth_session"] = bool(
            auth_file and (DATA_DIR / auth_file).exists()
        )
        return item


def upsert_source(data: Dict[str, Any]) -> Dict[str, Any]:
    now = datetime.now().isoformat(timespec="seconds")
    source_id = data["id"].strip().lower().replace(" ", "_")
    url_str = data["url"].strip()
    scraper_type = data.get("scraper_type") or "generic"
    if scraper_type == "generic":
        if source_id == "gojobs" or "gojobs.go.kr" in url_str:
            scraper_type = "gojobs"
        elif source_id == "jobkorea" or "jobkorea.co.kr" in url_str:
            scraper_type = "jobkorea"
    requires_login = 1 if data.get("requires_login") else 0
    auth_file = data.get("auth_state_file") or (
        f"{source_id}_auth.json" if requires_login else ""
    )
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO sources (
                id, name, url, scraper_type, requires_login, login_url,
                auth_state_file, enabled, max_items, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                url = excluded.url,
                scraper_type = excluded.scraper_type,
                requires_login = excluded.requires_login,
                login_url = excluded.login_url,
                auth_state_file = excluded.auth_state_file,
                enabled = excluded.enabled,
                max_items = excluded.max_items
            """,
            (
                source_id,
                data["name"].strip(),
                url_str,
                scraper_type,
                requires_login,
                data.get("login_url", "").strip(),
                auth_file,
                1 if data.get("enabled", True) else 0,
                int(data.get("max_items", 100)),
                now,
            ),
        )
    return get_source(source_id)  # type: ignore


def delete_source(source_id: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM jobs WHERE source = ?", (source_id,))
        conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))


def update_source_settings(
    source_id: str,
    max_items: Optional[int] = None,
    enabled: Optional[bool] = None,
) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        if max_items is not None:
            conn.execute(
                "UPDATE sources SET max_items = ? WHERE id = ?",
                (max(1, min(500, int(max_items))), source_id),
            )
        if enabled is not None:
            conn.execute(
                "UPDATE sources SET enabled = ? WHERE id = ?",
                (1 if enabled else 0, source_id),
            )
    return get_source(source_id)


def update_source_run_status(source_id: str, status_msg: str) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        conn.execute(
            "UPDATE sources SET last_run_at = ?, last_status = ? WHERE id = ?",
            (now, status_msg, source_id),
        )


def job_exists(source: str, external_id: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM jobs WHERE source = ? AND external_id = ?",
            (source, external_id),
        ).fetchone()
        return row is not None


def insert_or_update_job(job: Dict[str, Any]) -> bool:
    """
    Returns True if a new job was inserted, False if existing job was updated.
    Preserves user's status and memo if the job already exists.
    """
    now = datetime.now().isoformat(timespec="seconds")
    tags_json = (
        json.dumps(job.get("ai_tags", []), ensure_ascii=False)
        if isinstance(job.get("ai_tags"), list)
        else str(job.get("ai_tags", "[]"))
    )
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id, status FROM jobs WHERE source = ? AND external_id = ?",
            (job["source"], job["external_id"]),
        ).fetchone()

        if existing:
            conn.execute(
                """
                UPDATE jobs
                   SET deadline = COALESCE(NULLIF(?, ''), deadline),
                       updated_at = ?
                 WHERE id = ?
                """,
                (job.get("deadline", ""), now, existing["id"]),
            )
            return False

        conn.execute(
            """
            INSERT INTO jobs (
                source, source_name, external_id, title, company, location,
                employment_type, salary, deadline, posted_at, url, content,
                ai_score, ai_reason, ai_tags, status, memo, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job["source"],
                job.get("source_name", job["source"]),
                job["external_id"],
                job["title"],
                job.get("company", ""),
                job.get("location", ""),
                job.get("employment_type", ""),
                job.get("salary", ""),
                job.get("deadline", ""),
                job.get("posted_at", ""),
                job["url"],
                job.get("content", ""),
                int(job.get("ai_score", 0)),
                job.get("ai_reason", ""),
                tags_json,
                job.get("status", "new"),
                job.get("memo", ""),
                now,
                now,
            ),
        )
        return True


def mark_expired_jobs() -> int:
    """Marks jobs whose YYYY-MM-DD deadline is strictly before today as 'expired' (unless applied/rejected)."""
    today_str = date.today().isoformat()
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        cur = conn.execute(
            """
            UPDATE jobs
               SET status = 'expired',
                   updated_at = ?
             WHERE status IN ('new', 'saved')
               AND deadline GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*'
               AND substr(deadline, 1, 10) < ?
            """,
            (now, today_str),
        )
        return cur.rowcount


def _matches_career_filter(job: Dict[str, Any], career: Optional[str]) -> bool:
    if not career or career == "all":
        return True
    raw = (
        f"{job.get('employment_type', '')} {job.get('title', '')} "
        f"{(job.get('content', '') or '')[:300]}"
    )
    cleaned = (
        raw.replace("학력무관", "")
        .replace("성별무관", "")
        .replace("연령무관", "")
        .replace("전공무관", "")
        .replace("경력경쟁채용시험", "")
        .replace("경력경쟁채용", "")
        .replace("경력경쟁", "")
        .replace("전문경력관", "")
    )
    has_newcomer = "신입" in cleaned or "인턴" in cleaned
    has_career_free = (
        "경력무관" in cleaned
        or "경력 무관" in cleaned
        or job.get("source") == "gojobs"
    )
    has_exp = "경력" in cleaned.replace("경력무관", "").replace("경력 무관", "")

    if career == "신입":
        # 신입 명시 공고 또는 경력무관/공공채용 공고 (단, '신입' 없이 '경력N년'만 있는 순수 경력직은 제외)
        return has_newcomer or (has_career_free and not has_exp)
    if career == "신입전용":
        return has_newcomer
    if career == "경력":
        return has_exp or job.get("source") == "gojobs"
    if career == "무관":
        return has_career_free
    return True


def reset_jobs_db() -> Dict[str, int]:
    """
    수집된 모든 일자리 목록(jobs), 실행 이력(run_logs), 사이트별 최근 수집 상태를 모두 초기화합니다.
    (사용자의 맞춤 조건 설정 criteria, 등록된 사이트 설정 sources.max_items, 로그인 세션 파일은 DB 값 그대로 유지됩니다.)
    """
    with get_conn() as conn:
        deleted_jobs = conn.execute("DELETE FROM jobs").rowcount
        deleted_logs = conn.execute("DELETE FROM run_logs").rowcount
        try:
            conn.execute(
                "DELETE FROM sqlite_sequence WHERE name IN ('jobs', 'run_logs')"
            )
        except sqlite3.OperationalError:
            pass
        conn.execute("UPDATE sources SET last_run_at = '', last_status = ''")
    return {"deleted_jobs": deleted_jobs, "deleted_logs": deleted_logs}


def _ensure_rule_migration() -> None:
    """1회성 전체 DB 초기화 및 초기 시드(20->100) 1회 마이그레이션"""
    reset_flag = DATA_DIR / ".reset_20261029_v1.flag"
    if not reset_flag.exists():
        reset_jobs_db()
        with get_conn() as conn:
            conn.execute("UPDATE sources SET max_items = 100 WHERE max_items = 20")
        try:
            reset_flag.write_text(datetime.now().isoformat(), encoding="utf-8")
        except Exception:
            pass
        return

    import re as _re

    today_str = date.today().isoformat()
    need_recalc = False
    with get_conn() as conn:
        # 1) 나라일터(gojobs) 공고 중 등록일/마감일이 뒤바뀌어 잘못 마감(expired) 처리된 레코드 복구
        gojobs_rows = conn.execute(
            "SELECT id, posted_at, deadline, content, status FROM jobs WHERE source = 'gojobs'"
        ).fetchall()
        for r in gojobs_rows:
            text_blob = f"{r['posted_at']} {r['deadline']} {r['content']}"
            found = _re.findall(r"(20\d{2})[-./](\d{1,2})[-./](\d{1,2})", text_blob)
            iso_dates = [f"{int(y):04d}-{int(m):02d}-{int(d):02d}" for y, m, d in found]
            if iso_dates:
                real_posted = min(iso_dates)
                real_deadline = max(iso_dates)
                if real_posted != r["posted_at"] or real_deadline != r["deadline"]:
                    new_st = r["status"]
                    if new_st == "expired" and real_deadline >= today_str:
                        new_st = "new"
                    conn.execute(
                        "UPDATE jobs SET posted_at = ?, deadline = ?, status = ? WHERE id = ?",
                        (real_posted, real_deadline, new_st, r["id"]),
                    )
                    need_recalc = True

        # 2) 잡코리아 상세 페이지(#container 상단 메뉴 'AI·개발·데이터' 등) 오염 텍스트 제거 및 재계산
        jk_polluted = conn.execute(
            "SELECT id, content FROM jobs WHERE source = 'jobkorea' AND instr(content, char(10)) > 0"
        ).fetchall()
        for r in jk_polluted:
            clean_first_line = (r["content"] or "").split("\n", 1)[0].strip()
            conn.execute(
                "UPDATE jobs SET content = ? WHERE id = ?",
                (clean_first_line, r["id"]),
            )
            need_recalc = True

        # 3) 조건 불일치(filtered 또는 ai_score=0) 공고는 DB에 남기지 않고 즉시 삭제
        chk = conn.execute(
            """
            SELECT 1 FROM jobs
             WHERE status = 'filtered'
                OR ai_score = 0
                OR (source = 'gojobs' AND ai_reason LIKE '%경력직 전용%')
             LIMIT 1
            """
        ).fetchone()
        if chk:
            need_recalc = True
            conn.execute(
                "DELETE FROM jobs WHERE status = 'filtered' OR ai_score = 0"
            )
    if need_recalc:
        recalculate_existing_jobs()


def list_jobs(
    status: Optional[str] = None,
    source: Optional[str] = None,
    career: Optional[str] = None,
    min_score: int = 0,
    search: Optional[str] = None,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    _ensure_rule_migration()
    clauses = ["ai_score >= ?"]
    params: List[Any] = [min_score]

    if status and status != "all":
        clauses.append("status = ?")
        params.append(status)
    elif status == "all":
        # '활성 전체' 기본 보기에서는 확인완료(rejected)된 공고는 숨김
        clauses.append("status != 'rejected'")

    if source and source != "all":
        clauses.append("source = ?")
        params.append(source)

    if search and search.strip():
        q = f"%{search.strip()}%"
        clauses.append(
            "(title LIKE ? OR company LIKE ? OR location LIKE ? OR content LIKE ? OR ai_reason LIKE ?)"
        )
        params.extend([q, q, q, q, q])

    where_sql = " AND ".join(clauses)
    query = f"""
        SELECT * FROM jobs
         WHERE {where_sql}
         ORDER BY created_at DESC, id DESC
         LIMIT ?
    """
    params.append(limit * 2 if (career and career != "all") else limit)

    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            if not _matches_career_filter(d, career):
                continue
            try:
                d["ai_tags"] = json.loads(d.get("ai_tags") or "[]")
            except Exception:
                d["ai_tags"] = []
            results.append(d)
            if len(results) >= limit:
                break
        return results


def recalculate_existing_jobs() -> int:
    """
    사용자가 [내 맞춤 조건 설정]을 변경·저장했을 때,
    이미 DB에 저장된 활성 공고들의 점수(ai_score)와 사유(ai_reason)를 새 조건 기준으로 즉시 재계산합니다.
    """
    from matcher import rule_based_evaluate

    criteria = get_criteria()
    now = datetime.now().isoformat(timespec="seconds")
    updated_count = 0
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status != 'expired'"
        ).fetchall()
        for r in rows:
            job = dict(r)
            res = rule_based_evaluate(job, criteria)
            if res.get("hard_excluded") and job["status"] in ("new", "filtered"):
                conn.execute("DELETE FROM jobs WHERE id = ?", (job["id"],))
                updated_count += 1
                continue
            tags_json = json.dumps(res.get("tags", []), ensure_ascii=False)
            conn.execute(
                """
                UPDATE jobs
                   SET ai_score = ?,
                       ai_reason = ?,
                       ai_tags = ?,
                       updated_at = ?
                 WHERE id = ?
                """,
                (
                    int(res["score"]),
                    res["reason"],
                    tags_json,
                    now,
                    job["id"],
                ),
            )
            updated_count += 1
    return updated_count


def update_job_status_or_memo(
    job_id: int, status: Optional[str] = None, memo: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        if status is not None and memo is not None:
            conn.execute(
                "UPDATE jobs SET status = ?, memo = ?, updated_at = ? WHERE id = ?",
                (status, memo, now, job_id),
            )
        elif status is not None:
            conn.execute(
                "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, job_id),
            )
        elif memo is not None:
            conn.execute(
                "UPDATE jobs SET memo = ?, updated_at = ? WHERE id = ?",
                (memo, now, job_id),
            )
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["ai_tags"] = json.loads(d.get("ai_tags") or "[]")
        except Exception:
            d["ai_tags"] = []
        return d


def get_stats() -> Dict[str, Any]:
    _ensure_rule_migration()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM jobs GROUP BY status"
        ).fetchall()
        counts = {r["status"]: r["cnt"] for r in rows}
        high_fit = conn.execute(
            "SELECT COUNT(*) as cnt FROM jobs WHERE ai_score >= 75 AND status NOT IN ('rejected', 'expired')"
        ).fetchone()["cnt"]
        last_run = conn.execute(
            "SELECT * FROM run_logs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return {
            "total_active": counts.get("new", 0)
            + counts.get("saved", 0)
            + counts.get("applied", 0),
            "new": counts.get("new", 0),
            "saved": counts.get("saved", 0),
            "applied": counts.get("applied", 0),
            "rejected": counts.get("rejected", 0),
            "expired": counts.get("expired", 0),
            "high_fit": high_fit,
            "last_run": dict(last_run) if last_run else None,
        }


def create_run_log(trigger_type: str = "manual") -> int:
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO run_logs (started_at, trigger_type, status) VALUES (?, ?, 'running')",
            (now, trigger_type),
        )
        return int(cur.lastrowid)  # type: ignore


def finish_run_log(
    run_id: int,
    total_scanned: int,
    new_saved: int,
    expired_updated: int,
    status: str = "completed",
    summary: str = "",
) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE run_logs
               SET finished_at = ?,
                   total_scanned = ?,
                   new_saved = ?,
                   expired_updated = ?,
                   status = ?,
                   summary = ?
             WHERE id = ?
            """,
            (
                now,
                total_scanned,
                new_saved,
                expired_updated,
                status,
                summary,
                run_id,
            ),
        )


def list_run_logs(limit: int = 15) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM run_logs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
