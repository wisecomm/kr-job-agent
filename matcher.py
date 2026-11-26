import re
from typing import Any, Dict, List


def _split_csv(text: str) -> List[str]:
    return [t.strip() for t in (text or "").split(",") if t.strip()]


# 공고문 내 서류접수·원서접수·접수방법·관련문의 등 채용절차/일정 안내 구문 사전 제거 패턴
_PROCEDURAL_CLAUSE_PATTERNS = re.compile(
    r"(?:서류\s*접수|원서\s*접수|응시원서\s*접수|접수\s*기간|접수\s*방법|접수처|제출\s*방법|제출\s*기간|관련\s*문의|문의처)\s*[:：][^|\n○oㅇ□■※]*",
    flags=re.IGNORECASE,
)

# '전자' 직무 키워드와 전혀 다른 의미인 행정·문서 상용구('전자공문', '전자 공문', '전자우편', '전자 우편', '전자메일', '전자접수', '전자수입인지' 등) 사전 제거 패턴
_ADMIN_E_MAIL_PATTERNS = re.compile(
    r"전자\s*(?:공문(?:서)?|우편(?:함)?|메일|접수|문서|서명|결재|수입인지|정부(?:법)?|파일|관보|팩스|입찰|계약|민원|투표|사본|시스템|점자|소송|청구|증빙)",
    flags=re.IGNORECASE,
)


def _keyword_in_text(kw: str, text: str) -> bool:
    """
    1) '서류접수 : ...', '원서접수 : ...' 등 접수·일정 안내 구문은 직무 매칭에서 참조하지 않고 제거합니다.
    2) '전자공문(전자 공문)', '전자우편(전자 우편)', '전자메일(전자 메일)' 등 행정·문서 상용구는
       직무 키워드 '전자'와 무관하므로 사전 제거합니다.
    3) '전자' 검색 시 '운전자'(운전+자)처럼 다른 단어의 끝글자가 겹치는 오탐도 함께 차단합니다.
    4) 영문 키워드(예: IT, AI, QA, PM 등)가 일반 영단어(site, with, gmail 등) 내부에
       부분 문자열로 매칭되는 오탐을 막기 위해 영문 알파벳 경계를 검사합니다.
    """
    k = kw.strip()
    if not k or not text:
        return False
    cleaned_text = _PROCEDURAL_CLAUSE_PATTERNS.sub(" ", text)
    cleaned_text = _ADMIN_E_MAIL_PATTERNS.sub(" ", cleaned_text)
    if k == "전자":
        cleaned_text = cleaned_text.replace("운전자", " ")
    if re.match(r"^[a-zA-Z0-9+#.]+$", k):
        pattern = rf"(?<![a-zA-Z]){re.escape(k)}(?![a-zA-Z])"
        return bool(re.search(pattern, cleaned_text, flags=re.IGNORECASE))
    return k.lower() in cleaned_text.lower()


