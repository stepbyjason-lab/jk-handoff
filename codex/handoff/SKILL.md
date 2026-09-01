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

두 어댑터(Claude `/handoff`, Codex `$handoff`)의 `source:` frontmatter 줄과 narrative 를 뺀
on-disk 구조·순서·헤딩은 동일하다. `source:` 줄만 writer 에 따라 다르며, 어느 writer 의
산출물이든 타 writer 가 list / find / resume / save 로 이어갈 수 있다.

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

1. **현재 목표** → `summary`; **왜 이 방향인지**(대안 대비, Chair 추론) → `## Unapproved Proposals`
2. **완료 / 미완료** → `## Done` / `## Open`. 완료 항목은 가능한 한 **확인 증거**를 함께 적는다
   (예: `— 확인: 테스트 통과`). 증거 없으면 Done 대신 Open/Not Tried 로.
3. **다음 한 행동** → `## Exact Next Step` (구체적·즉시 실행 가능. 모호하면 묻기)
4. **블로커** → `## Blockers And Questions` (없으면 "현재 블로커 없음.")
5. **검증 상태** → `## Verification` (완료 항목을 **무엇으로** 확인했는지 명시 / 미검증)
6. **관련 결정** → 장기 기억 도구에 기록했으면 `## Unapproved Proposals` 에 포인터와 근거 명시
7. **유망하나 아직 안 해본 접근** → `## Not Tried Yet`

### Decisions / Unapproved Proposals 규율

1. `sections.decisions` 는 사용자 발화 원문 인용만. D-3 경계를 따른다.
2. `sections.unapproved` 에 Chair 가 정한 것과 **근거**를 함께 적는다.
3. `## Open` 각 항목에 **완료 조건**을 반증 가능한 문장으로. "즉시 적용한다" 류는 무효.
4. 저장 전, 이번 대화에서 사용자가 답한 질문을 훑어 답이 `Decisions` 에 원문으로 들어갔는지 확인한다.
5. **Resume 시 두 절을 반드시 읽고 보고에 반영한다** — `## Decisions` 는 사용자 확정 원문으로
   그대로 존중하고, `## Unapproved Proposals` 는 미승인이므로 실행 전 사용자에게 확인한다.

**`summary` 한 줄은 항상 실질적으로 채운다.** Codex-local CURRENT.md 인덱스가 `summary` +
`## Exact Next Step`·`## Blockers And Questions` 의 첫 줄을 뽑아 "지금 뭐 / 다음 뭐 / 막힌 것"을
보여준다 — 비면 인덱스가 "(요약 없음)" 으로 빈약해지고 다른 머신에서 상황 파악이 안 된다.

### 발화 대장 획득 (Codex)

Codex 는 트랜스크립트 경로를 state DB 가 들고 있어 **먼저 조회**해야 한다.

```bash
SESSION_ID="${CODEX_THREAD_ID:-}"
TRANSCRIPT=""
if [ -n "$SESSION_ID" ]; then
  TRANSCRIPT="$(CODEX_HOME="${CODEX_HOME:-$HOME/.codex}" python -c '
import os, sqlite3, sys
from pathlib import Path
db = Path(os.environ["CODEX_HOME"]) / "state_5.sqlite"
try:
    # mode=ro — 읽기 전용으로만 연다. 생성·쓰기·잠금 승격을 하지 않는다.
    with sqlite3.connect(db.absolute().as_uri() + "?mode=ro", uri=True, timeout=1.0) as c:
        row = c.execute("SELECT rollout_path FROM threads WHERE id = ?",
                        (sys.argv[1],)).fetchone()
except (OSError, sqlite3.Error) as exc:
    print("handoff: state DB 읽기 실패: %s" % exc, file=sys.stderr); raise SystemExit(1)
if not row or not row[0]:
    print("handoff: rollout_path 를 찾지 못했다.", file=sys.stderr); raise SystemExit(1)
print(row[0], end="")   # 무변환 — realpath·cygpath·드라이브 문자 치환 금지(junction 이 깨진다)
' "$SESSION_ID" || true)"
fi

[ -n "$TRANSCRIPT" ] && python -m handoff_cli --cwd "$PWD" utterances \
  --session "$SESSION_ID" --format codex \
  --transcript "$TRANSCRIPT" --topic "<확정 토픽>" [--delta | --full]
```

