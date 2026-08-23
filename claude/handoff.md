---
description: 세션 핸드오프 - 프로젝트 안에 상세 정본을 저장하고 글로벌 진행상황 인덱스를 갱신하며, 다음 세션·다른 머신에서 안전하게 이어간다.
version: 0.4.3
model: sonnet
---

# /handoff - 2-tier 세션 핸드오프 (Claude 어댑터)

여러 세션·여러 머신을 오가며 작업 상태를 넘기는 명령. **Claude Code 어댑터**로, 모든
파일쓰기는 공용 Python CLI(`core/handoff_cli`)에 위임한다. 어댑터는 (1) 대화에서
Done/Open/결정/다음행동을 판단해 사용자의 언어로 narrative 를 만들고, (2) CLI 에 구조화 JSON 을
넘기고, (3) CLI 출력·경고를 보고한다. Codex 어댑터(`codex/handoff/SKILL.md`)와 **같은 CLI
를 공유**하며 `source:` frontmatter 줄과 narrative 를 빼면 on-disk 구조·순서·헤딩이 동일하다.
`source:` 줄만 writer 에 따라 다르며, 어느 writer 의 산출물이든 타 writer 가 list/find/resume/save
할 수 있다. `/hd` 같은 정적 컨텍스트
로더와 달리, `/handoff`는 완료 사항·막힌 지점·다음 행동을 남기는 동적 기록이다.

## 2-tier 저장 모델

| 저장소 | 역할 | 정본 |
|---|---|---|
| `<project-root>/.handoff/<topic>/` | 토픽별 상세 본문·체인 | **상세 정본** |
| `~/.claude/handoffs/<project-name>/CURRENT.md` | 그 프로젝트 전 active 토픽 집계 인덱스 | 파생(CLI가 전 토픽 재스캔으로 재생성, 누락·skip 돼도 상세 정본에서 언제든 재생성됨) |
| 장기 기억 도구(선택) | 장기 결정·반복 블로커·재사용 결론 | 장기 기억 |

## Core Rules

1. **Source of truth 는 현재 프로젝트의 `.handoff/`다.** 기본 루트: 현재 `cwd`의 프로젝트
   마커를 우선하고, 없으면 git 저장소 루트. 명시 루트: `/handoff --root <path> ...`. 하위
   프로젝트에서 `~/projects` 같은 상위 폴더로 자동 승격하지 않는다.
2. **글로벌 `~/.claude/handoffs/<project-name>/`에는 CURRENT.md 인덱스 1장만 둔다.** 상세
   본문(timestamped)은 여전히 글로벌에 만들지 않는다 — 프로젝트 `.handoff/`에만.
3. **`INDEX.md`·`CURRENT.md`는 캐시·파생이다.** 정본은 각 토픽 `LATEST.md` 스캔 결과다.
4. **장기 기억 도구는 원본 문서 저장소가 아니다.** 장기 결정·반복 블로커·재사용 결론만 source 와
   함께 기록한다(상세는 "Durable Memory" 절 참조). auto-memory 포인터를 자동 추가하지 않는다.
5. **동시 저장으로 본문을 덮어쓰지 않는다.** CLI 가 신규 본문을 보존하고, LATEST 충돌이
   보이면 갱신을 중단·보고한다 — 사용자에게 어느 체인을 최신으로 할지 확인한다.
6. **작업 로그·핸드오프 본문은 사용자의 언어로 작성한다**(2026-05-31 사용자 지정,
   2026-07 언어체인 도입). 코드·명령어·경로·식별자(slug·frontmatter 키)·인용 영문
   원문은 원어 그대로 둔다. `save` payload 에 사용자의 대화 언어에 맞는 `"lang"`
   (`"ko"`/`"en"`)을 함께 전달한다 — report·resume_prompt·본문 기본값·경고·인덱스
   장식 텍스트가 그 언어로 렌더링된다. 미전달 시 CLI 가 env `HANDOFF_LANG` → OS
   locale → `en` 순으로 자동 해석한다.
7. **`/handoff`는 네트워크 연산(fetch/pull/push)을 하지 않는다.** 네트워크 동기화는 `/sync`
   책임이다. 글로벌 갱신이 충돌·원격앞섬으로 막히면 CLI 가 글로벌만 skip 하고 경고한다.
8. **하네스 주입 요약은 이 세션의 작업이 아니다(재개 오염 방어).** 세션 시작 시 주입되는
   "PRIOR-SESSION SUMMARY"·"Previous session summary" 류 컨텍스트는 다른 세션·다른 프로젝트의
   기록일 수 있다 — 토픽·루트·narrative 판단의 근거로 쓰지 않는다. `files_touched`에는 이
   세션에서 실제로 만지거나 확인한 파일만 넣는다(주입된 요약에서 옮기지 않는다). `state:
   read-only`는 유효하다 — 기준은 "수정"이 아니라 "이 세션 실작업 여부". 저장 대상 프로젝트가
   대화의 실작업과 달라 보이면(cwd ≠ 작업 파일 소속) 저장 전 사용자에게 확인한다. CLI 가
   교차 프로젝트 경고(`warn_cross_project_files`)를 내면 그대로 보고하고 진행 전 확인한다.

