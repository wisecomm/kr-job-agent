import re
from datetime import date, timedelta
from typing import Optional, Tuple


def parse_iso_date(text: str) -> Optional[date]:
    if not text:
        return None
    m = re.search(r"(20\d{2})-(\d{2})-(\d{2})", text.strip())
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def normalize_relative_posted_date(raw: str) -> str:
    """
    잡코리아/일반 채용사이트의 등록/수정일 텍스트를 YYYY-MM-DD로 변환합니다.
    예: '10분 전 등록', '3시간 전 수정', '오늘 등록', '어제', '3일 전', '10/25(일) 등록', '2026.10.25'
    """
    text = (raw or "").strip()
    if not text:
        return ""
    today = date.today()

    if any(k in text for k in ("분 전", "분전", "시간 전", "시간전", "오늘", "방금", "당일")):
        return today.isoformat()
    if "어제" in text:
        return (today - timedelta(days=1)).isoformat()

    m_days = re.search(r"(\d+)\s*일\s*전", text)
    if m_days:
        days_ago = int(m_days.group(1))
        return (today - timedelta(days=days_ago)).isoformat()

    m_full = re.search(r"(20\d{2})[-./](\d{1,2})[-./](\d{1,2})", text)
    if m_full:
        try:
            return date(
                int(m_full.group(1)), int(m_full.group(2)), int(m_full.group(3))
            ).isoformat()
        except ValueError:
            pass

    m_short_yr = re.search(r"\b(\d{2})[-./](\d{1,2})[-./](\d{1,2})", text)
    if m_short_yr:
        try:
            return date(
                2000 + int(m_short_yr.group(1)),
                int(m_short_yr.group(2)),
                int(m_short_yr.group(3)),
            ).isoformat()
        except ValueError:
            pass

    m_mmdd = re.search(r"(\d{1,2})[./](\d{1,2})", text)
    if m_mmdd:
        month, day = int(m_mmdd.group(1)), int(m_mmdd.group(2))
        try:
            candidate = date(today.year, month, day)
            # 등록일은 미래일 수 없으므로 미래 날짜면 작년으로 보정
            if candidate > today + timedelta(days=1):
                candidate = date(today.year - 1, month, day)
            return candidate.isoformat()
        except ValueError:
            pass

    return ""


def should_skip_by_date(
    posted_at: str, deadline: str, max_age_days: int = 7
) -> Tuple[bool, str]:
    """
    수집 단계 날짜 필터:
    1) 이미 마감된 공고(deadline < 오늘 또는 '마감완료') 즉시 스킵
    2) 등록일(posted_at)이 최근 max_age_days(기본 7일)를 초과한 오래된 공고 즉시 스킵
    반환값: (스킵 여부, 사유)
    """
    today = date.today()

    # 1. 마감 여부 검사
    dl_text = (deadline or "").strip()
    if any(k in dl_text for k in ("마감완료", "채용마감", "접수마감", "종료")):
        return True, f"마감된 공고 ({dl_text})"

    dl_date = parse_iso_date(dl_text)
    if dl_date and dl_date < today:
        return True, f"마감일 경과 ({dl_date.isoformat()} < {today.isoformat()})"

    # 2. 등록일 최근 N일(7일) 이내 검사
    if max_age_days > 0:
        posted_date = parse_iso_date(posted_at)
        if posted_date:
            age_days = (today - posted_date).days
            if age_days > max_age_days:
                return (
                    True,
                    f"등록일 {max_age_days}일 초과 ({posted_date.isoformat()}, {age_days}일 전)",
                )

    return False, ""
