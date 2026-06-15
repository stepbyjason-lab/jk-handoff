# Changelog

`jk-handoff` 공용 CLI 와 두 어댑터(Claude `/handoff`, Codex `$handoff`)의 변경 기록.

버전은 SemVer 가 아니라 작업 규모에 따른 사용자 지정 체계다. 형식은
[Keep a Changelog](https://keepachangelog.com/) 를 한국어로 따른다.

> 0.2 이전(0.01 / 0.1)은 CLI 이전의 산문 명령 + Windows 무결성 검증 스크립트 프로토타입
> 단계였고, 이 저장소에는 0.2 의 실행가능 CLI 구조부터 포함한다.

---

## [0.2.3] - 2026-07-02

### Fixed (수정)

- cp949 locale에서 CLI JSON 출력이 UnicodeError로 잘리던 문제 — 표준 스트림 UTF-8 고정.

### Added (추가)

- `## Not Tried Yet` 섹션(9→10섹션) + 어댑터 확인 증거 규율.
- save payload `lang` 필드 + 언어 체인(payload > HANDOFF_LANG > OS 로케일 > en, ko/en 메시지 테이블).
- 회귀 테스트 다수(65→90).
- CI 워크플로(`.github/workflows/test.yml`) 추가 — 기본/`HANDOFF_LANG=en` 두 모드 매트릭스 실행.

### Changed (변경)

- KST 하드코딩 → 시스템 로컬 타임존(ISO 오프셋 포함 불변).
- 어댑터 doc 무손실 슬림화.
- 어댑터 '사용자의 언어로 작성'.
- 어댑터 문서의 MemKraft(사설 도구) 참조를 "장기 기억 도구(선택)"로 일반화.
- legacy v0.1 `skill/handoff.md` 제거(공용 CLI로 대체된 지 오래된 죽은 파일).

---

## [0.2.2] - 2026-06-16

저장 후 사용자에게 보여주는 출력이 프로젝트·실행마다 달라지던 문제 해결. 어댑터(LLM)
자유서술 대신 CLI 가 완성된 한국어 보고 `report` 를 결정적으로 생성하고 어댑터는 그대로 출력한다.

### Added (추가)

- `cmd_save` 결과에 `report`(사용자 보고 전문) · `resume_prompt`(새 세션 복붙용 한국어 프롬프트,
  text 코드블럭) · `topic`/`status`/`summary` 필드 추가.
- resume_prompt: 프로젝트명 우선 + 경로 힌트(크로스머신), summary 1줄화 · 폴백 시 요약 줄 생략 ·
  코드펜스 무력화.
- 어댑터(Claude/Codex)는 `report` 를 그대로 출력하도록 지시 — 자유서술 금지.
- 테스트 11건(바이트 일치 · 결정성 · summary 위생 · 충돌 경로 resume 부재 등).

### Changed (변경)

- `concurrent_conflict` 경로는 `report` 가 충돌 안내이며 resume 블록이 없다.

---

## [0.2.1] - 2026-06-12

Codex 가 작성한 handoff 의 파생 인덱스가 Claude 전역 설정 저장소 상태에 묶이지 않도록
분리했다. 상세 정본은 계속 프로젝트 `.handoff/` 이며, Claude/Codex 가 서로 읽고 이어받는
호환성은 유지된다.

### Changed (변경)

- `source=codex` 저장의 기본 CURRENT.md 루트를 `~/.claude` 에서 `~/.codex` 로 변경.
- `source=claude-code` 저장은 기존 기본값 `~/.claude` 를 유지.
- remote-ahead skip 경고가 하드코딩된 `~/.claude` 대신 실제 writer-local 루트를 표시.
- `reindex --source codex` 추가 — Codex-local 인덱스만 재생성 가능.

### Verification (검증)

- Codex 기본 저장이 `~/.codex/handoffs/<project>/CURRENT.md` 로 향하는 회귀 테스트 추가.
- Claude 기본 저장이 `~/.claude/handoffs/<project>/CURRENT.md` 를 유지하는 회귀 테스트 추가.

---

## [0.2] - 2026-06-06

`/handoff` 를 산문 명령에서 **실행가능 Python 공용 CLI + 두 어댑터** 구조로 재설계.
Claude `/handoff` 와 Codex `$handoff` 가 같은 코어를 공유한다.

### Added (추가)

- **공용 Python CLI** (`core/handoff_cli/`) — 두 어댑터의 상세 본문·LATEST·INDEX·글로벌
  CURRENT.md **모든 파일쓰기를 위임**받는 단일 실행가능 코어.
  `python -m handoff_cli {save,list,find,resume,archive}`, JSON 인터op 경계.
- **Codex 어댑터** (`codex/handoff/SKILL.md` + `agents/openai.yaml`) — `$handoff`/자연어 진입점.
- **Claude 어댑터** (`claude/handoff.md`) — `/handoff` 라이브 command 정본.
- **글로벌 2-tier 인덱스** — `<writer-root>/handoffs/<project>/CURRENT.md` = 프로젝트당 1개,
  전 active 토픽 집계 재생성(파생). status taxonomy(active/waiting/watching/done) +
  레거시 open/open_planning/closed/CLOSED 정규화.
- **네트워크 없는 크로스호스트 가드** — fetch/pull/push 안 함. 충돌마커·divergent project_id·
  로컬 remote-tracking ahead 면 글로벌 인덱스만 skip, 상세 본문은 항상 먼저 저장.
- **secret 코드 게이트** — CURRENT.md body 패턴 적중 라인 `[REDACTED]` 치환(구조 라인 제외).
- **provenance** — updated_at 시스템 시계 실측, full 40자 SHA, 비-git mtime 앵커.
  `.project-id` 커밋·공유(미커밋·staged-only 경고).
- **unittest 스위트** (`tests/`) + `pyproject.toml`(`pip install -e .`).

### Changed (변경)

- 글로벌은 CURRENT.md 인덱스 1장만 허용(상세 본문은 프로젝트 `.handoff/` 전용).
- 타임스탬프/SHA 통일 — full ISO 8601(+09:00) + full 40자 SHA(short SHA resume 는 prefix 비교 호환).

### Verification (검증)

- 다중 렌즈 병렬 적대 검증(아키텍처·보안·일관성·테스트 정직성)으로 교차 검출 항목 수정.
- BOM `--input` → utf-8-sig 처리, hex basename 제목 오탐 → 구조 라인 스캔 제외.
