# 나라일터 (`gojobs`) 데이터 수집·필터 조건 (검사 순서별)

- **사이트 ID:** `gojobs`
- **사이트 표시명:** `나라일터`
- **수집 대상 URL:** `https://www.gojobs.go.kr/apmList.do?menuNo=401&mngrMenuYn=N&selMenuNo=400&upperMenuNo=`
- **관련 소스 파일:** [`scrapers/gojobs.py`](file:///Users/danyoh/XXX-AGENT/kr-job-agent/scrapers/gojobs.py), [`scrapers/__init__.py`](file:///Users/danyoh/XXX-AGENT/kr-job-agent/scrapers/__init__.py), [`scrapers/date_filter.py`](file:///Users/danyoh/XXX-AGENT/kr-job-agent/scrapers/date_filter.py), [`matcher.py`](file:///Users/danyoh/XXX-AGENT/kr-job-agent/matcher.py)

---

## 실제 코드 검사 순서 (먼저 검사하는 부분부터 나열)

### 1단계. 기존 DB 저장 공고의 마감일 선검사 (`Gate 0`)
- **검사 위치:** [`database.mark_expired_jobs()`](file:///Users/danyoh/XXX-AGENT/kr-job-agent/database.py#L430-L446)
- **검사 조건:** 기존 DB에 `status IN ('new', 'saved')`로 저장된 공고 중 `deadline < 오늘(YYYY-MM-DD)`인 공고
- **처리 결과:** 상태를 자동으로 `마감(expired)`으로 전환 (`지원완료`, `확인완료`는 변경하지 않음)

---

### 2단계. 목록 다중 페이지 스캔 및 0건 수집 실패 검사
- **검사 위치:** [`scrapers/gojobs.py`](file:///Users/danyoh/XXX-AGENT/kr-job-agent/scrapers/gojobs.py#L62-L170), [`scrapers/__init__.py`](file:///Users/danyoh/XXX-AGENT/kr-job-agent/scrapers/__init__.py#L115-L120)
- **검사 조건:**
  1. 회당 최대 수집 건수(`max_items`, 기본 `100건` = 10페이지)만큼 목록 테이블 행(`tr`)을 스캔
  2. 스캔된 전체 공고 수가 `0건`인지 확인
- **처리 결과:** `0건`이면 `수집 실패: 공고 0건 감지` 상태를 기록하고 즉시 중단

---

### 3단계. 중복 공고 여부 검사 (`Gate 1`)
- **검사 위치:** [`scrapers/gojobs.py`](file:///Users/danyoh/XXX-AGENT/kr-job-agent/scrapers/gojobs.py#L178), [`scrapers/__init__.py`](file:///Users/danyoh/XXX-AGENT/kr-job-agent/scrapers/__init__.py#L68-L73)
- **검사 조건:** 공고 고유 ID(`external_id = gojobs_{empmnsn}`)가 이미 DB(`known_ids`)에 존재하는지 확인
- **처리 결과:**
  - 이미 존재하면 **상세 페이지(`apmView.do`) 네트워크 요청을 생략**하고 마감일만 갱신한 뒤 **즉시 스킵(`continue`)** (기존 상태·메모 보존)

---

### 4단계. 목록 날짜 조건 1차 검사 (`Gate 2-A`: 마감일 & 최근 7일 등록일)
- **검사 위치:** [`scrapers/gojobs.py`](file:///Users/danyoh/XXX-AGENT/kr-job-agent/scrapers/gojobs.py#L174-L178), [`scrapers/date_filter.py`](file:///Users/danyoh/XXX-AGENT/kr-job-agent/scrapers/date_filter.py#L46-L70)
- **날짜 추출 규칙:** 목록 행의 모든 날짜(`YYYY-MM-DD`)를 추출하여 **가장 빠른 날짜를 `등록일(posted_at)`**, **가장 늦은 날짜를 `마감일(deadline)`**로 설정 (날짜 뒤바뀜 방지)
- **검사 조건:**
  1. **마감 여부:** `deadline < 오늘` (이미 마감된 공고)
  2. **등록일 경과 여부:** `(오늘 - posted_at).days > max_age_days` (기본 **최근 7일** 초과 공고)
- **처리 결과:** 둘 중 하나라도 해당하면 상세 페이지 요청을 생략하고 **DB에 저장하지 않은 채 즉시 스킵** (`날짜기준 스킵 N건` 증가)

---

### 5단계. 상세 페이지 추출 및 날짜 조건 2차 검사 (`Gate 2-B`)
- **검사 위치:** [`scrapers/gojobs.py`](file:///Users/danyoh/XXX-AGENT/kr-job-agent/scrapers/gojobs.py#L179-L192), [`scrapers/__init__.py`](file:///Users/danyoh/XXX-AGENT/kr-job-agent/scrapers/__init__.py#L78-L85)
- **추출 항목 및 접수·일정 상용구 제외:**
  - 3·4단계를 통과한 공고만 `apmView.do` 상세 페이지의 표(`기관명`, `등록일`, `마감일/접수기간`, `근무지역`, `채용직급/고용형태`, `주요업무`)를 정밀 추출
  - 단, 직무 내용과 무관한 행(`서류접수`·`원서접수`·`접수기간`·`접수방법`·`접수처`·`제출방법`·`첨부파일`·`관련링크`·`조회`·`장애인채용/우대`·`관련문의`·`문의처`), 본문 내 접수·일정 안내 구문(`서류접수 : ...`, `원서접수 : ...`, `관련문의 : ...` 등), 그리고 행정·문서 상용구(`전자공문`, `전자 공문`, `전자우편`, `전자 우편`, `전자메일`, `전자 메일`, `전자접수`, `전자수입인지`, `전자정부법`, `전자문서`, `전자서명`, `전자결재`, `전자파일`, `전자관보` 등)는 본문 추출 시 즉시 제거하여 참조하지 않음
- **검사 조건:** 상세 페이지에서 보강된 `posted_at`, `deadline` 기준으로 다시 한 번 마감 여부 및 7일 경과 여부 검사
- **처리 결과:** 기준 미달 시 **DB에 저장하지 않고 즉시 스킵**

---

### 6단계. 제외할 키워드 및 선호 직무 키워드 검사 (`Gate 3`)
- **검사 위치:** [`matcher.py` (`rule_based_evaluate`)](file:///Users/danyoh/XXX-AGENT/kr-job-agent/matcher.py#L9-L97)
- **검사 대상 텍스트:** `title(공고명)` + `employment_type(채용직급)` + `content(접수·일정 안내 구문 및 행정 상용구가 제거된 상세 채용표 본문)`
- **전처리 규칙:**
  - **접수·일정·문의 안내 구문 미참조 (`_PROCEDURAL_CLAUSE_PATTERNS`):** `서류접수 : 2026. 12. 24.(목) ~ ... (전자 공문)`, `원서접수 : ...`, `접수방법 : ...`, `관련문의 : ...` 등 채용절차/일정 안내 구문은 직무 매칭 전에 사전 제거하여 참조하지 않음
  - **행정·문서 상용구 미참조 (`_ADMIN_E_MAIL_PATTERNS`):** `전자공문(전자 공문)`, `전자우편(전자 우편)`, `전자메일(전자 메일)`, `전자접수`, `전자문서`, `전자수입인지`, `전자정부법`, `전자서명`, `전자결재`, `전자입찰`, `전자계약` 등(`운전자` 포함)은 직무 키워드 `전자`와 전혀 다른 의미이므로 매칭 전 사전 제거하여 절대 참조하지 않음
  - **영문 키워드 단어 경계 검사:** `IT`, `AI`, `QA`, `PM` 등 영문 키워드는 앞뒤에 영문 알파벳이 없을 때(`(?<![a-zA-Z])kw(?![a-zA-Z])`)만 인정 (`site`, `with`, `wait` 등 일반 영단어 내부 부분일치 오탐 차단)
- **검사 순서 및 조건:**
  1. **제외할 키워드(`exclude_keywords`) 먼저 검사 (`Gate 3-A`):** 사용자가 입력한 제외 키워드가 **1개라도 포함**되어 있으면 **DB에 저장하지 않고 즉시 스킵(`continue`)**
  2. **선호 직무 키워드(`include_keywords`) 검사 (`Gate 3-B`):** 설정된 선호 키워드 중 **최소 1개 이상 일치**하는지 확인 → **0개 일치(불일치) 시 DB에 저장하지 않고 즉시 스킵(`continue`)**

---

### 7단계. 선호 근무지역 일치 검사 (`Gate 4`)
- **검사 위치:** [`matcher.py` (`rule_based_evaluate`)](file:///Users/danyoh/XXX-AGENT/kr-job-agent/matcher.py#L81-L97)
- **검사 조건:**
  1. `선호 근무지역`(`preferred_locations`)에 **`무관`**이 포함되어 있으면 모든 공고 통과 (`#지역무관` 태그 부여)
  2. 특정 지역(`서울, 경기, 인천` 등)이 지정된 경우 `location(근무지역)` 또는 `title(공고명)`에 선호 지역명이 1개 이상 포함되어야 통과
- **처리 결과:** 불일치 시 **DB에 저장하지 않고 즉시 스킵(`continue`)**

---

### 8단계. 희망 경력 구분 일치 검사 (`Gate 5`)
- **검사 위치:** [`matcher.py` (`rule_based_evaluate`)](file:///Users/danyoh/XXX-AGENT/kr-job-agent/matcher.py#L99-L139)
- **오탐 방지 전처리 규칙:**
  - `학력무관`, `성별무관`, `연령무관`, `전공무관` 제거 (`경력무관`과 혼동 방지)
  - 공무원 시험 명칭인 `경력경쟁채용시험`, `경력경쟁채용`, `경력경쟁`, `전문경력관` 제거 (일반 민간 기업의 '경력직 전용'으로 오판되어 탈락하는 현상 방지)
- **검사 조건:**
  - `희망 경력 구분`(`preferred_career`)이 **`무관`**이면 모두 통과
  - 나라일터(`gojobs`) 공공채용은 기본적으로 응시자격이 열려 있으므로 `신입` 및 `경력` 설정 시 모두 허용
- **처리 결과:** 불일치 시 **DB에 저장하지 않고 즉시 스킵(`continue`)**

---

### 9단계. 최종 DB 저장 (`신규` 상태로만 저장)
- **저장 위치:** [`scrapers/__init__.py`](file:///Users/danyoh/XXX-AGENT/kr-job-agent/scrapers/__init__.py#L94-L103), [`database.insert_or_update_job()`](file:///Users/danyoh/XXX-AGENT/kr-job-agent/database.py#L368-L427)
- **처리 결과:** 위 1~8단계를 모두 통과한 공고만 매칭 키워드 태그(`#전산`, `#데이터` 등)와 함께 **무조건 `status = 'new'` (신규)**로 저장합니다.

