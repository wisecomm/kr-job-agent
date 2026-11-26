# 확장 구직 사이트 (`generic` / 사람인 `saramin` 등) 데이터 수집·필터 조건 (검사 순서별)

- **사이트 ID:** 사용자가 `[구직 사이트 & 로그인 관리]` 탭에서 추가 등록한 사이트 ID (예: `saramin`, `wanted`, `work24`)
- **관련 소스 파일:** [`scrapers/generic.py`](file:///Users/danyoh/XXX-AGENT/kr-job-agent/scrapers/generic.py), [`scrapers/__init__.py`](file:///Users/danyoh/XXX-AGENT/kr-job-agent/scrapers/__init__.py), [`scrapers/date_filter.py`](file:///Users/danyoh/XXX-AGENT/kr-job-agent/scrapers/date_filter.py), [`matcher.py`](file:///Users/danyoh/XXX-AGENT/kr-job-agent/matcher.py)

---

## 실제 코드 검사 순서 (먼저 검사하는 부분부터 나열)

1. **1단계 (기존 DB 마감일 선검사 - `Gate 0`):** 기존 DB의 `new`/`saved` 공고 중 `deadline < 오늘`인 공고를 `expired(마감)`로 전환
2. **2단계 (세션 주입 및 목록 스캔):**
   - `requires_login = 1`이고 `data/{source_id}_auth.json`이 존재하면 Playwright 세션 자동 주입
   - 도메인이 `saramin.co.kr`인 경우 사람인 전용 파서(`_parse_saramin_html`) 우선 실행, 그 외 사이트는 범용 채용 링크 파서 실행
   - 스캔된 공고가 `0건`이면 `수집 실패` 기록 후 종료
3. **3단계 (중복 공고 여부 검사 - `Gate 1`):** `external_id`가 이미 DB(`known_ids`)에 있으면 마감일만 갱신 후 **즉시 스킵**
4. **4단계 (날짜 조건 검사 - `Gate 2`):** `deadline < 오늘` 또는 `(오늘 - posted_at).days > max_age_days(기본 7일)`이면 **DB 미저장 즉시 스킵**
5. **5단계 (제외할 키워드 및 선호 직무 키워드 검사 - `Gate 3`):** 영문 단어 경계 검사 적용 후, `제외할 키워드(exclude_keywords)`가 1개라도 포함되거나 `선호 직무 키워드(include_keywords)`가 0개 일치하면 **DB 미저장 즉시 스킵**
6. **6단계 (선호 근무지역 검사 - `Gate 4`):** `무관`이 아니면서 지정 지역 불일치 시 **DB 미저장 즉시 스킵**
7. **7단계 (희망 경력 구분 검사 - `Gate 5`):** `학력무관` 제거 후 희망 경력(`무관`/`신입`/`경력`) 불일치 시 **DB 미저장 즉시 스킵**
8. **8단계 (최종 저장):** 모든 조건을 통과한 공고만 **무조건 `status = 'new'` (신규)**로 DB에 저장

