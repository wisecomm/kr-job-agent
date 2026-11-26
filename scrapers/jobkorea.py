import hashlib
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Set
from urllib.parse import urljoin
import httpx
from bs4 import BeautifulSoup
from scrapers.date_filter import normalize_relative_posted_date

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


def _normalize_jk_deadline(raw: str) -> str:
    """
    잡코리아 마감일 포맷('~11/15(금)', '상시채용', '오늘마감', '2026.11.15')을 표준화
    """
    text = (raw or "").strip()
    if not text:
        return ""
    today = date.today()
    if "오늘" in text or "당일" in text:
        return today.isoformat()
    if "내일" in text:
        return (today + timedelta(days=1)).isoformat()

    m_full = re.search(r"(20\d{2})[-./](\d{1,2})[-./](\d{1,2})", text)
    if m_full:
        return f"{int(m_full.group(1)):04d}-{int(m_full.group(2)):02d}-{int(m_full.group(3)):02d}"

    m_mmdd = re.search(r"(\d{1,2})/(\d{1,2})", text)
    if m_mmdd:
        month, day = int(m_mmdd.group(1)), int(m_mmdd.group(2))
        year = today.year
        try:
            candidate = date(year, month, day)
            if candidate < today - timedelta(days=60):
                candidate = date(year + 1, month, day)
            return candidate.isoformat()
        except ValueError:
            pass
    return text