## Usage / CLI 위임

어댑터는 직접 Write/Edit 로 handoff 파일을 만들지 않는다 — 아래 CLI 호출로만 동작한다.
(설치 전제, 머신당 1회: jk-handoff 레포에서 `pip install -e .` 실행 시 `handoff_cli` 가 전역
import 가능해져 PYTHONPATH 없이 `python -m handoff_cli` 가 동작한다.)

```text
/handoff                              # 현재 프로젝트 active 목록 + 이 세션 토픽 제안
/handoff save                         # 자동 토픽 결정 트리 적용 (토픽이 명백하면 바로 저장)
/handoff save --delta                 # 직전 저장 이후만 (기존 저장본 유무와 무관하게 강제)
/handoff save --full                  # 세션 전체 (저장본이 있어도 1번부터 다시)
/handoff <topic> [description]        # 지정 토픽[+한 줄 요약]으로 저장
/handoff list                         # 현재 프로젝트 토픽 목록(LATEST 스캔)
/handoff list --all                   # archived 포함
/handoff find <keyword>               # 현재 프로젝트 검색 → find --keyword <k>
/handoff find --global <keyword>      # 등록된 프로젝트 루트 read-only 검색 → find --global-scope <root>...
/handoff resume <topic>               # 최신 상태 로드 → resume --topic <t>
/handoff archive <topic>              # archived/ 로 이동 → archive --topic <t>
/handoff decisions [--id <ID>] [--all]  # 결정 색인 — ID 하나로 그 결정의 일생(생사·관계·체인). 읽기 전용
/handoff negative [--all]             # 부정 색인 — 실패·폐기·죽은 결정만. 「이거 해봤나?」 (읽기 전용)
/handoff --root <path> <mode...>      # git 루트가 아닌 명시 루트 사용 (위 CLI 커맨드는 모두 python -m handoff_cli --cwd <cwd> 접두)
```

전역 설정·공용 스킬·여러 프로젝트 횡단 운영 작업이 토픽이면, 사용자가 운영 루트를 명시하거나
현재 작업 디렉터리가 그 운영 루트일 때만 그곳에 저장한다.

`save` JSON 페이로드 핵심 필드: `topic`(slug) · `root`(선택, --root 절대경로) ·
`source: "claude-code"` · `status`(active/waiting/watching/done — 진행 중=`active`, 사용자
입력·외부 의존 대기=`waiting`, 나중에 볼 관망=`watching`, 종료=`done`; CLI 가 레거시
`open`/`open_planning`/`closed`/`CLOSED` 값도 정규화) · `summary`(한 줄 요약) ·
`sections`(Body Template 14절 키와 동일 — `sections.unapproved` 포함, 아래 참조) · `files_touched`(`path`/`state:
complete|in-progress|broken|read-only`/`note` 배열) · `lang`(선택, `"ko"`/`"en"` — 사용자의
대화 언어. 생략하면 CLI 가 env/OS locale 로 자동 해석).

**대장·저작 조건 필드(R8)**: `session_id` · `transcript`(명시 경로가 있을 때만) ·
`transcript_format`(`claude`/`codex`) · `covers_from`(델타 저장의 시작 경계, 없으면 `null`) ·
**`utterance_ledger`**(처분 배열 — 규율 2항) · **`decisions`**(결정 배열 — 규율 4항. `sections.decisions`·`sections.unapproved` 는 이 배열이 있으면 CLI 가 만든다) · `writer_effort`.
호스트별 값은 어댑터가 이 중립 이름으로 번역해 넘긴다 — 코어가 호스트 변수 이름을 알면
벤더가 늘 때마다 코어가 바뀐다.

**`writer_model` 은 넘기지 않는다.** CLI 가 트랜스크립트에서 **실측**한다 — 신고값이 실제
저작 모델과 달랐던 실측 사고가 있다(`claude-opus-5` 로 적혔으나 실제는 `claude-sonnet-5`).

CLI 결과의 `warnings` 배열을 **빠짐없이 사용자에게 보고**한다(미커밋 `.project-id`, 동시성 충돌,
글로벌 skip, secret redaction, orphan 등).

## Main Entry (`/handoff`)

`python -m handoff_cli list` 로 active 토픽을 스캔해 보여주고(파일 수정 없음), 현재 세션
수정 파일·`cwd`·사용자 주제로 토픽을 제안한다. CLI 출력의 `project_root`/`handoff store` 를
첫 줄에 보고: `project root: <absolute path>` / `handoff store: <project root>/.handoff/`.

## Save (`/handoff save` 또는 `/handoff <topic>`)

