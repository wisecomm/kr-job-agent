# Agent Guidelines for `kr-job-agent`

## 필수 참조 규칙 (Mandatory Reference Before Coding)

1. **코딩 및 소스 수정 전 필수 참조:** 이 프로젝트의 어떤 소스 파일(`database.py`, `matcher.py`, `app.py`, `scrapers/*`, `templates/index.html`)이든 수정하기 전에 반드시 아래 두 곳의 문서를 **먼저 읽고** 검사 순서와 필터 조건을 확인해야 합니다:
   - **[`데이터-필터-조건/`](file:///Users/danyoh/XXX-AGENT/kr-job-agent/데이터-필터-조건/README.md)** 하위 구직 사이트별 필터 조건 문서 ([`gojobs_나라일터.md`](file:///Users/danyoh/XXX-AGENT/kr-job-agent/데이터-필터-조건/gojobs_나라일터.md), [`jobkorea_잡코리아.md`](file:///Users/danyoh/XXX-AGENT/kr-job-agent/데이터-필터-조건/jobkorea_잡코리아.md), [`generic_확장사이트.md`](file:///Users/danyoh/XXX-AGENT/kr-job-agent/데이터-필터-조건/generic_확장사이트.md))
   - **[`flowchart/FLOWCHART.md`](file:///Users/danyoh/XXX-AGENT/kr-job-agent/flowchart/FLOWCHART.md)**
2. **소스 수정 후 동기화:** 조건 설정, 사이트별 필터링 조건, 검사 순서, 정렬, 페이징 등 동작 흐름이 변경되면 반드시 **[`데이터-필터-조건/*.md`](file:///Users/danyoh/XXX-AGENT/kr-job-agent/데이터-필터-조건/README.md)** 및 **[`flowchart/FLOWCHART.md`](file:///Users/danyoh/XXX-AGENT/kr-job-agent/flowchart/FLOWCHART.md)** 도 함께 업데이트하여 코드와 문서가 100% 일치하도록 유지해야 합니다.
