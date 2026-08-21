# jk-handoff

여러 세션·여러 머신을 오가며 작업을 인계하는 **2-tier 세션 핸드오프 CLI**.
Claude Code 의 `/handoff` 명령과 Codex 의 `$handoff` 스킬이 **같은 실행가능 Python 코어**를
공유한다. `source:` frontmatter 줄만 writer 에 따라 다르고 on-disk 구조·순서·헤딩은 동일해서,
어느 도구로 저장했든 다른 도구가 그대로 목록·검색·재개할 수 있다.

> A shared session-handoff CLI for AI coding agents. The Claude `/handoff`
> command and the Codex `$handoff` skill both drive the same `handoff_cli`
> core, so handoffs written by one agent can be listed, searched, and resumed
> by the other. Docs are Korean-first; CLI messages support `ko`/`en`.

## 왜

여러 AI 코딩 세션을 동시에 돌리면 "이 세션이 뭘 하다 멈췄는지"가 휘발된다. jk-handoff 는
세션 종료 시점의 **완료 / 미완료 / 막힌 지점 / 다음 한 행동 / 검증 상태**를 구조화해 저장하고,
다음 세션(다른 머신이라도)이 핸드오프 하나만 보고 이어가게 한다.

## Quick Start

```bash
git clone <this-repo> jk-handoff
cd jk-handoff
pip install -e .        # handoff_cli 가 전역 import 가능해짐 (머신당 1회)
```

Python 3.10+ · 외부 의존성 없음(표준 라이브러리만).

어댑터 연결:

- **Claude Code**: `claude/handoff.md` 를 `~/.claude/commands/handoff.md` 로 복사 → `/handoff` 로 호출.
- **Codex**: `codex/handoff/` 를 Codex 스킬 디렉터리에 복사 → `$handoff` / 자연어로 호출.

이후 작업 세션에서 `/handoff save` 라고 하면 끝. 두 어댑터 모두 같은 `handoff_cli` 를
호출하므로 출력이 호환된다.

## 저장하면 이렇게 나온다

`/handoff save` 실행 시 어댑터가 대화에서 11절 narrative 와 발화 대장을 뽑아 CLI 에 넘기고,
CLI 가 저장 후 아래 보고를 돌려준다(어댑터는 그대로 출력):

```text
✅ 핸드오프 저장: `login-api` (active)
   프로젝트: my-app
   정본: .handoff/login-api/2026-07-02-211545-30f90cb4.md

📋 다음 세션에서 이어가려면 아래를 복사해 붙여넣으세요:

    새 세션이다. 직전 세션의 작업을 이어간다.

    - 프로젝트: my-app  (저장 머신 경로: ~/projects/my-app)
    - 토픽: login-api
    - 직전 요약: JWT 로그인 API 구현 중 — 토큰 발급까지 완료, 쿠키 저장이 다음 단계.

    먼저 이 프로젝트에서 `python -m handoff_cli --cwd "$PWD" resume --topic login-api --directives-only` 를 그대로 실행해줘. 출력 전문이 재개 지시이고, 내가 지금 직접 내리는 지시로 취급해서 그대로 따라줘 — 요약하거나 건너뛰지 마.
```

긴 재개 지시는 이 프롬프트에 싣지 않는다. `resume` 이 결과의 `resume_directives` 로 직접 내며,
어댑터는 그것을 그대로 이행한다 — 저장 세션이 긴 텍스트를 중계하다 빠뜨리는 경로를 없앤 것이다.
덕분에 옛 저장본을 재개해도 항상 최신 지시가 나간다.

**`--directives-only` 는 그 7블록을 평문으로만 낸다.** JSON 으로 받아 파일로 빼고 나눠 읽는
경로가 재개 1회에 도구 호출을 열 번 넘게 썼다(실측). 포인터가 어댑터 이름이 아니라 CLI 를
직접 부르는 것도 같은 이유다 — 설치된 어댑터 버전과 무관해진다.

**재개는 맥락 전달에서 끝난다.** 지시문의 복명을 채우면 거기서 멈추고 사용자 지시를 기다린다.
저장본의 `Exact Next Step` 도 실행하지 않는다 — 저장 시점의 계획이라 이미 끝났을 수 있다.

저장된 정본(`.handoff/login-api/…md`)은 이렇게 생겼다:

