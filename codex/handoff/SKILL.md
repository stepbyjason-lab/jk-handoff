---
name: handoff
description: 세션 핸드오프를 저장·재개·검색·목록·archive 한다. $handoff 또는 "핸드오프 저장해줘 / 이어받게 정리해줘 / 핸드오프 재개 / 핸드오프 검색 / 핸드오프 목록" 같은 자연어, 그리고 세션을 끝내기 전 다음 세션이나 다른 머신이 이어받게 현재 상태를 정리해야 할 때 사용한다. 프로젝트 안 .handoff/ 에 상세 정본을 저장하고 글로벌 진행상황 인덱스를 갱신하는 작업을 공용 Python CLI 에 위임하는 Codex 어댑터다.
allowed-tools: Bash, Read
---

# handoff — 2-tier 세션 핸드오프 (Codex 어댑터)

여러 세션·여러 머신을 오가며 작업 상태를 넘기는 스킬이다. 이 문서는 **Codex 어댑터**이며,
Claude Code 의 `/handoff` 명령과 **같은 공용 Python CLI(`core/handoff_cli`)를 공유**한다.

## 역할 경계

이 어댑터가 하는 일:

1. 현재 대화에서 Done / Open / 결정 / 다음 행동을 판단해 사용자의 언어로 narrative 를 만든다.
2. 공용 CLI 에 구조화 JSON 입력을 넘긴다.
3. CLI 출력과 경고를 사용자에게 보고한다.

이 어댑터가 **하지 않는** 일: handoff 파일을 직접 만들거나 고치지 않는다. 상세 본문 ·
`LATEST.md` · `INDEX.md` · Codex-local `CURRENT.md` 의 모든 파일쓰기는 CLI 가 수행한다.
Codex 의 `apply_patch` 로 handoff 산출물을 만들지 않는다 — 반드시 CLI 경유로 생성한다.

두 어댑터(Claude `/handoff`, Codex `$handoff`)의 on-disk 출력은 바이트 동일하다. 어느
writer 의 산출물이든 타 writer 가 list / find / resume / save 로 이어갈 수 있다.

## 2-tier 저장 모델

- `<project-root>/.handoff/<topic>/` — 토픽별 상세 본문(상세 정본).
- `~/.codex/handoffs/<project-name>/CURRENT.md` — Codex 의 프로젝트 전 active 토픽 집계 인덱스(파생).
- Claude Code 는 같은 정본을 읽되 자체 기본 인덱스 `~/.claude/handoffs/<project-name>/CURRENT.md` 를 쓴다.
- 장기 기억 도구(선택) — 장기 결정·반복 블로커·재사용 결론.

writer-local 인덱스는 파생물이라 누락·skip 돼도 상세 정본에서 재생성된다. `handoff` 는 네트워크
연산(fetch/pull/push)을 하지 않는다 — 충돌·원격앞섬이면 CLI 가 인덱스만 skip 하고 경고한다.

## 세션 없이 이어받기 체크리스트 (저장 전 필수)

세션은 머신 간 동기화하지 않는다(2026-06-05 결정). 핸드오프 하나만 보고 다른 세션·다른 머신에서
이어갈 수 있어야 한다. 저장 전 아래를 대화 맥락에서 **채우거나 — 없으면 사용자에게 묻는다**
(빈칸 boilerplate 채우기 금지):

1. **현재 목표 + 왜 이 방향인지**(대안 대비) → `summary` + `## Decisions`
2. **완료 / 미완료** → `## Done` / `## Open`. 완료 항목은 가능한 한 **확인 증거**를 함께 적는다
   (예: `— 확인: 테스트 통과`). 증거 없으면 Done 대신 Open/Not Tried 로.
3. **다음 한 행동** → `## Exact Next Step` (구체적·즉시 실행 가능. 모호하면 묻기)
4. **블로커** → `## Blockers And Questions` (없으면 "현재 블로커 없음.")
5. **검증 상태** → `## Verification` (완료 항목을 **무엇으로** 확인했는지 명시 / 미검증)
6. **관련 결정** → 장기 기억 도구에 기록했으면 `## Decisions` 에 포인터 명시
7. **유망하나 아직 안 해본 접근** → `## Not Tried Yet`

