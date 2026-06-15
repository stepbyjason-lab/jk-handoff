---
description: 세션 핸드오프 - 프로젝트 안에 상세 정본을 저장하고 글로벌 진행상황 인덱스를 갱신하며, 다음 세션·다른 머신에서 안전하게 이어간다.
version: 0.2
model: sonnet
---

# /handoff - 2-tier 세션 핸드오프 (Claude 어댑터)

여러 세션·여러 머신을 오가는 환경에서 작업의 현재 상태를 넘기는 명령이다. 이 문서는
**Claude Code 어댑터**로, 모든 파일쓰기는 공용 Python CLI(`core/handoff_cli`)에 위임한다.
어댑터는 (1) 현재 대화에서 Done/Open/결정/다음행동을 판단해 사용자의 언어로 narrative 를 만들고,
(2) CLI 에 구조화 JSON 을 넘기고, (3) CLI 출력·경고를 사용자에게 보고한다.

Codex 어댑터(`codex/handoff/SKILL.md`)와 **같은 CLI 를 공유**한다. 두 어댑터의 on-disk
출력은 바이트 동일하며(narrative 본문 텍스트만 다를 수 있음), 어느 writer 의 산출물이든
타 writer 가 list/find/resume/save 할 수 있다.

`/hd` 같은 정적 컨텍스트 로더와 역할이 다르다. `/handoff`는 이번 작업의 완료 사항, 막힌
지점, 다음 행동을 남기는 동적 기록이다.

## 2-tier 저장 모델

| 저장소 | 역할 | 정본 |
|---|---|---|
| `<project-root>/.handoff/<topic>/` | 토픽별 상세 본문·체인 | **상세 정본** |
| `~/.claude/handoffs/<project-name>/CURRENT.md` | 그 프로젝트 전 active 토픽 집계 인덱스 | 파생(CLI가 전 토픽 재스캔으로 재생성, 누락·skip 돼도 상세 정본에서 언제든 재생성됨) |
| 장기 기억 도구(선택) | 장기 결정·반복 블로커·재사용 결론 | 장기 기억 |

## Core Rules

1. **Source of truth 는 현재 프로젝트의 `.handoff/`다.**
   - 기본 루트: 현재 `cwd`의 프로젝트 마커를 우선하고, 없으면 git 저장소 루트.
   - 명시 루트: `/handoff --root <path> ...`.
   - 하위 프로젝트에서 `~/projects` 같은 상위 폴더로 자동 승격하지 않는다.
2. **글로벌 `~/.claude/handoffs/<project-name>/`에는 CURRENT.md 인덱스 1장만 둔다.** 상세
   본문(timestamped)은 여전히 글로벌에 만들지 않는다 — 프로젝트 `.handoff/`에만.
3. **`INDEX.md`·`CURRENT.md`는 캐시·파생이다.** 정본은 각 토픽 `LATEST.md` 스캔 결과다.
4. **장기 기억 도구는 원본 문서 저장소가 아니다.** 장기 결정·반복 블로커·재사용 결론만 source 와
   함께 기록한다(상세는 "Durable Memory" 절 참조). auto-memory 포인터를 자동 추가하지 않는다.
5. **동시 저장으로 본문을 덮어쓰지 않는다.** CLI 가 신규 본문을 보존하고, LATEST 충돌이
   보이면 갱신을 중단·보고한다 — 사용자에게 어느 체인을 최신으로 할지 확인한다.
6. **작업 로그·핸드오프 본문은 사용자의 언어로 작성한다.** (2026-05-31 사용자 지정,
   2026-07 언어체인 도입)
   - 코드·명령어·경로·식별자(slug·frontmatter 키)·인용 영문 원문은 원어 그대로 둔다.
   - `save` payload 에 사용자의 대화 언어에 맞는 `"lang"`(`"ko"`/`"en"`)을 함께
     전달한다 — report·resume_prompt·본문 기본값·경고·인덱스 장식 텍스트가 그
     언어로 렌더링된다. 미전달 시 CLI 가 env `HANDOFF_LANG` → OS locale → `en`
     순으로 자동 해석한다.