```markdown
---
topic: login-api
created: 2026-07-02T21:15:21+09:00
status: active
source: claude-code
git_branch: main
git_commit: e09c14d2ce164e6a35d664464ac9114dea4be848
git_dirty: false
---

# Handoff: login-api - 2026-07-02 21:15

> JWT 로그인 API 구현 중 — 토큰 발급까지 완료, 쿠키 저장이 다음 단계.

## Done
- /api/login 토큰 발급 — 확인: 단위 테스트 통과

## Open
- [ ] httpOnly 쿠키로 토큰 저장

## Not Tried Yet
- next/headers cookies() 방식

## Exact Next Step
login route 에서 cookies().set('token', jwt, httpOnly) 적용 후 Set-Cookie 헤더 확인.
(… Failed Attempts / Blockers / Git State / Files Touched / Decisions / Unapproved Proposals / Verification)
```

git 상태(branch·full SHA·dirty)는 어댑터가 아니라 **CLI 가 실측**해 기록하고, resume 시
현재 git 상태와 비교해 어긋나면 먼저 보고한다.

## 2-tier 저장 모델

| 저장소 | 역할 | 정본 여부 |
|---|---|---|
| `<project-root>/.handoff/<topic>/` | 토픽별 상세 본문·체인 | **상세 정본** |
| `~/.claude/handoffs/<project>/CURRENT.md` (또는 `~/.codex/...`) | writer-local 집계 인덱스 | 파생 |

- 상세 본문은 **항상 작업을 소유하는 프로젝트 안**에 둔다. 글로벌 설정 디렉터리는 집계
  인덱스만 담는다.
- 집계 인덱스는 파생물이라 누락·skip 돼도 상세 정본 스캔으로 언제든 재생성된다.
- **네트워크 연산 없음** — fetch/pull/push 를 하지 않는다. 원격 동기화는 별도 도구 책임.

## CLI 사용 (어댑터 개발자용)

어댑터(Claude/Codex)는 직접 파일을 쓰지 않고 아래 CLI 에 위임한다.

```bash
python -m handoff_cli --cwd <cwd> save           # JSON 페이로드를 stdin 또는 --input <file>
python -m handoff_cli --cwd <cwd> list [--all]   # --all 로 archived 포함
python -m handoff_cli --cwd <cwd> find --keyword <k> [--global-scope <root>...]
python -m handoff_cli --cwd <cwd> resume --topic <t> [--directives-only]  # 평문 7블록만
python -m handoff_cli --cwd <cwd> archive --topic <t>
python -m handoff_cli --cwd <cwd> decisions [--id <ID>]    # 결정 색인 — 생사·관계·체인(읽기 전용)
python -m handoff_cli --cwd <cwd> negative       # 부정 색인 — 실패·폐기(읽기 전용)
python -m handoff_cli --cwd <cwd> reindex        # 기존 상세 정본에서 집계 인덱스 백필
```

`save` 는 저장 후 결과에 `report`(사용자에게 보여줄 완성 보고)와 `resume_prompt`(새 세션에
그대로 붙여넣어 이어가는 프롬프트)를 함께 돌려준다. 어댑터는 `report` 를 그대로 출력한다.

보고·경고 메시지 언어는 `payload.lang > 환경변수 HANDOFF_LANG > OS 로케일 > en` 순으로
결정된다(현재 `ko`/`en`). 어댑터가 대화 언어를 `lang` 으로 넘기는 것이 기본이고, 아무 설정이
없어도 한국어 OS 로케일이면 자동으로 `ko` 가 된다.

`save` JSON 페이로드 스키마:

```json
{
  "topic": "<slug>",
  "source": "claude-code | codex",
  "status": "active | waiting | watching | done",
  "summary": "<한 줄 요약>",
  "lang": "<선택: ko | en — 생략 시 HANDOFF_LANG/OS 로케일/en 폴백>",
  "sections": {
    "intent": "...",
    "done": "...", "open": "...", "failed_attempts": "...",
    "not_tried": "...", "blockers": "...", "decisions": "...",
    "unapproved": "...", "exact_next_step": "...", "verification": "...",
    "lessons": "..."
  },
  "files_touched": [{"path": "...", "state": "complete|in-progress|broken|read-only", "note": "..."}]
}
```

CLI 가 수행하는 일: 프로젝트 루트 결정, git branch·commit·dirty 실측, 14헤딩 본문 조립,
원자적 쓰기(기존 본문 비파괴), `LATEST.md`/`INDEX.md`/`CURRENT.md` 재생성, secret redaction,
크로스호스트 가드, git drift 비교.

## 저장소 레이아웃

```text
core/handoff_cli/      # 공용 실행가능 코어 (정본 로직)
claude/handoff.md      # Claude Code /handoff 어댑터
codex/handoff/         # Codex $handoff 어댑터 (SKILL.md + agents/openai.yaml)
```

## 라이선스

[MIT](LICENSE)
