# 맞춤 일자리 탐색·지속 업데이트·관리 에이전트 (`kr-job-agent`)

`ai-job-search`의 불필요한 영문 LaTeX 이력서/면접 기능을 덜어내고, **한국 구직 사이트(나라일터, 잡코리아 및 추가 확장 사이트)에서 내가 원하는 조건의 일자리만 지속적으로 수집·AI 필터링·상태 관리**하는 로컬 에이전트입니다.

---

## 주요 기능

1. **오픈형 + 로그인 필요 사이트 동시 지원**
   - **나라일터 (`gojobs`)**: 오픈된 공공채용 목록(`apmList.do`) 및 상세(`apmView.do`) 자동 수집
   - **잡코리아 (`jobkorea`)**: 브라우저 1회 수동 로그인으로 세션(`data/jobkorea_auth.json`)을 저장해 캡차/보안 차단 없이 로그인 상태로 지속 수집
   - **확장형 사이트 플러그인 (`generic`)**: 대시보드에서 새 구직 사이트 URL만 등록하면 즉시 수집 대상에 추가 (정교한 파서가 필요할 경우 `scrapers/`에 모듈 추가 가능)
2. **2단계 스마트 필터링 (비용 절감 & 정확도 향상)**
   - **1단계 (규칙 필터):** 이미 확인한 공고(`external_id` 중복) 스킵 + 제외 키워드 포함 공고 자동 `제외됨(rejected)` 분류
   - **2단계 (Gemini AI 채점):** `gemini-2.5-flash` Structured Output을 통해 내 희망 조건과 상세 요강을 비교하여 **0~100점 적합도 점수 + 추천 사유 + 핵심 태그** 생성 (`GEMINI_API_KEY` 미설정 시 키워드 가중치 평가기로 자동 동작)
3. **자동 업데이트 & 마감 관리**
   - 백그라운드 스케줄러(APScheduler)가 지정된 주기(예: 180분)마다 신규 공고를 수집하고, 마감일이 지난 공고는 자동으로 `마감(expired)` 상태로 전환
4. **로컬 SQLite + 웹 대시보드 (`http://127.0.0.1:8088`)**
   - 상태 관리(`신규`, `관심`, `지원완료`, `제외됨`, `마감`), 점수별/사이트별 필터링, 개인 메모 저장

---

## 빠른 시작 방법

```bash
cd /Users/danyoh/XXX-AGENT/kr-job-agent

# 1. 가상환경 생성 및 패키지 설치 (uv 사용 시)
uv sync
uv run playwright install chromium

# 2. 에이전트 웹 대시보드 & 백그라운드 스케줄러 실행
uv run python app.py
```

브라우저에서 **`http://127.0.0.1:8088`** 에 접속합니다.

---

## 프로젝트 구조

```text
kr-job-agent/
├── app.py                  # FastAPI 웹 서버 + APScheduler 자동 업데이트 스케줄러
├── database.py             # SQLite (data/jobs.db) 스키마 및 상태·이력 관리
├── matcher.py              # 1차 키워드 필터 + 2차 Gemini AI 적합도 점수 산출
├── scrapers/
│   ├── __init__.py         # 수집 파이프라인 오케스트레이터 & 로그인 세션 캡처
│   ├── gojobs.py           # 나라일터 (오픈 구직사이트) 전용 수집기
│   ├── jobkorea.py         # 잡코리아 (로그인 세션 연동 + iframe 상세 추출) 수집기
│   └── generic.py          # 추후 사이트 추가용 범용 Playwright/HTML 수집기
├── templates/
│   └── index.html          # 한국어 로컬 웹 대시보드 UI
└── data/
    ├── jobs.db             # 로컬 SQLite 데이터베이스 (자동 생성)
    └── jobkorea_auth.json  # 잡코리아 로그인 세션 쿠키 파일 (로그인 연결 시 생성)
```