7. **`/handoff`는 네트워크 연산(fetch/pull/push)을 하지 않는다.** 네트워크 동기화는 `/sync`
   책임이다. 글로벌 갱신이 충돌·원격앞섬으로 막히면 CLI 가 글로벌만 skip 하고 경고한다.

## Usage / CLI 위임

어댑터는 직접 Write/Edit 로 handoff 파일을 만들지 않는다 — 아래 CLI 호출로만 동작한다.
(설치 전제, 머신당 1회: jk-handoff 레포에서 `pip install -e .` 를 실행하면 `handoff_cli`
가 전역 import 가능해져 PYTHONPATH 없이 `python -m handoff_cli` 가 동작한다.)

```text
/handoff                              # 현재 프로젝트 active 목록 + 이 세션 토픽 제안
/handoff save                         # 토픽이 명백하면 바로 저장 → python -m handoff_cli --cwd <cwd> save (JSON stdin)
/handoff <topic> [description]        # 지정 토픽[+한 줄 요약]으로 저장
/handoff list                         # 현재 프로젝트 토픽 목록(LATEST 스캔)
/handoff list --all                   # archived 포함
/handoff find <keyword>               # 현재 프로젝트 검색 → find --keyword <k>
/handoff find --global <keyword>      # 등록된 프로젝트 루트 read-only 검색 → find --global-scope <root>...
/handoff resume <topic>               # 최신 상태 로드 → resume --topic <t>
/handoff archive <topic>              # archived/ 로 이동 → archive --topic <t>
/handoff --root <path> <mode...>      # git 루트가 아닌 명시 루트 사용 (위 CLI 커맨드는 모두 python -m handoff_cli --cwd <cwd> 접두)
```

전역 설정·공용 스킬·여러 프로젝트 횡단 운영 작업이 토픽이면, 사용자가 운영 루트를 명시하거나
현재 작업 디렉터리가 그 운영 루트일 때만 그곳에 저장한다.

`save` JSON 페이로드 핵심 필드: `topic`(slug) · `root`(선택, --root 절대경로) ·
`source: "claude-code"` · `status`(active/waiting/watching/done — 진행 중=`active`, 사용자
입력·외부 의존 대기=`waiting`, 나중에 볼 관망=`watching`, 종료=`done`; CLI 가 레거시
`open`/`open_planning`/`closed`/`CLOSED` 값도 정규화) · `summary`(한 줄 요약) ·
`sections`(Body Template 10섹션 키와 동일 — 아래 참조) · `files_touched`(`path`/`state:
complete|in-progress|broken|read-only`/`note` 배열) · `lang`(선택, `"ko"`/`"en"` — 사용자의
대화 언어. 생략하면 CLI 가 env/OS locale 로 자동 해석).

CLI 결과의 `warnings` 배열을 **빠짐없이 사용자에게 보고**한다(미커밋 `.project-id`, 동시성 충돌,
글로벌 skip, secret redaction, orphan 등).

## Main Entry (`/handoff`)

`python -m handoff_cli list` 로 active 토픽을 스캔해 보여주고(파일 수정 없음), 현재 세션
수정 파일·`cwd`·사용자 주제로 토픽을 제안한다. CLI 출력의 `project_root`/`handoff store` 를
첫 줄에 보고한다: `project root: <absolute path>` / `handoff store: <project root>/.handoff/`.

## Save (`/handoff save` 또는 `/handoff <topic>`)

1. 루트를 결정·보고한다(CLI `project_root`).
2. 토픽을 검증한다(CLI 가 한글·소문자정규화·traversal 거부).
3. 대화에서 10섹션 narrative 와 `status` 를 판단해 JSON 페이로드를 만든다.
4. CLI `save` 를 호출한다. CLI 가 수행: `.project-id` 생성(save 경로에서만)/읽기 · git
   branch·full commit·dirty·시각 실측 · 상세 본문 저장(기존 파일 덮어쓰지 않음, 원자교체) ·
   `LATEST.md`·`INDEX.md` 재생성 · 글로벌 CURRENT.md 를 전 active 토픽 집계로 재생성
   (best-effort, 네트워크 없음).