사용자가 `$handoff save --delta`·`--full` 을 줬으면 **그 플래그를 그대로 붙여 보낸다.**
안 줬으면 생략한다 — CLI 가 알아서 가른다(규율 7).

`CODEX_THREAD_ID` 가 없거나 DB 조회가 실패하면 아래 규율 8 의 폴백으로 간다 — 잠금을
우회하거나 DB 를 쓰기 모드로 다시 열지 않는다.

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

#### 8. 대장을 못 얻었으면 — **부분만 얻었어도** — 전수 검증을 주장하지 않는다

세션 식별자가 없거나 트랜스크립트를 못 찾았으면 `session_id`·`transcript`·
`transcript_format`·`covers_from`·`utterance_ledger` 를 전부 생략하고 저장을 계속하되,
**「전부 정리했다」고 말하지 않는다.** frontmatter 의 `writer_session: null` 이 보증 없음을
그대로 드러낸다.

**대장을 받았다고 그것이 세션 전체라는 뜻은 아니다(R9).** Claude Code 는 컨텍스트가 차면
대화를 **새 전사 파일로 잘라 옮긴다** — 그래서 `--session` 이 가리키는 파일 하나만 읽으면
뒷부분만 덮은 대장이 「세션 전체」로 나간다(실측 277건 중 54건). CLI 가 잘린 파일 머리의
이음매를 따라 앞부분까지 거슬러 올라가 한 대장으로 잇지만, 앞 전사가 지워졌거나 잠겨
있으면 못 잇는다. 그때 판정은 출력에 값으로 온다:

- **`coverage`** — `complete` 면 이으려던 것을 다 읽었고, `partial` 이면 **앞 구간의 발화가
  이 대장에 없다.** `scope` 와 **다른 축**이다: `scope` 는 「어디부터 덮기로 했나」이고
  `coverage` 는 「덮기로 한 것을 실제로 다 읽었나」다. 그래서 **`scope: "full"` 이면서
  `coverage: "partial"` 일 수 있고, 그것은 전수가 아니다.**
- `transcript_chain` 이 실제로 읽은 전사 전부(오래된 것부터)다. 항목이 둘 이상이면 이 세션에
  자동압축이 걸렸다는 뜻이다.
- `partial` 이면 CLI 가 `warn_compact_chain_*` 경고를 낸다 — 그대로 보고하고, 저장본에
  **덮은 범위를 명시한다.** 앞 전사를 찾을 수 있으면 `--transcript` 로 직접 주고 다시 받는다.

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

#### 9-b. `work_id` — 조직이 이 작업을 부르는 이름

라운드·티켓·에픽 같은 공식 식별자다. **저장하는 세션이 적는다** — 이 세션은 사용자와
대화하며 그 값을 확정했으므로 알고 있고, 재개는 그것을 블록 1 에 실어 나르기만 한다
(복명 ② 가 그 줄의 `**…**` 안 값을 옮긴다).

**토픽에서 만들어 내지 마라.** 토픽은 파일 축이라 한 작업이 여러 토픽에 흩어지고 작업이
아닌 토픽도 있다 — 실측으로 한 라운드가 토픽 셋, 다른 라운드는 열여덟 개였다. 유추하면
틀린 값이 매 기록에 박힌다.

**모르면 넣지 않는다.** 빈 값은 재개에 「미상」으로 뜨고, 그건 거짓이 아니라 사실이다.
프로젝트에 식별자 정본 목록이 있으면 거기서 고른다. 사용자가 알려주면 그때 적는다.

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