def scrape_jobkorea(
    source_cfg: Dict[str, Any],
    known_ids: Set[str],
    data_dir: Path,
    max_age_days: int = 7,
) -> List[Dict[str, Any]]:
    """
    잡코리아 (https://www.jobkorea.co.kr/recruit/joblist?menucode=local&localorder=1)
    로그인이 필요한 구직사이트 수집기:
    - data/jobkorea_auth.json 세션 파일이 있으면 Playwright storage_state에 자동 주입하여 로그인 상태로 수집
    - 상세 공고(GI_Read) 및 내부 iframe 요약 텍스트 추출
    """
    list_url = source_cfg["url"]
    max_items = int(source_cfg.get("max_items", 100))
    auth_file_name = source_cfg.get("auth_state_file") or "jobkorea_auth.json"
    auth_path = data_dir / auth_file_name

    html_text = ""
    items: List[Dict[str, Any]] = []

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx_kwargs: Dict[str, Any] = {"user_agent": USER_AGENT}
            if auth_path.exists():
                ctx_kwargs["storage_state"] = str(auth_path)

            context = browser.new_context(**ctx_kwargs)
            page = context.new_page()
            page.goto(list_url, wait_until="domcontentloaded", timeout=35000)
            page.wait_for_timeout(2000)

            seen_ext_ids: Set[str] = set()
            max_pages = max(1, min(15, (max_items + 39) // 40))
            for page_idx in range(1, max_pages + 1):
                if page_idx > 1:
                    moved = page.evaluate(
                        """(nextPage) => {
                            const link = document.querySelector(`.tplPagination a[data-page="${nextPage}"]`)
                                || Array.from(document.querySelectorAll('.tplPagination a, .pagination a')).find(a => (a.textContent || '').trim() === String(nextPage));
                            if (link) {
                                link.click();
                                return true;
                            }
                            return false;
                        }""",
                        page_idx,
                    )
                    if not moved:
                        sep = "&" if "?" in list_url else "?"
                        page.goto(
                            f"{list_url}{sep}Page_No={page_idx}#anchorGICnt_{page_idx}",
                            wait_until="domcontentloaded",
                            timeout=30000,
                        )
                    page.wait_for_timeout(1800)

                page_items = _parse_jobkorea_html(page.content(), list_url, max_items)
                added_in_page = 0
                for it in page_items:
                    if it["external_id"] not in seen_ext_ids:
                        seen_ext_ids.add(it["external_id"])
                        items.append(it)
                        added_in_page += 1
                        if len(items) >= max_items:
                            break
                if added_in_page == 0 or len(items) >= max_items:
                    break

            # 주의: 상세 페이지(#container 등) 전체 텍스트를 긁어오면 사이트 상단 메뉴('AI·개발·데이터')나
            # 기업소개/복리후생('공간개발', '자기개발비', '신약개발') 때문에 세일즈·제약 공고가 오탐되므로,
            # 목록 행에 명시된 순수 직무 분류 태그(p.dsc)와 공고 제목만으로 정확하게 필터링합니다.
            context.close()
            browser.close()
    except Exception:
        # Playwright 미설치 또는 브라우저 실행 불가 환경일 경우 httpx 폴백
        try:
            with httpx.Client(
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
                timeout=20.0,
            ) as client:
                resp = client.get(list_url)
                if resp.status_code == 200:
                    items = _parse_jobkorea_html(resp.text, list_url, max_items)
        except Exception:
            pass

    return items[:max_items]


def _parse_jobkorea_html(
    html_text: str, base_url: str, max_items: int
) -> List[Dict[str, Any]]:
    if not html_text:
        return []
    soup = BeautifulSoup(html_text, "lxml")
    results: List[Dict[str, Any]] = []
    seen_ids: Set[str] = set()

    # 1차: 잡코리아 채용정보 리스트 표준 행 (tr.devloopArea 또는 .tplJobList tbody tr)
    rows = soup.select("tr.devloopArea, .tplJobList tbody tr, .list-default li.list-post")
    for row in rows:
        link_tag = row.select_one(
            "td.tplTit a.link, .titBx a, a[href*='GI_Read'], a.title"
        )
        if not link_tag:
            continue

        title = re.sub(r"\s+", " ", link_tag.get_text(" ", strip=True))
        href = link_tag.get("href", "")
        if not title or not href:
            continue

        full_url = urljoin("https://www.jobkorea.co.kr", href)
        gno_match = re.search(r"GI_Read/(\d+)", full_url) or re.search(
            r"Gno=(\d+)", full_url
        )
        gno = (
            gno_match.group(1)
            if gno_match
            else row.get("data-gno-no")
            or hashlib.md5(full_url.encode("utf-8")).hexdigest()[:10]
        )
        ext_id = f"jobkorea_{gno}"
        if ext_id in seen_ids:
            continue
        seen_ids.add(ext_id)

        comp_tag = row.select_one("td.tplCo a, .coName a, a.name, .post-list-corp a")
        company = (
            re.sub(r"\s+", " ", comp_tag.get_text(" ", strip=True))
            if comp_tag
            else "잡코리아 채용기업"
        )

        etc_spans = [
            re.sub(r"\s+", " ", sp.get_text(" ", strip=True))
            for sp in row.select("p.etc span.cell, .option span, .etc span")
            if sp.get_text(strip=True)
        ]
        dsc_tag = row.select_one("p.dsc, .dsc")
        dsc_text = (
            re.sub(r"\s+", " ", dsc_tag.get_text(" ", strip=True)) if dsc_tag else ""
        )

        location = ""
        emp_type = ""
        for sp in etc_spans:
            if any(
                loc in sp
                for loc in (
                    "서울",
                    "경기",
                    "인천",
                    "부산",
                    "대구",
                    "대전",
                    "광주",
                    "울산",
                    "세종",
                    "강원",
                    "충북",
                    "충남",
                    "전북",
                    "전남",
                    "경북",
                    "경남",
                    "제주",
                )
            ):
                location = sp
            elif any(
                k in sp for k in ("신입", "경력", "경력무관", "정규직", "계약직", "인턴")
            ) and sp not in ("학력무관", "성별무관", "연령무관", "전공무관"):
                emp_type = f"{emp_type} {sp}".strip()

        date_tag = row.select_one("td.odd span.date, .date")
        time_tag = row.select_one("td.odd span.time, .regDate, span.time")
        deadline = _normalize_jk_deadline(
            date_tag.get_text(" ", strip=True) if date_tag else ""
        )
        posted_at = normalize_relative_posted_date(
            time_tag.get_text(" ", strip=True) if time_tag else ""
        )

        summary = " | ".join(filter(None, [" / ".join(etc_spans), dsc_text]))
        results.append(
            {
                "external_id": ext_id,
                "title": title,
                "company": company,
                "location": location,
                "employment_type": emp_type,
                "posted_at": posted_at,
                "deadline": deadline,
                "url": full_url,
                "content": summary,
            }
        )
        if len(results) >= max_items:
            return results

    # 2차 폴백: 페이지 내 모든 GI_Read 링크 탐색 (잡코리아 UI 개편 대응)
    if not results:
        for a_tag in soup.select("a[href*='GI_Read']"):
            title = re.sub(r"\s+", " ", a_tag.get_text(" ", strip=True))
            if len(title) < 4:
                continue
            href = a_tag.get("href", "")
            full_url = urljoin("https://www.jobkorea.co.kr", href)
            m = re.search(r"GI_Read/(\d+)", full_url)
            if not m:
                continue
            ext_id = f"jobkorea_{m.group(1)}"
            if ext_id in seen_ids:
                continue
            seen_ids.add(ext_id)
            parent_text = (
                re.sub(r"\s+", " ", a_tag.parent.get_text(" ", strip=True))
                if a_tag.parent
                else title
            )
            results.append(
                {
                    "external_id": ext_id,
                    "title": title,
                    "company": "잡코리아 채용공고",
                    "location": "",
                    "employment_type": "",
                    "posted_at": "",
                    "deadline": "",
                    "url": full_url,
                    "content": parent_text[:400],
                }
            )
            if len(results) >= max_items:
                break

    return results