**`summary` 한 줄은 항상 실질적으로 채운다.** Codex-local CURRENT.md 인덱스가 `summary` +
`## Exact Next Step`·`## Blockers And Questions` 의 첫 줄을 뽑아 "지금 뭐 / 다음 뭐 / 막힌 것"을
보여준다 — 비면 인덱스가 "(요약 없음)" 으로 빈약해지고 다른 머신에서 상황 파악이 안 된다.

### Body Template (CLI 가 조립, 어댑터는 섹션 내용 제공)

CLI 가 frontmatter 와 다음 10섹션을 조립한다. 각 마크다운 헤딩 옆의 JSON key 는 아래
`save` payload 의 `sections` 안에 채워 넘긴다:

```markdown
## Done              → sections.done
## Open               → sections.open
## Failed Attempts    → sections.failed_attempts
## Not Tried Yet       → sections.not_tried
## Blockers And Questions → sections.blockers
## Git State          → (sections 아님 — CLI 가 git meta 로 자동 생성)
## Files Touched       → (sections 아님 — top-level files_touched 배열)
## Decisions          → sections.decisions
## Exact Next Step     → sections.exact_next_step
## Verification        → sections.verification
```

## CLI 호출

설치 전제(머신당 1회): jk-handoff 레포에서 `pip install -e .` 를 한 번 실행하면
`handoff_cli` 가 전역 import 가능해져 아래 호출이 PYTHONPATH 없이 동작한다.

```bash
python -m handoff_cli --cwd "$PWD" save           # JSON 페이로드를 stdin 으로(source=codex 기본 인덱스: ~/.codex)
python -m handoff_cli --cwd "$PWD" list           # --all 로 archived 포함
python -m handoff_cli --cwd "$PWD" find --keyword "<k>"   # 글로벌: --global-scope ~/projects ~/work (하위 트리 .handoff/ 전부 read-only)
python -m handoff_cli --cwd "$PWD" resume --topic "<t>"
python -m handoff_cli --cwd "$PWD" archive --topic "<t>"
```

`save` JSON 페이로드:

```json
{
  "topic": "<slug>",
  "source": "codex",
  "status": "active | waiting | watching | done",
  "summary": "<한 줄 요약>",
  "lang": "ko | en",
  "sections": {
    "done": "...", "open": "...", "failed_attempts": "...", "not_tried": "...",
    "blockers": "...", "decisions": "...",
    "exact_next_step": "...", "verification": "..."
  },
  "files_touched": [{"path": "...", "state": "complete", "note": "..."}]
}
```

`source` 는 반드시 `codex` 로 둔다. `status` 는 대화 맥락에서 판단한다(진행 중=active,
대기=waiting, 관망=watching, 종료=done). CLI 가 기존 open/open_planning/closed/CLOSED 도
정규화하므로 레거시 detail 과 호환된다.

## 동작 원칙

1. 저장 전 루트(`project_root`)와 토픽을 확인한다. 프로젝트나 토픽이 모호하면 쓰기 전에
   사용자에게 확인한다 — 자동선택하지 않는다.
2. **저장 결과의 `report` 문자열을 한 글자도 바꾸지 말고 그대로 출력한다.** `report` 에 저장 확인 ·
   복붙용 이어가기 프롬프트(```text 코드블럭) · 경고가 모두 들어 있다 — 자유 서술로 다시 쓰지 않는다.
3. `concurrent_conflict` 가 true 면 `report` 가 충돌 안내(resume 블록 없음)다. 그대로 전달하고
   두 최신본 중 어느 체인을 최신으로 할지 확인한다.
4. resume 시 CLI 의 `git_drift` 가 있으면 어긋남을 먼저 보고하고 어느 상태에서 이어갈지 확인한다.
5. 작업 로그·핸드오프 본문은 사용자의 언어로 작성한다. 코드·경로·식별자·인용 영문은 원어
   그대로 둔다. `save` payload 에 사용자의 대화 언어에 맞는 `"lang"`(`"ko"`/`"en"`)을
   함께 전달한다 — 미전달 시 CLI 가 env `HANDOFF_LANG` → OS locale → `en` 순으로 해석한다.
6. 장기 기억 도구 기록은 장기 가치(설계/제품 결정, 반복 블로커, 다음 라운드 필수 사실)일 때만 한다.