1. 루트를 결정·보고한다(CLI `project_root`).
2. 토픽을 검증한다(CLI 가 한글·소문자정규화·traversal 거부).
3. **「발화 대장 획득」을 먼저 실행한다** — 다른 절을 쓰기 전이다(순서가 뒤집히면 기억으로 쓰고
   대장으로 사후 정당화하게 된다).
4. 대장을 근거로 14절 narrative 와 `status` 를 판단해 JSON 페이로드를 만든다. 대장을 얻었으면
   `session_id`·`transcript_format`·`covers_from` 을 함께 넣는다(`transcript` 는 명시 경로가
   있을 때만).
5. CLI `save` 를 호출한다. CLI 가 수행: `.project-id` 생성(save 경로에서만)/읽기 · git
   branch·full commit·dirty·시각 실측 · 상세 본문 저장(기존 파일 덮어쓰지 않음, 원자교체) ·
   `LATEST.md`·`INDEX.md` 재생성 · 글로벌 CURRENT.md 를 전 active 토픽 집계로 재생성
   (best-effort, 네트워크 없음).
6. **CLI 결과의 `report` 문자열을 한 글자도 바꾸지 말고 그대로 출력한다** — 저장 확인·복붙용
   이어가기 프롬프트(```text 코드블럭)·경고가 모두 들어 있다, 자유 서술로 다시 쓰지 않는다.
   `concurrent_conflict: true` 면 `report` 가 충돌 안내(resume 블록 없음)이니 그대로 전달하고
   두 최신본 중 어느 체인을 최신으로 할지 확인받는다.

### 세션 없이 이어받기 체크리스트 (저장 전 필수)

세션은 머신 간 동기화하지 않는다(2026-06-05 결정) — 핸드오프 하나만 보고 다른 세션·다른
머신에서 이어갈 수 있어야 한다. 저장 전 아래를 대화 맥락에서 **채우거나 — 없으면 사용자에게
묻는다**(빈칸 boilerplate 채우기 금지):

1. **현재 목표** → `summary`; **왜 이 방향인지**(대안 대비, Chair 추론) → `## Unapproved Proposals`
2. **완료 / 미완료** → `## Done` / `## Open`. 완료 항목은 가능한 한 **확인 증거**를 함께 적는다
   (예: `— 확인: 테스트 통과`) — 증거 없으면 Done 대신 Open/Not Tried 로.
3. **다음 한 행동** → `## Exact Next Step`(구체적·즉시 실행 가능. 모호하면 묻기)
4. **블로커** → `## Blockers And Questions`(없으면 "현재 블로커 없음.")
5. **검증 상태** → `## Verification`(완료 항목을 **무엇으로** 확인했는지 명시 / 미검증)
6. **관련 결정** → 장기 기억 도구에 기록했으면 `## Unapproved Proposals` 에 포인터와 근거 명시
7. **유망하나 아직 안 해본 접근** → `## Not Tried Yet`

### Decisions / Unapproved Proposals 규율

1. `sections.decisions` 는 사용자 발화 원문 인용만. D-3 경계를 따른다.
2. `sections.unapproved` 에 Chair 가 정한 것과 **근거**를 함께 적는다.
3. `## Open` 각 항목에 **완료 조건**을 반증 가능한 문장으로. "즉시 적용한다" 류는 무효.
4. 저장 전, 이번 대화에서 사용자가 답한 질문을 훑어 답이 `Decisions` 에 원문으로 들어갔는지 확인한다.
5. **Resume 시 두 절을 반드시 읽고 보고에 반영한다** — `## Decisions` 는 사용자 확정 원문으로
   그대로 존중하고, `## Unapproved Proposals` 는 미승인이므로 실행 전 사용자에게 확인한다.

**`summary` 한 줄은 항상 실질적으로 채운다** — 글로벌 CURRENT.md 인덱스가 `summary` +
`## Exact Next Step`·`## Blockers And Questions` 첫 줄을 뽑아 "지금 뭐/다음 뭐/막힌 것"을
보여주므로, 비면 인덱스가 "(요약 없음)" 으로 빈약해져 다른 머신에서 상황 파악이 안 된다.

### 자동 토픽 결정 트리

1. active 토픽이 하나뿐이고 수정 파일·세션 주제가 일치하면 자동 선택.
2. active 토픽이 둘 이상이면 후보·근거 제시 후 확인.
3. active 토픽이 없으면 새 토픽명을 받는다.
4. 수정 파일이 여러 프로젝트에 걸치면 `--root`·토픽 확인 전 저장하지 않는다.

### 발화 대장 획득 (Claude Code)

세션 식별자는 런타임이 준다. 트랜스크립트 경로는 CLI 가 `--cwd` 로 유도하므로 넘기지 않아도 된다.

```bash
python -m handoff_cli --cwd "$PWD" utterances \
  --session "$CLAUDE_CODE_SESSION_ID" \
  --topic "<확정 토픽>" [--delta | --full]
```

사용자가 `/handoff save --delta`·`--full` 을 줬으면 **그 플래그를 그대로 붙여 보낸다.**
안 줬으면 생략한다 — CLI 가 알아서 가른다(규율 7).

`--session` 값이 비어 있으면 이 호출을 생략하고 아래 규율 8 의 폴백으로 간다.

### 내용 보전 규율 (R8 — 두 어댑터 동일 문구)

**목표는 변곡점을 찾는 것이 아니라 세션의 내용이 전부 정리되었는가다.** 변곡점 기준은 폐기했다 —
정규화가 안 돼 모델마다 눈금이 갈렸고(같은 세션에 41 대 76), 뭉치는 압력이 **사용자 결정을
정반대로 적는 사고**를 만들었다. 이제 단위는 **발화**다. 발화 수는 코드가 세므로 의견이 아니다.

#### 1. 대장을 먼저 받는다

토픽을 확정한 **직후**, 다른 절을 쓰기 **전에** 대장을 받는다. 순서가 뒤집히면 기억에서 먼저
쓰고 대장으로 사후 정당화하게 되어 최신성 편향이 그대로 들어온다.

#### 2. 모든 UID 를 처분한다 — 목적지는 **절 이름**

`utterance_ledger` 배열로 넘긴다. 지문은 **CLI 가 넣으므로 적지 않는다**(옮겨 적다 바뀌면
「원문 그대로」가 깨진 걸 아무도 모른다).

```json
"utterance_ledger": [
  {"uid": "U0007", "section": "Failed Attempts", "note": "감사 문서 부분독 인정 → 통독으로"},
  {"uid": "U0012", "section": "없음",            "note": ""}
]
```

- `section` 은 **본문 절 이름 그대로** 또는 `없음`. 쓸 수 있는 값 열셋:
  `Intent And Purpose` · `Done` · `Open` · `Failed Attempts` · `Not Tried Yet` ·
  `Blockers And Questions` · `Decisions` · `Unapproved Proposals` · `Exact Next Step` ·
  `Verification` · `Incidents` · `Lessons` · `Standing Directives`
- `note` 는 **그 발화에서 무엇이 남았는지** 한 줄. 절 이름을 지목했으면 반드시 적고,
  `없음` 이면 이유를 적는다(아래 3항 — 길이 면제 없음). **질문·확인형 발화**
  (`Verification`·`Blockers` 처분)는 「무엇을 물었나 → 어떻게 답했나」까지 쌍으로 —
  질문의 답은 어느 절도 받아주지 않아서, note 가 안 적으면 답이 상실된다.
- **표를 만들지 않는다.** 마크다운으로 넘기면 CLI 가 정규식으로 파싱해야 하고, 그렇게 했다가
  형식 사고를 두 번 냈다(표 전용 검사가 목록에서 안 돌던 일, 앵커 수로 뭉침을 잡으려다 헛짚은 일).

**CLI 가 거부하는 것 넷**: 처분 안 된 UID(`ledger_uid_missing`) · 목적지가 절 이름도 `없음` 도
아님(`ledger_bad_destination`) · **지목한 절이 실제로 비어 있음**(`ledger_empty_target`) ·
절에 담았다면서 `note` 가 없음(`ledger_note_missing`).

#### 3. `없음` 은 「중요하지 않다」가 아니라 「남길 내용이 없다」다

**`없음` 으로 처분하는 발화 전부에 `note` 로 이유를 적는다.** 길이 면제는 없다.

**짧다고 안 중요한 게 아니다.** 세션에서 방향이 가장 크게 꺾이는 자리가 보통
`멈춰.`·`아니`·`취소` 다 — 두세 글자다. 길이로 면제하면 **가장 중요한 부류를 정확히
면제**하게 된다(이 프로젝트가 길이 문턱을 뒀다가 이 지적으로 폐기했다).

이유는 짧아도 된다 — `단순 승인`·`앞 지시 반복`·`도구 출력 붙여넣기` 면 충분하다.
요점은 **빈칸을 못 쓰게 하는 것**이다: 빈칸은 비용이 0이지만 거짓 이유를 지어내는 것은
비용이 있고, 무엇보다 **인접한 지문 옆에서 눈에 띈다.**

**비율 자체는 막지 않는다.** CLI 가 밀도 줄에 박아 보이게만 한다 — 직전 판에서 같은
자리(`유지`)가 69% 였고 **완전 소실 전부가 거기서 나왔다.** 그 수가 다시 나오면 이름만
바뀐 것이다.

#### 4. 결정 — **인용이 권위, 해석은 비권위**

**너는 결정 내용도 주체도 쓰지 않는다.** UID 를 가리키기만 하면 CLI 가 인용을 원문 그대로
넣고 주체를 파생한다. 이 형식은 **사용자 결정을 정반대로 적고 그것을 `누가: 사용자` 로
귀속시킨 사고가 세 번 재발**해서 나왔다.

`decisions` 를 **배열로** 넘긴다(`sections.decisions` 산문이 아니다).

```json
"decisions": [
  {"id": "D4", "source": ["U0029"],
   "interpretation": "P8을 R48g로 분리한다 (a안)",
   "relations": [{"token": "NARROWS", "target": "D3"}]},

  {"id": "D5", "source": [],
   "interpretation": "D4를 뒤집는다. closure 가 안 되기 때문",
   "relations": [{"token": "REVERSES", "target": "D4"}]}
]
```

- **`source`** — 그 결정을 만든 **사용자 발화 UID**. 여기 UID 가 있으면 CLI 가 주체를
  `user` 로 파생하고 `## Decisions` 에 넣는다.
- **`source` 가 비면** 주체는 `chair` 이고 **자동으로 `## Unapproved Proposals`** 로 간다.
  chair 가 정한 것을 사용자 것으로 귀속시킬 **필드가 없다.**
- **`interpretation`** — 네가 쓰는 유일한 줄. 본문에 **「해석(비권위)」로 표시**되어 렌더된다.
- **`id`·`target`** 은 `D1` 로 줄여 써도 CLI 가 완전형(`<프로젝트>-<토픽>-D1`)으로 정규화한다.
- 교훈 ID 는 `-L<번호>`.

**인용문을 옮겨 적지 마라.** CLI 가 대장 원문에서 넣는다. 옮겨 적다 바뀌면 「원문 그대로」가
깨진 걸 아무도 모른다.

**대장과 서로 가리켜야 한다.** 대장이 `Decisions` 로 처분한 UID 와 결정이 인용한 UID 가
**집합으로 같아야** 하며, 어긋나면 CLI 가 거부한다(`decision_ledger_mismatch`). 한쪽만 고치는
것이 불가능하다.

**관계 토큰은 닫힌 집합이다.** 가르는 축은 「이전 것이 아직 살아 있나」.

| 군 | 토큰 | 뜻 |
|---|---|---|
| **이전 것이 죽는다** | `REVERSES` | 정반대로 뒤집는다 |
| | `SUPERSEDES` | 목적은 같고 방법을 갈아탄다 |
| | `ABANDONS` | 대체 없이 접는다 |
| **이전 것이 살아 있다** | `NARROWS` | 적용 범위를 줄인다 |
| | `DEFERS` | 시점을 미룬다 |
| | `TRANSFERS` | 소유를 옮긴다 |
| | `CONFIRMS` | 여전히 유효함을 확인한다 |
| **이전 것이 안 끝났다** | `RETRIES` | 앞이 실패해서 다른 방법으로 다시 |
| | `CONTINUES` | 앞이 끝나 다음 단계로 |
| | `SPLITS` | 하나를 여럿으로 쪼갠다 |
| | `RESOLVES` | **목표를 실제로 달성해 체인을 닫는다** |

- **새 결정 쪽에 적고 옛 문서는 건드리지 않는다.** 색인이 역방향을 파생한다.
- `RESOLVES` 는 스레드 맨 위로 답을 끌어올린다 — 다섯 번 재시도한 목표도 조회하면
  `[resolved by …]` 가 먼저 보여 끝까지 안 읽어도 답을 안다. **성공했으면 반드시 적는다.**
- 닫힌 집합에 안 맞으면 `RELATES` + `note` 에 산문 한 줄. **토큰을 임의로 만들지 않는다.**

> **사용자 선택을 네가 뒤집었으면 반드시 별도 항목이다.** 원래 결정의 `interpretation` 을
> 고쳐 쓰지 마라 — 그게 세 번 재발한 사고의 형태다. `source: []` 로 새 항목을 만들고
> `REVERSES` 로 가리켜라. 그러면 `Unapproved Proposals` 에 미승인으로 남는다.

#### 5. `## Lessons` — 다섯 칸을 채운다. 0건이 정상이다

**판별 한 줄: 이 프로젝트를 몰라도 쓸모 있는가.** 프로젝트 맥락이 필요하면 교훈이 아니라 상태다.

```markdown
### <프로젝트>-<토픽>-L1 — 한 줄 제목
- **언제**: 이 교훈을 떠올려야 할 상황
- **무엇**: 하라 / 하지 마라
- **왜**: 안 적으면 다음 사람이 무시한다
- **증거**: 실측 / 추론 / 1회 관측 — 값이나 재현 경로
- **대신**: 막았으면 대안을 준다
```

**「언제」가 맨 앞인 이유**: 꺼내 쓰는 열쇠다. 나머지가 좋아도 이게 없으면 필요한 순간에 못 찾는다.
개수 상한은 없다 — 여덟 개를 배웠으면 여덟 개를 쓴다. 다만 **배운 게 없는 세션이 훨씬 많다.**

#### 6. 범위는 사용자가 정한다. 저장을 거부하지 않는다

사용자가 `--delta`·`--full` 을 주면 **기존 저장본이 있든 없든 그대로 실행한다.** 플래그가 없을
때만 `scope` 가 자동으로 갈리는데, 기준은 **직전 저장본을 쓴 세션이 지금 이 세션인가**
(`same_session_resave`)이지 「저장본이 있는가」가 아니다.

| 사용자 입력 | 뜻 |
|---|---|
| `save --delta` | 직전 저장 이후만. 직전이 다른 세션이어도 강제 |
| `save --full` | 세션 전체. 저장본이 있어도 무시 |
| `save` | 같은 세션 2회차면 델타, 아니면 전체 |

**`LATEST.md` 가 이미 있다는 것만 보고 「중복이니 저장하지 않겠다」고 판단하지 말 것** —
실제로 그렇게 거부한 사고가 있다. 중복이 걱정되면 거부가 아니라 **보고**한다.

#### 7. 거부되면 1회 재시도, 그래도 걸리면 강등 저장

`ok: false` + `schema_problems` 로 돌아오면 **지적된 항목만** 고쳐 **1회** 재제출한다. 그래도
걸리면 `force_schema: true` 로 저장하고, 사용자에게 강등 사실과 위반 목록을 보고한다.
**처분을 `없음` 으로 바꿔 통과시키지 않는다** — 그건 목적을 정확히 뒤집는다.

#### 8. 대장을 얻지 못했으면 전수 검증을 주장하지 않는다

세션 식별자가 없거나 트랜스크립트를 못 찾았으면 `session_id`·`transcript`·
`transcript_format`·`covers_from`·`utterance_ledger` 를 전부 생략하고 저장을 계속하되,
**「전부 정리했다」고 말하지 않는다.** frontmatter 의 `writer_session: null` 이 보증 없음을
그대로 드러낸다.

#### 9. 상시 규율(`standing`) — 다음 세션에도 참이어야 하는 규칙만

**판별 한 줄: 이 지시가 다음 세션에도 참이어야 하는가.** 예면 여기, 아니오면 `decisions` 다.
형식은 결정과 같다(인용이 권위) — `id` 는 `S<번호>`, `source` 에 그 규칙을 건 사용자 발화
UID. **`source` 없는 상시 규율은 거부된다**(`standing_source_missing`) — 다음 세션을 구속하는
힘은 사용자 발화에서 나오므로 chair 가 스스로 만들 수 없다.

```json
"standing": [
  {"id": "S1", "source": ["U0031"],
   "interpretation": "교훈은 칸채우기 형식으로 받는다"}
]
```

- **승계는 CLI 가 한다. 다시 선언하지 마라** — 재저장 때 이전 저장본의 규율이 자동으로
  실려 온다. 네가 옮겨 적으면 빠뜨리는 그 사고(대장이 막은 것)가 규율에서 재발한다.
- **폐기는 관계 토큰으로만**: 새 항목이 `REVERSES`/`SUPERSEDES` 로 가리킨 규율은 다음
  판부터 빠진다. 조용히 안 실어서 없애는 경로는 없다. **단 폐기도 사용자 출처가 있는
  항목만 할 수 있다** — `source: []` 인 미승인 제안은 사용자 규율을 못 죽인다(구속력이
  사용자 발화에서 나오므로 해제도 같은 곳에서만 나온다).
- 대장 처분과 서로 가리켜야 한다(`standing_ledger_mismatch`) — 결정과 같은 규칙이다.
- `id` 는 `-S<번호>` 형식이어야 하고 한 payload 안에서 유일해야 한다
  (`standing_id_malformed`·`standing_id_duplicate`). ID 는 승계·폐기가 겨누는 손잡이다.
- **`sections.standing` 에 산문을 쓰지 마라 — 무시된다.** 이 절은 항상 위 배열에서
  렌더된다(산문으로 출처 검사를 우회하던 경로를 막았다).
- 강등 저장(`force_schema`)된 규율은 **재개에도 승계에도 안 실린다** — 다음 판에서
  제대로 재선언해야 한다(`standing_carry_demoted`).
- resume 가 이 절의 원문을 재개 지시에 실어 나간다 — **여기 적힌 것만 세션을 관통한다.**

#### 10. `Session Recap` — 대장 전체를 「발화 → 응답/행동」 쌍으로

`sections.session_recap` 에 산문으로 쓴다. 규율 둘:

- **각 항목은 왕복이다.** 사용자 말만 요약하면 답변이 상실된다(사용자 지적) — 무엇을
  물었/시켰고 **무엇이 답해졌/행해졌는지**까지가 한 항목이다. 사용자 쪽 전수는 대장이
  보증하고, 응답 쪽은 이 산문이 채운다.
- **덮을 범위는 CLI 가 정한다. 네가 고르지 않는다.** 절 머리에 `덮을 구간(CLI 계수):
  대장의 처음 ~ U00xx 직전` 이 박혀 나온다. 그 뒤 구간은 `Recent Dialogue` 에 **원문으로
  이미 있으므로 요약에서 다시 쓰지 않는다** — 같은 구간을 두 번 실으면 후반이 이중
  가중되고 전반이 두 번 밀린다. 꼬리는 「원문 참조」 한 줄로 넘기고 지면은 앞쪽에 쓴다.
  (「고르게 쓰라」는 훈계로는 최신성 편향이 안 막혀서 경계를 기계가 계산하게 바꿨다.)

목표 1줄(`> 목표:`)은 CLI 가 `summary` 를 재삽입한다 — 적지 않는다. `Recent Dialogue`
(대화 꼬리 30건)도 CLI 가 트랜스크립트에서 직접 넣는다 — 페이로드 입력이 없다.

`exact_next_step` 의 근거 발화는 `next_step_source: ["U00xx"]` 로 UID 만 넘긴다 —
CLI 가 지시 원문을 절에 병기한다(지시가 원문으로 있어야 다음 세션의 손이 바로 움직인다).
**사람이 친 발화만 권위다** — 하네스 레코드(`<task-notification>`·`<system-reminder>`·
자동압축 재개 주입문 같은 것)의 UID 를 넣으면 거부된다(`exact_source_not_human`).
결정·상시 규율과 같은 잣대다. **근거가 없으면 비워 둔다** — 그러면 CLI 가 그 절 맨 앞에
「chair 가 정한 것 · 사용자가 지시한 적 없다」를 박고, 다음 세션은 실행 전에 확인한다.
지어낸 UID 로 채우지 마라.
쓰기 대상 경로는 `next_step_targets: ["relative/path"]` 배열로 별도 넘긴다. CLI 는 이를
`exact_target_paths` frontmatter 구조로 보존할 뿐 Exact 산문을 다시 읽어 파싱하지 않는다.
**대장 처분과 집합이 같아야 한다**(`next_step_ledger_mismatch`) — 대장을
`Exact Next Step` 으로 처분했으면 반드시 여기에 그 UID 를 넣고, 그 반대도 같다.

#### 11. `## Incidents` — 사고 대장. 다섯 칸을 채운다. **0건은 의심 신호다**

**판별 한 줄: 만들어 놓은 것이 의도대로 안 돌아갔나.** 코드 결함·절차 위반·잘못된 측정·
거짓 보고 전부. **내가 낸 것과 남이 낸 것을 가리지 않는다.** 아직 안 해본 것
(`Not Tried Yet`)·막힌 것(`Blockers`)·접은 설계 선택(`Failed Attempts`)은 사고가 아니다 —
**판단이 갈린 것은 사고가 아니다.**

```markdown
### <프로젝트>-<토픽>-I1 — 한 줄 제목
- **증상**: 겉으로 무엇이 보였나 (「아무것도 안 보였다」도 값이다)
- **원인**: 실제로 무엇이 깨졌나 — 파일·함수까지
- **수명**: 언제 심었고 언제 잡혔나
- **잡은 것**: 자체검증 / 테스트 / 외부리뷰 / 사용자지적 / 운영중  ← 이 다섯 중 하나
- **처방**: 무엇을 고쳤나
```

- **「잡은 것」은 닫힌 집합이다**(`incident_catcher_unknown` 으로 거부). 자유 서술이면
  셀 수 없고, 세지 못하면 **「검증 절차가 실제로 무엇을 잡나」** 에 답할 수 없다 — 그게
  이 대장의 존재 이유다.
- **재발은 `RETRIES`, 해결은 `RESOLVES`** 로 이전 사고를 가리킨다. 새 어휘를 만들지
  않는다 — 결정 색인·부정 색인이 그대로 읽는다.
- `Lessons` 와 다른 물건이다. 교훈은 **일반화된 규칙**이라 0건이 정상이고, 사고는
  **구체적 사건**이라 많을수록 정직하다. **교훈은 사고에서 증류된다** — 실측: heredoc
  교훈(L3)은 같은 사고를 **두 번 낸 뒤에야** 나왔다. 사고 대장이 있었으면 첫 번째에서 잡혔다.
- 개수 상한 없음. **0건으로 저장하기 전에 정말 없었는지 다시 본다.**

#### ACTIVE-CONSTRAINTS 전역 제약 append

사용자가 현재 프로젝트의 전역 제약에 **추가하라고 명시한 경우에만**, 이미 이번 핸드오프
본문에 생성한 문구와 그 UID/ID 출처를 top-level `active_constraint_entries`로 가리킨다.
CLI는 문구를 새로 쓰지 않고, 저장된 본문에 실제로 있는 원문만
`.handoff/ACTIVE-CONSTRAINTS.md` 끝에 append한다.

```json
"active_constraint_entries": [
  {
    "text": "이번 핸드오프 본문에 이미 생성한 문구 원문",
    "source": "U0007 또는 기존 결정·사고 ID",
    "document": ".handoff/current-rule.md"
  }
]
```

- `document`는 선택값이다. 현재 규율 문서를 가리킬 때만 넣으며, resume은 그 경로를 전체 읽기 목록에 넣는다.
- `text`와 `source`는 이번에 저장되는 본문에 실제로 있어야 한다. 새 문장·요약·의역은 넣지 않는다.
- CLI는 파일이 없으면 단일 Markdown 목록으로 생성하고, 있으면 기존 바이트를 보존한 채 끝에 append한다.
- 같은 `source`는 같은 저장에서 한 번만 기록한다. 문구 문자열을 병합 키로 쓰지 않는다.
- Incident·Lesson·처방은 전부 자동 승격하지 않으며, 자동 제거·proof test 생명주기도 없다.

### Body Template (CLI 가 조립, 어댑터는 섹션 내용 제공)

CLI 가 frontmatter(topic/created/project_root/status/prev/source/git_branch/git_commit/
git_dirty/writer_model — 어댑터가 작성 안 함, CLI 가 실측해 생성)와 다음 **14헤딩**을 조립한다.
그중 **11개가 어댑터가 채우는 절**이고, 셋(`Git State`·`Files Touched`·`Utterance Ledger`)은
**CLI 가 만드는 데이터 블록**이다. 어댑터는 절 내용을 사용자의 언어로 채운다:

```markdown
## Intent And Purpose  → sections.intent
## Done              → sections.done
## Open               → sections.open
## Failed Attempts    → sections.failed_attempts
## Not Tried Yet       → sections.not_tried
## Blockers And Questions → sections.blockers
## Git State          → (sections 아님 — CLI 가 git meta 로 자동 생성)
## Files Touched       → (sections 아님 — top-level files_touched 배열)
## Decisions          → sections.decisions
## Unapproved Proposals → sections.unapproved
## Verification        → sections.verification
## Incidents          → sections.incidents (규율 11항 — 사고 대장)
## Lessons            → sections.lessons
## Utterance Ledger    → (sections 아님 — top-level utterance_ledger 배열)
## Session Recap       → sections.session_recap (목표 1줄은 CLI 가 summary 재삽입)
## Standing Directives  → top-level standing 배열 (규율 9항 — CLI 가 렌더·승계)
## Exact Next Step     → sections.exact_next_step (+ next_step_source 인용 병기)
## Recent Dialogue     → (sections 아님 — CLI 가 트랜스크립트 꼬리 30건 직접 삽입)
```

말미 4절(`Session Recap`→`Standing Directives`→`Exact Next Step`→`Recent Dialogue`)은
**맥락 전달 묶음**이다 — 자동압축의 주입 구조(요약 → 원문 꼬리 → 이어서 작업)를 문서
말미에 재현한 것(2026-08-18 자동압축 분석). 순서를 바꾸지 않는다.

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
- **Resume** — **`--directives-only` 로 한 번에 받는다.** 이 한 줄이 재개의 전부다:

  ```bash
  python -m handoff_cli --cwd "$PWD" resume --topic <t> --directives-only
  ```

  재개 지시 평문이 그대로 나온다. JSON 을 받아 파일로 빼서 나눠 읽지 않는다 — 판정에 쓰는
  절(`Decisions`·`Open`·`Blockers`·`Verification`)은 블록 2·4 에 원문으로 이미 실려 있다.
  블록 5 가 이름을 댄 외부 문서만 추가로 연다.

  블록 2는 그대로 따르고, 블록 3은 현재 지시로 올리지 않으며, 블록 4는 재확인한다.
  지시문을 요약·생략하지 않는다.

  **블록 6의 복명을 끝까지 채우는 것이 재개다 — 칸 수는 그 블록이 말한다. 거기서 멈추고 사용자 지시를 기다린다.** 블록 2의
  `Exact Next Step` 도 실행하지 않는다. 확인하겠다고 테스트를 돌리거나 코드를 뒤지지 않는다.
  git drift·broken 포인터 같은 어긋남은 블록 4에 실려 나오며, 보이면 **먼저 보고**하고
  어느 상태에서 이어갈지 확인한다. 본문 전체·`prev` 체인이 필요하면 그때만
  `--directives-only` 없이 다시 호출한다.
- **Archive** — 토픽을 `archived/`로 이동(대상 존재 시 중단), `INDEX.md` 재생성. 다른 프로젝트
  기록·장기 기억 도구·auto-memory 는 자동 삭제하지 않는다.

## Legacy Migration / Round Contracts

이전 `~/.claude/handoffs/<topic>/` 본문은 소유 프로젝트가 명확할 때만 `<project-root>/.handoff/`로
한 번 이전한다. 애매하면 원위치 보류·보고. 레거시 글로벌 `INDEX.md`는 이력으로 보존하되 정본으로
쓰지 않는다. 프로젝트가 `.handoff/` 에 두는 다른 산출물(설계·리뷰 기록 등)도 같은 프로젝트
로컬 원칙을 따른다 — `/handoff` 기록은 그것들을 요약·참조할 수 있으나 대체하지 않는다.
