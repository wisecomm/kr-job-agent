import hashlib
import re
from typing import Any, Dict, List, Set
from urllib.parse import urljoin
import httpx
from bs4 import BeautifulSoup
from scrapers.date_filter import should_skip_by_date

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


def _extract_all_dates(text: str) -> List[str]:
    """문자열 내의 모든 YYYY-MM-DD / YYYY.MM.DD 날짜를 찾아 YYYY-MM-DD 리스트로 반환합니다."""
    matches = re.findall(r"(20\d{2})[-./](\d{1,2})[-./](\d{1,2})", text or "")
    return [f"{int(y):04d}-{int(m):02d}-{int(d):02d}" for y, m, d in matches]


def _extract_empmnsn(tag: Any, row_html: str) -> str:
    for attr in ("onclick", "href", "data-id", "data-seq"):
        val = tag.get(attr, "") if tag else ""
        m = re.search(r"(?:fn_view|apmView|empmnsn)[('\"=\s]*(\d{4,})", str(val))
        if m:
            return m.group(1)
    m2 = re.search(r"(\d{5,})", row_html)
    if m2:
        return m2.group(1)
    return ""


def scrape_gojobs(
    source_cfg: Dict[str, Any], known_ids: Set[str], max_age_days: int = 7
) -> List[Dict[str, Any]]:
    """
    나라일터 (https://www.gojobs.go.kr/apmList.do?menuNo=401&mngrMenuYn=N&selMenuNo=400&upperMenuNo=)
    오픈된 구직사이트 수집기:
    1) httpx + BeautifulSoup으로 목록 테이블 파싱
    2) 동적 렌더링 필요 시 Playwright로 자동 폴백
    """
    list_url = source_cfg["url"]
    max_items = int(source_cfg.get("max_items", 100))
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": "https://www.gojobs.go.kr/",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
    }

    client = httpx.Client(headers=headers, follow_redirects=True, timeout=20.0, verify=False)
    items: List[Dict[str, Any]] = []
    seen_ext_ids: Set[str] = set()
    max_pages = max(1, min(50, (max_items + 9) // 10))
    base_form_data: Dict[str, str] = {
        "menuNo": "401",
        "mngrMenuYn": "N",
        "selMenuNo": "400",
        "upperMenuNo": "",
    }
    form_action_url = "https://www.gojobs.go.kr/apmList.do"

    for page_idx in range(1, max_pages + 1):
        try:
            if page_idx == 1:
                resp = client.get(list_url)
                if resp.status_code == 200 and resp.text:
                    soup_p1 = BeautifulSoup(resp.text, "lxml")
                    form = soup_p1.find("form")
                    if form:
                        act = form.get("action")
                        if act:
                            form_action_url = urljoin(list_url, str(act))
                        for inp in form.find_all("input"):
                            nm = inp.get("name")
                            if nm:
                                base_form_data[str(nm)] = str(inp.get("value", ""))
            else:
                payload = dict(base_form_data)
                payload["pageIndex"] = str(page_idx)
                payload["currPage"] = str(page_idx)
                payload["pageNo"] = str(page_idx)
                resp = client.post(form_action_url, data=payload)
                if resp.status_code != 200:
                    sep = "&" if "?" in list_url else "?"
                    resp = client.get(f"{list_url}{sep}pageIndex={page_idx}")

            if resp.status_code == 200:
                page_items = _parse_gojobs_list_html(
                    resp.text, list_url, max_items
                )
                if not page_items:
                    break
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
        except Exception:
            break

    # 정적 요청에서 테이블이 비어있거나 1페이지(10건) 이후 페이징이 안 된 경우 Playwright 헤드리스로 다중 페이지 수집
    if len(items) < max_items and len(items) <= 10 and max_pages > 1 or not items:
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(user_agent=USER_AGENT)
                page.goto(list_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(1200)

                for page_idx in range(1, max_pages + 1):
                    page_items = _parse_gojobs_list_html(
                        page.content(), list_url, max_items
                    )
                    added_in_page = 0
                    for it in page_items:
                        if it["external_id"] not in seen_ext_ids:
                            seen_ext_ids.add(it["external_id"])
                            items.append(it)
                            added_in_page += 1
                            if len(items) >= max_items:
                                break
                    if len(items) >= max_items or (page_idx > 1 and added_in_page == 0):
                        break

                    next_p = page_idx + 1
                    moved = page.evaluate(
                        """(nextPage) => {
                            const fnCandidates = ['fn_egov_link_page', 'fn_page', 'goPage', 'linkPage', 'fn_movePage', 'fn_search'];
                            for (const fnName of fnCandidates) {
                                if (typeof window[fnName] === 'function') {
                                    window[fnName](nextPage);
                                    return true;
                                }
                            }
                            const inp = document.querySelector('input[name="pageIndex"], input[name="currPage"], input[name="pageNo"]');
                            if (inp && inp.form) {
                                inp.value = String(nextPage);
                                inp.form.submit();
                                return true;
                            }
                            const links = Array.from(document.querySelectorAll('a, button'));
                            const target = links.find(el => {
                                const txt = (el.textContent || '').trim();
                                const onclick = el.getAttribute('onclick') || '';
                                const href = el.getAttribute('href') || '';
                                return txt === String(nextPage) || onclick.includes(`(${nextPage})`) || onclick.includes(`'${nextPage}'`) || href.includes(`(${nextPage})`);
                            });
                            if (target) {
                                target.click();
                                return true;
                            }
                            return false;
                        }""",
                        next_p,
                    )
                    if not moved:
                        break
                    page.wait_for_timeout(1200)

                browser.close()
        except Exception:
            pass

    results: List[Dict[str, Any]] = []
    for item in items[:max_items]:
        ext_id = item["external_id"]
        skip, _ = should_skip_by_date(
            item.get("posted_at", ""), item.get("deadline", ""), max_age_days
        )
        # 이미 DB에 저장된 공고이거나 날짜 조건(마감/7일 경과)에 걸린 공고는 상세 페이지 요청 생략
        if not skip and ext_id not in known_ids and item.get("empmnsn"):
            detail_info = _fetch_gojobs_detail(client, item["empmnsn"])
            if detail_info.get("content"):
                item["content"] = detail_info["content"]
            if detail_info.get("company"):
                item["company"] = detail_info["company"]
            if detail_info.get("location"):
                item["location"] = detail_info["location"]
            if detail_info.get("employment_type"):
                item["employment_type"] = detail_info["employment_type"]
            if detail_info.get("posted_at"):
                item["posted_at"] = detail_info["posted_at"]
            if detail_info.get("deadline"):
                item["deadline"] = detail_info["deadline"]
        results.append(item)

    client.close()
    return results


def _parse_gojobs_list_html(
    html_text: str, base_url: str, max_items: int
) -> List[Dict[str, Any]]:
    if not html_text:
        return []
    soup = BeautifulSoup(html_text, "lxml")
    rows = soup.select("table tbody tr")
    if not rows:
        rows = soup.select("tr")

    parsed: List[Dict[str, Any]] = []
    for tr in rows:
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue

        a_tag = tr.find("a")
        if not a_tag:
            continue

        title = re.sub(r"\s+", " ", a_tag.get_text(strip=True))
        if not title or len(title) < 3:
            continue

        row_html = str(tr)
        empmnsn = _extract_empmnsn(a_tag, row_html)
        col_texts = [re.sub(r"\s+", " ", td.get_text(" ", strip=True)) for td in tds]

        # 나라일터 apmList.do 컬럼 구조:
        # [번호, 공고명, 기관명, 근무지역, 접수기간(YYYY-MM-DD ~ YYYY-MM-DD), 등록일(YYYY-MM-DD)]
        # 또는 [번호, 기관명, 공고명, ...]
        company = col_texts[1] if len(col_texts) > 1 else "공공기관"
        if company == title and len(col_texts) > 2:
            company = col_texts[2] if not col_texts[2].isdigit() else col_texts[0]

        # 한 셀에 '2026-11-05 ~ 2026-11-09'처럼 범위가 있거나 마지막 컬럼이 등록일('2026-10-28')인 경우:
        # 모든 날짜를 추출해 가장 빠른 날짜를 등록일(posted_at), 가장 늦은 날짜를 마감일(deadline)로 설정
        all_dates: List[str] = []
        for c in col_texts:
            all_dates.extend(_extract_all_dates(c))
        posted_at = min(all_dates) if all_dates else ""
        deadline = max(all_dates) if all_dates else ""

        location = ""
        emp_type = ""
        for c in col_texts:
            if any(
                loc in c
                for loc in (
                    "서울",
                    "경기",
                    "인천",
                    "세종",
                    "대전",
                    "부산",
                    "대구",
                    "광주",
                    "울산",
                    "강원",
                    "충북",
                    "충남",
                    "전북",
                    "전남",
                    "경북",
                    "경남",
                    "제주",
                    "전국",
                )
            ) and c != title:
                location = c
            elif any(
                et in c
                for et in ("임기제", "공무직", "기간제", "전문경력관", "개방형", "일반직", "계약직", "시간선택제")
            ) and c != title:
                emp_type = c

        if empmnsn:
            detail_url = (
                f"https://www.gojobs.go.kr/apmView.do?menuNo=401"
                f"&mngrMenuYn=N&selMenuNo=400&upperMenuNo=&empmnsn={empmnsn}"
            )
            ext_id = f"gojobs_{empmnsn}"
        else:
            href = a_tag.get("href", "")
            detail_url = (
                urljoin(base_url, href)
                if href and not href.startswith("javascript")
                else base_url
            )
            digest = hashlib.md5(f"{company}_{title}".encode("utf-8")).hexdigest()[:10]
            ext_id = f"gojobs_{digest}"

        summary_line = " | ".join([c for c in col_texts if c and c != title])
        parsed.append(
            {
                "external_id": ext_id,
                "empmnsn": empmnsn,
                "title": title,
                "company": company,
                "location": location,
                "employment_type": emp_type,
                "posted_at": posted_at,
                "deadline": deadline,
                "url": detail_url,
                "content": summary_line,
            }
        )
        if len(parsed) >= max_items:
            break

    return parsed


# 나라일터 공고문 내 접수·일정·문의 안내 구문('서류접수 : ...', '원서접수 : ...', '관련문의 : ...' 등) 제거 패턴
_GOJOBS_PROCEDURAL_CLAUSE_RE = re.compile(
    r"(?:서류\s*접수|원서\s*접수|응시원서\s*접수|접수\s*기간|접수\s*방법|접수처|제출\s*방법|제출\s*기간|관련\s*문의|문의처)\s*[:：][^|\n○oㅇ□■※]*",
    flags=re.IGNORECASE,
)

# 나라일터 공고문 내 행정·문서 상용구('전자공문', '전자 공문', '전자우편', '전자 우편', '전자메일', '전자수입인지', '전자정부법' 등) 제거 패턴
_GOJOBS_ADMIN_NOISE_RE = re.compile(
    r"전자\s*(?:공문(?:서)?|우편(?:함)?|메일|접수|문서|서명|결재|수입인지|정부(?:법)?|파일|관보|팩스|입찰|계약|민원|투표|사본|시스템|점자|소송|청구|증빙)",
    flags=re.IGNORECASE,
)


def _fetch_gojobs_detail(client: httpx.Client, empmnsn: str) -> Dict[str, str]:
    info: Dict[str, str] = {}
    try:
        detail_url = "https://www.gojobs.go.kr/apmView.do"
        data = {
            "menuNo": "401",
            "mngrMenuYn": "N",
            "selMenuNo": "400",
            "empmnsn": empmnsn,
        }
        resp = client.post(detail_url, data=data)
        if resp.status_code != 200:
            resp = client.get(detail_url, params=data)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "lxml")
            table_texts: List[str] = []
            # 상세 페이지 테이블(기관명, 등록일, 접수마감일, 채용직급, 근무지역, 담당업무) 정밀 추출
            for tr in soup.select("table tr"):
                cells = tr.find_all(["th", "td"])
                first_label = (
                    re.sub(r"\s+", "", cells[0].get_text(strip=True)) if cells else ""
                )
                # 접수·일정·첨부·링크 등 직무 내용과 무관한 메타/안내 행은 본문 추출에서 제외
                if first_label not in (
                    "접수방법",
                    "접수처",
                    "제출방법",
                    "원서접수",
                    "서류접수",
                    "접수기간",
                    "첨부파일",
                    "관련링크",
                    "조회",
                    "장애인채용/우대",
                    "장애인채용우대",
                    "관련문의",
                    "문의처",
                ):
                    row_txt = re.sub(r"\s+", " ", tr.get_text(" ", strip=True))
                    if row_txt:
                        table_texts.append(row_txt)
                for idx in range(len(cells) - 1):
                    label = re.sub(r"\s+", "", cells[idx].get_text(strip=True))
                    val = re.sub(r"\s+", " ", cells[idx + 1].get_text(" ", strip=True))
                    if not val:
                        continue
                    if "기관명" in label and not info.get("company"):
                        info["company"] = val
                    elif "등록일" in label:
                        ds = _extract_all_dates(val)
                        if ds:
                            info["posted_at"] = ds[0]
                    elif "마감일" in label or "접수기간" in label:
                        ds = _extract_all_dates(val)
                        if ds:
                            info["deadline"] = ds[-1]
                    elif "근무지역" in label and not info.get("location"):
                        info["location"] = val
                    elif ("채용직급" in label or "고용형태" in label) and not info.get("employment_type"):
                        info["employment_type"] = val[:80]

            if table_texts:
                raw_body = " | ".join(table_texts)
            else:
                board = soup.select_one(".board_view, table")
                raw_body = (
                    re.sub(r"\s+", " ", board.get_text(" ", strip=True))
                    if board
                    else ""
                )
            if raw_body:
                cleaned_body = _GOJOBS_PROCEDURAL_CLAUSE_RE.sub(" ", raw_body)
                cleaned_body = _GOJOBS_ADMIN_NOISE_RE.sub(" ", cleaned_body)
                info["content"] = re.sub(r"\s+", " ", cleaned_body).strip()[:3000]
    except Exception:
        pass
    return info