def rule_based_evaluate(
    job: Dict[str, Any], criteria: Dict[str, Any]
) -> Dict[str, Any]:
    """
    특정 업종에 종속되지 않는 범용 순수 조건 매칭 필터:
    1) 제외할 키워드(exclude_keywords) 포함 여부 먼저 검사 (1개라도 포함 시 제외)
    2) 선호 직무 키워드(include_keywords) 1개 이상 포함 여부 검사
    3) 선호 근무지역('무관' 또는 지정 지역) 일치 여부 검사
    4) 희망 경력 구분('무관' / '신입' / '경력') 일치 여부 검사
    """
    include_kws = _split_csv(criteria.get("include_keywords", ""))
    exclude_kws = _split_csv(criteria.get("exclude_keywords", ""))
    pref_locs = _split_csv(criteria.get("preferred_locations", ""))

    # 잡코리아의 경우 상세 페이지 전체 HTML(#container 메뉴 등)이 줄바꿈 이후에 붙어있던 구버전 데이터 오탐 방지를 위해 첫 줄(순수 직무 태그 요약)만 사용
    raw_content = job.get("content", "") or ""
    if job.get("source") == "jobkorea" and "\n" in raw_content:
        raw_content = raw_content.split("\n", 1)[0]

    full_text = " ".join(
        [
            job.get("title", ""),
            job.get("employment_type", ""),
            raw_content,
        ]
    )

    title_str = job.get("title", "")

    # Gate 3-A: 제외할 키워드가 설정되어 있고 1개라도 포함되어 있으면 즉시 제외 처리
    for ex_kw in exclude_kws:
        if _keyword_in_text(ex_kw, title_str) or _keyword_in_text(ex_kw, full_text):
            return {
                "score": 0,
                "reason": f"제외 키워드({ex_kw}) 포함",
                "tags": ["제외키워드"],
                "hard_excluded": True,
            }

    matched_kws: List[str] = []
    matched_locs: List[str] = []

    for kw in include_kws:
        if _keyword_in_text(kw, title_str) or _keyword_in_text(kw, full_text):
            matched_kws.append(kw)

    # Gate 3-B: 선호 직무 키워드가 설정되어 있는데 단 1개도 매칭되지 않은 경우 즉시 제외 처리
    if include_kws and not matched_kws:
        return {
            "score": 0,
            "reason": "선호 직무 키워드 불일치",
            "tags": ["직무불일치"],
            "hard_excluded": True,
        }

    # Gate 4: 선호 근무지역 확인 ('무관'이면 모든 공고 통과)
    if "무관" in pref_locs:
        matched_locs.append("지역무관")
    else:
        for loc in pref_locs:
            if loc.lower() in (
                job.get("location", "") + " " + job.get("title", "")
            ).lower():
                matched_locs.append(loc)
                break
        if pref_locs and job.get("location", "").strip() and not matched_locs:
            return {
                "score": 0,
                "reason": f"선호 근무지역({', '.join(pref_locs)}) 불일치",
                "tags": ["지역불일치"],
                "hard_excluded": True,
            }

    # Gate 5: 희망 경력 구분 확인 ('학력무관', 공무원 '경력경쟁채용시험' 등을 오인하지 않도록 분리)
    pref_career = (criteria.get("preferred_career") or "무관").strip()
    raw_emp = (
        job.get("employment_type", "")
        + " "
        + job.get("title", "")
        + " "
        + (job.get("content", "") or "")[:300]
    )
    cleaned_emp = (
        raw_emp.replace("학력무관", "")
        .replace("성별무관", "")
        .replace("연령무관", "")
        .replace("전공무관", "")
        .replace("경력경쟁채용시험", "")
        .replace("경력경쟁채용", "")
        .replace("경력경쟁", "")
        .replace("전문경력관", "")
    )
    has_newcomer = "신입" in cleaned_emp or "인턴" in cleaned_emp
    has_career_free = (
        "경력무관" in cleaned_emp
        or "경력 무관" in cleaned_emp
        or job.get("source") == "gojobs"
    )
    has_exp = "경력" in cleaned_emp.replace("경력무관", "").replace("경력 무관", "")

    if pref_career == "신입" and has_exp and not has_newcomer and not has_career_free:
        return {
            "score": 0,
            "reason": "경력직 전용 공고 (신입 선호 제외)",
            "tags": ["경력직전용"],
            "hard_excluded": True,
        }
    if pref_career == "경력" and has_newcomer and not has_exp and not has_career_free:
        return {
            "score": 0,
            "reason": "신입 전용 공고 (경력 선호 제외)",
            "tags": ["신입전용"],
            "hard_excluded": True,
        }

    tags = list(dict.fromkeys(matched_kws[:4] + matched_locs[:1]))
    reason_parts = []
    if matched_kws:
        reason_parts.append(f"일치 키워드: {', '.join(matched_kws[:4])}")
    if matched_locs:
        reason_parts.append(f"지역: {matched_locs[0]}")

    return {
        "score": 100,
        "reason": " · ".join(reason_parts) if reason_parts else "조건 일치",
        "tags": tags[:5],
        "hard_excluded": False,
    }


def evaluate_job(job: Dict[str, Any], criteria: Dict[str, Any]) -> Dict[str, Any]:
    return rule_based_evaluate(job, criteria)