CLI 가 frontmatter 와 다음 **14헤딩**을 조립한다. 그중 **11개가 어댑터가 채우는 절**이고,
셋(`Git State`·`Files Touched`·`Utterance Ledger`)은 **CLI 가 만드는 데이터 블록**이다.
각 마크다운 헤딩 옆의 JSON key 는 아래 `save` payload 의 `sections` 안에 채워 넘긴다:

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

## CLI 호출

설치 전제(머신당 1회): jk-handoff 레포에서 `pip install -e .` 를 한 번 실행하면
`handoff_cli` 가 전역 import 가능해져 아래 호출이 PYTHONPATH 없이 동작한다.

```bash
python -m handoff_cli --cwd "$PWD" save           # JSON 페이로드를 stdin 으로(source=codex 기본 인덱스: ~/.codex)
#   범위 강제: 위 utterances 호출에 --delta(직전 저장 이후만) / --full(세션 전체)
python -m handoff_cli --cwd "$PWD" list           # --all 로 archived 포함
python -m handoff_cli --cwd "$PWD" find --keyword "<k>"   # 글로벌: --global-scope ~/projects ~/work (하위 트리 .handoff/ 전부 read-only)
python -m handoff_cli --cwd "$PWD" resume --topic "<t>"
python -m handoff_cli --cwd "$PWD" archive --topic "<t>"
python -m handoff_cli --cwd "$PWD" decisions [--id <ID>]  # 결정 색인 (읽기 전용)
python -m handoff_cli --cwd "$PWD" negative        # 부정 색인 — 실패·폐기 (읽기 전용)
```

`save` JSON 페이로드:

```json
{
  "topic": "<slug>",
  "source": "codex",
  "status": "active | waiting | watching | done",
  "summary": "<한 줄 요약>",
  "lang": "ko | en",
  "session_id": "<CODEX_THREAD_ID — 대장을 얻었을 때만>",
  "work_id": "<조직이 이 작업을 부르는 이름 — 모르면 넣지 않는다>",
  "transcript": "<rollout_path 무변환 — 대장을 얻었을 때만>",
  "transcript_format": "codex",
  "covers_from": null,
  "sections": {
    "intent": "...",
    "done": "...", "open": "...", "failed_attempts": "...", "not_tried": "...",
    "blockers": "...",
    "exact_next_step": "...", "verification": "...", "lessons": "..."
  },
  "decisions": [
    {"id": "D1", "source": ["U0007"], "interpretation": "...",
     "relations": [{"token": "REVERSES", "target": "D0"}]}
  ],
  "utterance_ledger": [
    {"uid": "U0001", "section": "Done", "note": "무엇이 남았나"},
    {"uid": "U0002", "section": "없음", "note": ""}
  ],
  "files_touched": [{"path": "...", "state": "complete", "note": "..."}]
}
```

`source` 는 반드시 `codex` 로 둔다. `status` 는 대화 맥락에서 판단한다(진행 중=active,
대기=waiting, 관망=watching, 종료=done). CLI 가 기존 open/open_planning/closed/CLOSED 도
정규화하므로 레거시 detail 과 호환된다.

## Save (`$handoff save` 또는 `$handoff <topic>`)

1. 루트(`project_root`)와 토픽을 확인한다. 프로젝트나 토픽이 모호하면 쓰기 전에 사용자에게
   확인한다 — 자동선택하지 않는다.
2. **「발화 대장 획득」을 먼저 실행한다** — 다른 절을 쓰기 전이다(순서가 뒤집히면 기억으로 쓰고
   대장으로 사후 정당화하게 된다).
3. 대장을 근거로 14절 narrative 와 `status` 를 판단해 JSON 페이로드를 만든다. 대장을 얻었으면
   `"session_id"`·`"transcript"`·`"transcript_format": "codex"`·`"covers_from"` 을 함께 넣는다.
