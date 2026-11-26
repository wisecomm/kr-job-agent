import hashlib
import re
from pathlib import Path
from typing import Any, Dict, List, Set
from urllib.parse import urljoin, urlparse
import httpx
from bs4 import BeautifulSoup
from scrapers.date_filter import normalize_relative_posted_date
from scrapers.jobkorea import _normalize_jk_deadline

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


def scrape_generic_portal(
    source_cfg: Dict[str, Any], known_ids: Set[str], data_dir: Path
) -> List[Dict[str, Any]]:
    """
    추후 새로운 구직 사이트(사람인, 원티드, 워크넷/고용24, 알리오 등)를 추가했을 때 작동하는 범용 수집기:
    - requires_login 여부에 따라 세션 파일(storage_state)을 자동 적용하여 Playwright로 렌더링
    - 테이블 행 또는 채용 상세 링크 패턴(recruit, job, view, detail, gi_read, relay 등)을 자동 감지해 추출
    """
    list_url = source_cfg["url"]
    source_id = source_cfg["id"]
    max_items = int(source_cfg.get("max_items", 100))
    auth_file = source_cfg.get("auth_state_file", "")
    auth_path = data_dir / auth_file if auth_file else None

    html_text = ""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx_kwargs: Dict[str, Any] = {"user_agent": USER_AGENT}
            if auth_path and auth_path.exists():
                ctx_kwargs["storage_state"] = str(auth_path)
            context = browser.new_context(**ctx_kwargs)
            page = context.new_page()
            page.goto(list_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)
            html_text = page.content()
            context.close()
            browser.close()
    except Exception:
        try:
            with httpx.Client(
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
                timeout=20.0,
                verify=False,
            ) as client:
                resp = client.get(list_url)
                if resp.status_code == 200:
                    html_text = resp.text
        except Exception:
            pass

    if not html_text:
        return []

    soup = BeautifulSoup(html_text, "lxml")
    results: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    domain = urlparse(list_url).netloc

    # 사람인(saramin.co.kr) URL인 경우 사람인 전용 정밀 파서 우선 실행
    if "saramin.co.kr" in domain:
        saramin_items = _parse_saramin_html(soup, list_url, source_id, max_items)
        if saramin_items:
            return saramin_items

    for a_tag in soup.find_all("a", href=True):
        title = re.sub(r"\s+", " ", a_tag.get_text(" ", strip=True))
        href = a_tag.get("href", "")
        if len(title) < 6 or len(title) > 140:
            continue
        if any(
            skip in title
            for skip in ("로그인", "회원가입", "고객센터", "개인정보처리방침", "이용약관", "공지사항", "자주묻는질문")
        ):
            continue

        full_url = urljoin(list_url, href)
        row_container = a_tag.find_parent(["tr", "li", "article", "div"])
        context_text = (
            re.sub(r"\s+", " ", row_container.get_text(" ", strip=True))
            if row_container
            else title
        )

        # 채용 공고성 링크인지 휴리스틱 판별
        url_lower = full_url.lower()
        is_job_like = any(
            kw in url_lower
            for kw in (
                "recruit",
                "job",
                "view",
                "detail",
                "read",
                "notice",
                "emp",
                "apm",
                "idx=",
                "seq=",
                "no=",
                "id=",
            )
        ) or bool(re.search(r"20\d{2}[-./]\d{1,2}[-./]\d{1,2}|채용|모집|공고|경력|신입", context_text))

        if not is_job_like:
            continue

        uid = hashlib.md5(f"{source_id}:{full_url}:{title}".encode("utf-8")).hexdigest()[:12]
        ext_id = f"{source_id}_{uid}"
        if ext_id in seen:
            continue
        seen.add(ext_id)

        date_m = re.search(r"(20\d{2})[-./](\d{1,2})[-./](\d{1,2})", context_text)
        deadline = (
            f"{int(date_m.group(1)):04d}-{int(date_m.group(2)):02d}-{int(date_m.group(3)):02d}"
            if date_m
            else ""
        )

        results.append(
            {
                "external_id": ext_id,
                "title": title,
                "company": source_cfg.get("name", domain),
                "location": "",
                "employment_type": "",
                "posted_at": "",
                "deadline": deadline,
                "url": full_url,
                "content": context_text[:600],
            }
        )
        if len(results) >= max_items:
            break

    return results


def _parse_saramin_html(
    soup: BeautifulSoup, list_url: str, source_id: str, max_items: int
) -> List[Dict[str, Any]]:
    """
    사람인(saramin.co.kr) 검색/지역/추천 채용 목록 전용 파서
    (.item_recruit, .list_item, .box_item 등 대응)
    """
    items: List[Dict[str, Any]] = []
    seen: Set[str] = set()

    rows = soup.select(
        "div.item_recruit, div.list_item, tr[id^='rec-'], div.box_item"
    )
    for row in rows:
        a_tag = row.select_one(
            ".job_tit a, .notification_info .str_tit, a[href*='rec_idx=']"
        )
        if not a_tag:
            continue

        title = re.sub(r"\s+", " ", a_tag.get_text(" ", strip=True))
        href = a_tag.get("href", "")
        if not title or not href:
            continue

        full_url = urljoin("https://www.saramin.co.kr", href)
        rec_m = re.search(r"rec_idx=(\d+)", full_url) or re.search(
            r"(\d{7,})", str(row.get("value") or row.get("id") or "")
        )
        rec_idx = (
            rec_m.group(1)
            if rec_m
            else hashlib.md5(full_url.encode("utf-8")).hexdigest()[:10]
        )
        ext_id = f"{source_id}_{rec_idx}"
        if ext_id in seen:
            continue
        seen.add(ext_id)

        comp_tag = row.select_one(
            ".corp_name a, .company_nm a, .company_nm .str_tit, .col.company_nm"
        )
        company = (
            re.sub(r"\s+", " ", comp_tag.get_text(" ", strip=True))
            if comp_tag
            else "사람인 채용기업"
        )

        cond_spans = [
            re.sub(r"\s+", " ", sp.get_text(" ", strip=True))
            for sp in row.select(
                ".job_condition span, .recruit_condition li, .work_place, .career, .education"
            )
            if sp.get_text(strip=True)
        ]
        sector_tag = row.select_one(".job_sector")
        sector_text = (
            re.sub(r"\s+", " ", sector_tag.get_text(" ", strip=True))
            if sector_tag
            else ""
        )

        location = cond_spans[0] if len(cond_spans) >= 1 else ""
        emp_type = " · ".join(cond_spans[1:4]) if len(cond_spans) >= 2 else ""

        dl_tag = row.select_one(".job_date .date, .support_info .date")
        deadline = _normalize_jk_deadline(
            dl_tag.get_text(" ", strip=True) if dl_tag else ""
        )

        day_tag = row.select_one(".job_day, .support_info .deadlines")
        posted_at = normalize_relative_posted_date(
            day_tag.get_text(" ", strip=True) if day_tag else ""
        )

        summary = " | ".join(filter(None, [" / ".join(cond_spans), sector_text]))
        items.append(
            {
                "external_id": ext_id,
                "title": title,
                "company": company,
                "location": location,
                "employment_type": emp_type,
                "posted_at": posted_at,
                "deadline": deadline,
                "url": full_url,
                "content": summary[:600],
            }
        )
        if len(items) >= max_items:
            break

    return items