5. **CLI 결과의 `report` 문자열을 한 글자도 바꾸지 말고 그대로 출력한다.** 저장 확인·복붙용
   이어가기 프롬프트(```text 코드블럭)·경고가 모두 들어 있다 — 자유 서술로 다시 쓰지 않는다.
   (`concurrent_conflict: true` 면 `report` 가 충돌 안내이고 resume 블록이 없다. 그대로 전달하고
   두 최신본 중 어느 체인을 최신으로 할지 확인받는다.)

### 세션 없이 이어받기 체크리스트 (저장 전 필수)

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

**`summary` 한 줄은 항상 실질적으로 채운다.** 글로벌 CURRENT.md 인덱스가 `summary` +
`## Exact Next Step`·`## Blockers And Questions` 의 첫 줄을 뽑아 "지금 뭐 / 다음 뭐 / 막힌 것"을
보여준다 — 비면 인덱스가 "(요약 없음)" 으로 빈약해지고 다른 머신에서 상황 파악이 안 된다.

### 자동 토픽 결정 트리

1. active 토픽이 하나뿐이고 수정 파일·세션 주제가 일치하면 자동 선택.
2. active 토픽이 둘 이상이면 후보·근거 제시 후 확인.
3. active 토픽이 없으면 새 토픽명을 받는다.
4. 수정 파일이 여러 프로젝트에 걸치면 `--root`·토픽 확인 전 저장하지 않는다.

### Body Template (CLI 가 조립, 어댑터는 섹션 내용 제공)

CLI 가 frontmatter(topic/created/project_root/status/prev/source/git_branch/git_commit/
git_dirty — 어댑터가 작성 안 함, CLI 가 실측해 생성)와 다음 10섹션을 조립한다. 어댑터는
각 섹션의 내용을 사용자의 언어로 채운다:

```markdown
## Done
## Open
## Failed Attempts
## Not Tried Yet
## Blockers And Questions
## Git State
## Files Touched
## Decisions
## Exact Next Step
## Verification
```

### Durable Memory

장기 기억 스킬/MCP(예: memory 계열 도구)가 있으면 장기 설계/제품 결정·반복 블로커·다음
라운드 필수 사실만 source 포인터와 함께 기록한다. 없으면 Claude Code auto-memory 를 같은
규율로 활용한다. 둘 다 없으면 생략해도 된다 — 상세 정본(`.handoff/`)이 이미 영구 기록이다.
단순 진행 목록·일회성 로그는 복제하지 않는다.

## List / Find / Resume / Archive

- **List** — `python -m handoff_cli list [--all]`. 스캔 결과가 정본. 사용자 동의 없이 과거
  본문을 수정하지 않는다.
- **Find** — 프로젝트 로컬 검색. `--global` 은 등록된 작업 루트를 `--global-scope ~/projects
  ~/work` 로 CLI 에 넘기면, CLI 가 각 스코프 **하위 트리의 모든 `.handoff/`** 를 read-only
  검색하며 파일 생성·인덱스 갱신·archive 이동을 하지 않는다.
- **Resume** — CLI 가 LATEST→본문→`prev` 1~2개를 읽고 git drift 를 비교, 브랜치가 다르거나
  HEAD 이동(특히 저장 시 `git_dirty: true`)이면 어긋남을 **먼저 보고**하고 어느 상태에서
  이어갈지 확인한다. broken 포인터면 임의 파일을 최신으로 고르지 않고 보고한다.
- **Archive** — 토픽을 `archived/`로 이동(대상 존재 시 중단), `INDEX.md` 재생성. 다른 프로젝트
  기록·장기 기억 도구·auto-memory 는 자동 삭제하지 않는다.

## Legacy Migration / Round Contracts

이전 `~/.claude/handoffs/<topic>/` 본문은 소유 프로젝트가 명확할 때만 `<project-root>/.handoff/`로
한 번 이전한다. 애매하면 원위치 보류·보고. 레거시 글로벌 `INDEX.md`는 이력으로 보존하되 정본으로
쓰지 않는다. Codex-Claude 라운드의 `.handoff/round-N-*-contract.md`/`result.md`/`review.md`도
같은 프로젝트 로컬 원칙을 따른다 — `/handoff` 기록은 이 산출물을 요약·참조할 수 있으나 대체하지 않는다.