4. CLI `save` 를 호출한다.
5. **CLI 결과의 `report` 문자열을 한 글자도 바꾸지 말고 그대로 출력한다.** `report` 에 저장 확인 ·
   복붙용 이어가기 프롬프트(```text 코드블럭) · 경고가 모두 들어 있다 — 자유 서술로 다시 쓰지 않는다.

## 동작 원칙

1. 저장 전 루트(`project_root`)와 토픽을 확인한다. 프로젝트나 토픽이 모호하면 쓰기 전에
   사용자에게 확인한다 — 자동선택하지 않는다.
2. **저장 결과의 `report` 문자열을 한 글자도 바꾸지 말고 그대로 출력한다.** `report` 에 저장 확인 ·
   복붙용 이어가기 프롬프트(```text 코드블럭) · 경고가 모두 들어 있다 — 자유 서술로 다시 쓰지 않는다.
3. `concurrent_conflict` 가 true 면 `report` 가 충돌 안내(resume 블록 없음)다. 그대로 전달하고
   두 최신본 중 어느 체인을 최신으로 할지 확인한다.
4. **resume 은 `--directives-only` 로 한 번에 받는다** — `python -m handoff_cli --cwd "$PWD"
   resume --topic <t> --directives-only`. 재개 지시 평문이 그대로 나온다. JSON 을 받아 파일로 빼서
   나눠 읽지 않는다 — 판정에 쓰는 절(`Decisions`·`Open`·`Blockers`·`Verification`)은 블록 2·4 에
   원문으로 이미 실려 있다. 블록 5 가 이름을 댄 외부 문서만 추가로 연다. 블록 2는 그대로
   따르고, 블록 3은 현재 지시로 올리지 않으며, 블록 4는 재확인한다. 지시문을 요약·생략하지
   않는다. **블록 6의 복명을 끝까지 채우는 것이 재개다 — 칸 수는 그 블록이 말한다. 거기서 멈추고 사용자 지시를 기다린다.**
   블록 2의 `Exact Next Step` 도 실행하지 않는다. 확인하겠다고 테스트를 돌리거나 코드를
   뒤지지 않는다. git drift 는 블록 4에 실려 나오며, 보이면 먼저 보고하고 어느 상태에서
   이어갈지 확인한다. 본문 전체·`prev` 체인이 필요하면 그때만 플래그 없이 다시 호출한다.
5. 작업 로그·핸드오프 본문은 사용자의 언어로 작성한다. 코드·경로·식별자·인용 영문은 원어
   그대로 둔다. `save` payload 에 사용자의 대화 언어에 맞는 `"lang"`(`"ko"`/`"en"`)을
   함께 전달한다 — 미전달 시 CLI 가 env `HANDOFF_LANG` → OS locale → `en` 순으로 해석한다.
6. 장기 기억 도구 기록은 장기 가치(설계/제품 결정, 반복 블로커, 다음 라운드 필수 사실)일 때만 한다.
7. **하네스 주입 요약은 이 세션의 작업이 아니다(재개 오염 방어).** 세션 시작 시 주입되는
   "PRIOR-SESSION SUMMARY"·"Previous session summary" 류 컨텍스트는 다른 세션·다른 프로젝트의
   기록일 수 있다 — 토픽·루트·narrative 판단 근거로 쓰지 않는다. `files_touched`에는 이 세션에서
   실제로 만지거나 확인한 파일만 넣는다(주입된 요약에서 옮기지 않는다). `state: read-only`는
   유효하다 — 기준은 "수정"이 아니라 "이 세션 실작업 여부". 저장 대상 프로젝트가 대화 실작업과
   달라 보이면 저장 전 확인한다. CLI 가 교차 프로젝트 경고(`warn_cross_project_files`)를 내면
   그대로 보고하고 진행 전 확인한다.
