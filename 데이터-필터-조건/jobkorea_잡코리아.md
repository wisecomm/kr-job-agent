# 잡코리아 (`jobkorea`) 데이터 수집·필터 조건 (검사 순서별)

- **사이트 ID:** `jobkorea`
- **사이트 표시명:** `잡코리아`
- **수집 대상 URL:** `https://www.jobkorea.co.kr/recruit/joblist?menucode=local&localorder=1`
- **로그인 세션 파일:** `data/jobkorea_auth.json` (`requires_login = 1`)
- **관련 소스 파일:** [`scrapers/jobkorea.py`](file:///Users/danyoh/XXX-AGENT/kr-job-agent/scrapers/jobkorea.py), [`scrapers/__init__.py`](file:///Users/danyoh/XXX-AGENT/kr-job-agent/scrapers/__init__.py), [`scrapers/date_filter.py`](file:///Users/danyoh/XXX-AGENT/kr-job-agent/scrapers/date_filter.py), [`matcher.py`](file:///Users/danyoh/XXX-AGENT/kr-job-agent/matcher.py)

---

## 실제 코드 검사 순서 (먼저 검사하는 부분부터 나열)

### 1단계. 기존 DB 저장 공고의 마감일 선검사 (`Gate 0`)
- **검사 위치:** [`database.mark_expired_jobs()`](file:///Users/danyoh/XXX-AGENT/kr-job-agent/database.py#L430-L446)
- **검사 조건:** 기존 DB에 `status IN ('new', 'saved')`로 저장된 공고 중 `deadline < 오늘(YYYY-MM-DD)`인 공고
- **처리 결과:** 상태를 자동으로 `마감(expired)`으로 전환 (`지원완료`, `확인완료`는 변경하지 않음)

---

### 2단계. 로그인 세션 주입 및 목록 다중 페이지 스캔 (0건 수집 실패 검사)
- **검사 위치:** [`scrapers/jobkorea.py`](file:///Users/danyoh/XXX-AGENT/kr-job-agent/scrapers/jobkorea.py#L74-L124), [`scrapers/__init__.py`](file:///Users/danyoh/XXX-AGENT/kr-job-agent/scrapers/__init__.py#L115-L120)
- **동작 및 검사 조건:**
  1. `data/jobkorea_auth.json` 파일이 존재하면 Playwright `storage_state`에 자동 주입하여 로그인 상태로 접속
  2. 회당 최대 수집 건수(`max_items`, 기본 `100건`)에 도달할 때까지 페이지네이션(`Page_No`)을 순회하며 채용 리스트 행(`tr.devloopArea`, `.tplJobList tbody tr`) 추출
  3. **상세 페이지 오염 텍스트 수집 금지:** 잡코리아 상세 페이지(`#container`) 전체를 긁어오면 사이트 상단 메뉴(`AI·개발·데이터`)나 기업 복리후생(`자기개발비`) 때문에 비IT 공고가 오탐되므로, 반드시 **목록 행의 `title(공고 제목)` + `p.etc(경력·지역 조건)` + `p.dsc(직무 태그 요약)`**만 추출
  4. 스캔된 공고 수가 `0건`이면 `수집 실패: 공고 0건 감지` 상태를 기록하고 즉시 중단

---

### 3단계. 중복 공고 여부 검사 (`Gate 1`)
- **검사 위치:** [`scrapers/__init__.py`](file:///Users/danyoh/XXX-AGENT/kr-job-agent/scrapers/__init__.py#L68-L73)
- **검사 조건:** 공고 고유 ID(`external_id = jobkorea_{gno}`)가 이미 DB(`known_ids`)에 존재하는지 확인
- **처리 결과:** 이미 존재하면 마감일만 갱신하고 **즉시 스킵(`continue`)**

---

### 4단계. 날짜 조건 검사 (`Gate 2`: 마감일 & 최근 7일 등록일)
- **검사 위치:** [`scrapers/jobkorea.py`](file:///Users/danyoh/XXX-AGENT/kr-job-agent/scrapers/jobkorea.py#L17-L46), [`scrapers/date_filter.py`](file:///Users/danyoh/XXX-AGENT/kr-job-agent/scrapers/date_filter.py#L17-L70), [`scrapers/__init__.py`](file:///Users/danyoh/XXX-AGENT/kr-job-agent/scrapers/__init__.py#L78-L85)
- **날짜 정규화 규칙:**
  - 마감일(`~11/15(금)`, `오늘마감`, `내일마감`, `2026.11.15`) → `YYYY-MM-DD` 변환 (`상시채용`은 미마감 유지)
  - 등록일(`3시간 전`, `방금 전`, `어제`, `3일 전`, `10/28`) → 오늘 기준 `YYYY-MM-DD` 변환
- **검사 조건:**
  1. **마감 여부:** `deadline < 오늘`
  2. **등록일 경과 여부:** `(오늘 - posted_at).days > max_age_days` (기본 **최근 7일** 초과)
- **처리 결과:** 둘 중 하나라도 해당하면 **DB에 저장하지 않고 즉시 스킵(`continue`)** (`날짜기준 스킵 N건` 증가)

---

### 5단계. 제외할 키워드 및 선호 직무 키워드 검사 (`Gate 3`)
- **검사 위치:** [`matcher.py` (`rule_based_evaluate`)](file:///Users/danyoh/XXX-AGENT/kr-job-agent/matcher.py#L24-L78)
- **검사 대상 텍스트:** `title(공고 제목)` + `employment_type(경력/고용형태)` + `content`의 첫 줄(목록 직무 태그 요약)
- **전처리 규칙:**
  - **영문 키워드 단어 경계 검사:** `IT`, `AI`, `QA`, `PM` 등 영문 키워드는 앞뒤에 영문 알파벳이 없을 때(`(?<![a-zA-Z])kw(?![a-zA-Z])`)만 매칭 (`site`, `with`, `gmail` 등 일반 영단어 내부 부분일치 오탐 차단)
  - 특정 직무(IT 등)에 종속되는 하드코딩 없이, 사용자가 설정한 모든 분야의 키워드를 동일하게 검사
- **검사 순서 및 조건:**
  1. **제외할 키워드(`exclude_keywords`) 먼저 검사 (`Gate 3-A`):** 사용자가 입력한 제외 키워드가 **1개라도 포함**되어 있으면 **DB에 저장하지 않고 즉시 스킵(`continue`)**
  2. **선호 직무 키워드(`include_keywords`) 검사 (`Gate 3-B`):** 설정된 선호 키워드 중 **최소 1개 이상 일치**하는지 확인 → **0개 일치(불일치) 시 DB에 저장하지 않고 즉시 스킵(`continue`)**

---

### 6단계. 선호 근무지역 일치 검사 (`Gate 4`)
- **검사 위치:** [`matcher.py` (`rule_based_evaluate`)](file:///Users/danyoh/XXX-AGENT/kr-job-agent/matcher.py#L81-L97)
- **검사 조건:**
  1. `선호 근무지역`(`preferred_locations`)에 **`무관`**이 포함되어 있으면 모든 공고 통과 (`#지역무관` 태그 부여)
  2. 특정 지역(`서울, 경기, 인천` 등)이 지정된 경우 `location` 또는 `title`에 선호 지역명이 1개 이상 포함되어야 통과
- **처리 결과:** 불일치 시 **DB에 저장하지 않고 즉시 스킵(`continue`)**

---

### 7단계. 희망 경력 구분 일치 검사 (`Gate 5`)
- **검사 위치:** [`scrapers/jobkorea.py`](file:///Users/danyoh/XXX-AGENT/kr-job-agent/scrapers/jobkorea.py#L222-L225), [`matcher.py` (`rule_based_evaluate`)](file:///Users/danyoh/XXX-AGENT/kr-job-agent/matcher.py#L99-L139)
- **오탐 방지 전처리 규칙:**
  - 목록 파싱 단계와 매칭 단계 모두에서 **`학력무관`, `성별무관`, `연령무관`, `전공무관`을 완전히 제거** (`학력무관`이 `경력무관`으로 오인되는 현상 차단)
- **검사 조건:**
  - `희망 경력 구분`(`preferred_career`)이 **`무관`**이면 신입·경력 모든 공고 통과
  - **`신입`** 선택 시: `신입`/`인턴`/`경력무관` 없이 순수 `경력`(예: `경력2년↑`, `경력무관` 미포함)만 명시된 공고는 탈락
  - **`경력`** 선택 시: `경력`/`경력무관` 없이 순수 `신입`만 명시된 공고는 탈락
- **처리 결과:** 불일치 시 **DB에 저장하지 않고 즉시 스킵(`continue`)**

---

### 8단계. 최종 DB 저장 (`신규` 상태로만 저장)
- **저장 위치:** [`scrapers/__init__.py`](file:///Users/danyoh/XXX-AGENT/kr-job-agent/scrapers/__init__.py#L94-L103), [`database.insert_or_update_job()`](file:///Users/danyoh/XXX-AGENT/kr-job-agent/database.py#L368-L427)
- **처리 결과:** 위 1~7단계를 모두 통과한 공고만 매칭 키워드 태그와 함께 **무조건 `status = 'new'` (신규)**로 저장합니다.

