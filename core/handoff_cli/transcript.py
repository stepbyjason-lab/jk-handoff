"""세션 트랜스크립트에서 **사용자 발화 대장**을 코드로 뽑는다 (R8 3안).

왜 코드인가:
    대장의 가치는 **믿을 수 있다**는 것 하나에 걸려 있다. 대장이 불완전하면 빠진 발화는
    여전히 조용히 사라지는데 겉보기엔 완전해 보여서 지금보다 나쁘다. 그래서 모델을 쓰지
    않는다 — 실측(2026-08-17): 같은 일을 Haiku 서브에이전트에 맡겼더니 긴 붙여넣기를
    잘라먹고 개수를 48개라 보고했는데 실제 43개였다. **LLM 을 쓰는 순간 자기 신고로 돌아간다.**

무엇이 「발화」인가 — 판단이 아니라 형식으로 가른다:
    - `message.content` 가 **문자열**이면 사용자가 친(또는 붙여넣은) 발화다.
    - `content` 가 `tool_result` 블록 리스트면 도구 출력이다 — 발화가 아니다.
    - `isMeta` 는 스킬·시스템 주입이라 발화가 아니다. **다만 다른 세션이 보낸 발화도
      그 표시를 달고 온다** — 본문의 구조 태그로 가려 `kind="peer"` 로 담는다.
    - 하네스가 넣는 알림(`[SYSTEM NOTIFICATION`·`<task-notification` 등)은 문자열로 오지만
      사람이 친 것이 아니므로 `kind="system"` 으로 **표시하되 버리지 않는다**(버리면 그 판단이
      대장에 안 남는다).

호스트 결합 경계:
    경로는 **호출자가 주는 것이 정본**이고(`--transcript`), 유도는 편의다. 못 찾으면
    **소리 나게 실패한다** — 조용히 빈 대장을 내면 「전수」 보증이 거짓이 된다.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

__all__ = ["derive_transcript_path", "extract_utterances", "extract_utterances_from",
            "compact_boundary", "compact_chain", "looks_compacted",
            "human_utterance_uids",
            "extract_dialogue_tail",
            "parse_ts", "excerpt", "measure_writer_model",
            "count_malformed", "read_manifest", "read_dialogue_tail",
            "TranscriptNotFound"]

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class TranscriptNotFound(Exception):
    """트랜스크립트를 못 찾았다. 시도한 경로를 함께 싣는다."""

    def __init__(self, tried: list[str]):
        self.tried = tried
        super().__init__("트랜스크립트를 찾지 못했다: " + " | ".join(tried))


# 하네스가 사용자 역할로 밀어 넣는 것들. 사람이 친 게 아니다.
_SYSTEM_PREFIXES = (
    "[SYSTEM NOTIFICATION",
    "<task-notification",
    "<system-reminder",
    "Caveat:",
    "[Request interrupted",
    # 자동압축 재개 주입문. 어댑터 규율 8항이 *"하네스 주입 요약은 이 세션의 작업이 아니다"*
    # 라고 이미 막아둔 것을, 대장이 `role: user` 로 되살려 넣고 있었다(실측 U0093).
    # 그대로 두면 재개문이 변곡점 하나를 지어낸다.
    "This session is being continued from a previous conversation",
    "PRIOR-SESSION SUMMARY",
    "Previous session summary",
)


#: 다른 세션이 보낸 발화. 호스트가 `role: user` 로 넣지만 **사람이 친 말이 아니다.**
#:
#: 이 레코드는 지금까지 대장에서 통째로 빠졌다(실측: 한 세션에 9건). 빠진 이유가 이쪽의
#: 판정이 아니라 호스트가 붙이는 `isMeta` 였다 — 그 발화를 위해 만들어진 자리가 아니다.
#: 그래서 **본문의 구조 태그로 가른다.** 태그는 호스트가 그 메시지를 위해 쓰는 것이라
#: 안내 문구(영어 산문)보다 오래 간다.
_PEER_TAG = "<cross-session-message"
_PEER_BLOCK = re.compile(r"<cross-session-message\b([^>]*)>(.*?)</cross-session-message>",
                         re.S)
_PEER_NAME = re.compile(r'from-name="([^"]*)"')


def _peer_text(content: str) -> str:
    """래퍼를 벗기고 보낸 쪽 이름을 앞에 세운다.

    벗기지 않으면 지문 120자가 영어 안내문(*"Another Claude session sent a message…"*)과
    긴 `uds:` 파이프 경로로 다 차서 **무슨 말이 오갔는지가 대장에서 안 보인다.** 대장이
    시각 순서를 지키는 목적이 맥락이므로, 자리만 있고 내용이 안 보이면 반쪽이다.
    """
    block = _PEER_BLOCK.search(content)
    if not block:
        return content
    attrs, body = block.group(1), block.group(2).strip()
    name = _PEER_NAME.search(attrs)
    who = (name.group(1).strip() if name else "") or "다른 세션"
    return f"[{who}] {body}" if body else f"[{who}]"


def _peer_row(record: dict) -> dict | None:
    """다른 세션이 보낸 발화면 대장 행으로, 아니면 `None`."""
    message = record.get("message") or {}
    if message.get("role") != "user":
        return None
    content = message.get("content")
    if not isinstance(content, str) or _PEER_TAG not in content:
        return None
    return {"ts": record.get("timestamp"), "record": record.get("uuid"),
            "text": _peer_text(content), "kind": "peer", "role": "user"}


def _project_slug(cwd: str) -> str:
    """`D:\\Code\\cc_handoff` -> `D--Code-cc-handoff` (Claude Code 프로젝트 디렉터리 규칙)."""
    return re.sub(r"[:\\/_]", "-", str(Path(cwd).resolve()))


def derive_transcript_path(session_id: str, cwd: str,
                           explicit: str | None = None) -> Path:
    """**호출자가 준 경로가 정본이다.** 유도는 Claude Code 전용 편의일 뿐이다.

    벤더 사정은 어댑터가 안다 — 어댑터가 자기 호스트에서 경로를 구해 넘기고, 코어는 읽기만
    한다. Codex 는 `CODEX_THREAD_ID` 로 `state_5.sqlite.threads` 를 조회해 `rollout_path` 를
    얻으며(**무변환으로 쓴다** — `.codex` 가 junction 이라 드라이브 문자로 재작성하면 깨진다),
    그 경로 규칙을 코어가 흉내낼 수 없다.

    (2026-08-17 정정: 여기서 `~/.codex/projects/<slug>/<id>.jsonl` 을 후보로 두고 있었는데
    **그런 경로는 존재하지 않는다.** 확인 없이 Claude 규칙을 대칭으로 복사한 것이었다.
    실제 Codex 저장소는 `sessions/YYYY/MM/DD/rollout-*.jsonl` 이고 DB 조회로만 찾는다.)

    유도가 실패하면 `TranscriptNotFound` — 조용히 넘어가지 않는다. 빈 대장을 내면
    「전수」 보증이 거짓이 된다.
    """
    tried: list[str] = []
    if explicit:
        path = Path(explicit)
        if path.is_file():
            return path
        tried.append(str(path))

    # Claude Code 전용 유도. 호스트 버전이 바뀌어 규칙이 달라지면 여기를 갱신한다
    # (예외 메시지가 시도한 경로를 그대로 보여주므로 무엇이 어긋났는지 바로 보인다).
    derived = (Path(os.path.expanduser("~")) / ".claude" / "projects"
               / _project_slug(cwd) / f"{session_id}.jsonl")
    if derived.is_file():
        return derived
    tried.append(str(derived))
    raise TranscriptNotFound(tried)


def parse_ts(value: str | None):
    """ISO8601 문자열을 tz-aware datetime 으로. 실패하면 None."""
    if not value or value == "null":
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _claude_rows(handle) -> list[dict]:
    """Claude Code 발화는 **채널이 둘**이다. 한쪽만 읽으면 「전수」가 거짓이 된다.

    ① `message.role == "user"` + `content` 가 문자열 — 보통의 턴.
    ② `attachment.type == "queued_command"` — **작업 중 미리 쳐 넣은 입력**(큐).

    ②를 안 읽어 실측 6건이 대장에서 빠졌다(검체 `madi/r48f`). 그중
    *"기획했던 게 무시된 부분이 얼마나 더 있을지… 꼼꼼하게 찾아봐"* 처럼 세션 방향을 크게
    바꾼 발화가 있었다. 대장의 존재 이유가 「빠뜨리면 흔적이 남는다」인데,
    **애초에 목록에 없으면 흔적도 안 남는다.**

    **`queue-operation` 레코드는 읽지 않는다.** 그건 큐 장부이고 발화의 정본이 아니다 —
    `dequeue` 는 내용을 아예 안 싣고(85건 전부 `content: null`), `remove` 는 「사용자가 취소」가
    아니라 **전달되며 큐에서 빠진 것**이다(실측: `remove` 된 발화가 그 직후 세션 방향을 바꿨다).
    장부로 판정하려다 전달된 발화를 취소로 오인할 뻔했다.

    ②의 필터 둘: `origin.kind == "human"`(도구·훅이 넣은 것 배제) ·
    `commandMode == "prompt"`(`task-notification` 3건이 여기서 빠진다).
    """
    rows: list[dict] = []
    malformed = 0
    for line in handle:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            # **세되 조용히 넘기지 않는다.** 깨진 줄을 그냥 건너뛰면 대장이 불완전한데도
            # 「모든 UID 를 처분했다」가 100% 로 나온다 — 보증이 거짓이 되는 자리다.
            malformed += 1
            continue
        if record.get("isSidechain"):
            continue
        # **`isMeta` 보다 먼저 본다.** 다른 세션이 보낸 발화가 그 표시를 달고 오므로
        # 뒤에 두면 판정할 기회가 없다 — 지금까지 전량이 그렇게 빠졌다.
        peer = _peer_row(record)
        if peer is not None:
            rows.append(peer)
            continue
        if record.get("isMeta"):
            continue
        # 자동압축이 새 전사 머리에 넣는 **요약**은 하네스 산출물이지 발화가 아니다.
        # 앞 판은 대장 판독만 이 필드를 안 봐, 요약 첫 문장이 `_SYSTEM_PREFIXES` 와
        # **우연히 맞을 때만** 빠졌다. 지금은 `read_session` 한 판독에서 대장과 꼬리를
        # 함께 만들므로 여기서 한 번 걸러 둘이 같은 입력을 본다.
        if record.get("isCompactSummary"):
            continue

        attachment = record.get("attachment") or {}
        if attachment.get("type") == "queued_command":
            origin = attachment.get("origin") or {}
            prompt = attachment.get("prompt")
            if (origin.get("kind") == "human"
                    and attachment.get("commandMode") == "prompt"
                    and isinstance(prompt, str)):
                rows.append({"ts": record.get("timestamp") or attachment.get("timestamp"),
                             "record": record.get("uuid"), "text": prompt,
                             "role": "user"})
            continue

        message = record.get("message") or {}
        role = message.get("role")
        if role == "user":
            text = _user_text(message.get("content"))
            if text:
                # 레코드 아이디를 함께 낸다 — 압축이 옮겨 적은 복사본은 **같은 레코드**라
                # 이 값이 같고, 서로 다른 발화는 본문·시각이 같아도 다르다(`_carry_key`).
                rows.append({"ts": record.get("timestamp"),
                             "record": record.get("uuid"), "text": text,
                             "role": "user"})
        elif role == "assistant":
            # **조수의 말도 같은 판독에서 낸다.** 대장은 사람 발화만 쓰지만 대화 꼬리는
            # 둘 다 쓴다. 따로 읽으면 두 목록을 사후에 이어야 하고, 그 잇기가 이 라운드에서
            # 여덟 번 터졌다 — 같은 판독에서 나오면 이을 것이 없다.
            if record.get("isApiErrorMessage"):
                continue          # API 오류 문구는 조수의 말이 아니다
            content = message.get("content")
            if isinstance(content, list):
                parts = [b["text"] for b in content
                         if isinstance(b, dict) and b.get("type") == "text"
                         and isinstance(b.get("text"), str)]
                text = "\n".join(parts).strip()
            elif isinstance(content, str):
                text = content.strip()
            else:
                text = ""
            if text:
                rows.append({"ts": record.get("timestamp"),
                             "record": record.get("uuid"), "text": text,
                             "role": "assistant"})
    return rows, malformed


def _user_text(content) -> str:
    """사용자 메시지 본문을 뽑는다. **문자열과 텍스트 블록 배열을 모두 읽는다.**

    배열을 안 읽어 발화가 통째로 빠지던 자리다 — 호스트 버전에 따라 같은 사용자 턴이
    `content: "..."` 로도, `content: [{"type": "text", "text": "..."}]` 로도 기록된다.
    **공개되고 안정된 JSONL 스키마가 없으므로** 한 형태만 읽는 것은 가정이지 계약이 아니다.

    `tool_result` 등 텍스트가 아닌 블록은 발화가 아니므로 버린다. 텍스트 블록이 여럿이면
    이어 붙인다(한 턴이 여러 블록으로 쪼개져 기록되는 경우).
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = [b["text"] for b in content
             if isinstance(b, dict) and b.get("type") == "text"
             and isinstance(b.get("text"), str)]
    return "\n".join(parts)


def _claude_models(handle) -> list[str]:
    """assistant 메시지의 `model` 을 파일 순서대로. 저작 모델을 **실측**하기 위한 것."""
    out: list[str] = []
    for line in handle:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = record.get("message") or {}
        if message.get("role") == "assistant" and isinstance(message.get("model"), str):
            out.append(message["model"])
    return out


def _codex_models(handle) -> list[str]:
    """Codex: `turn_context.payload.model`."""
    out: list[str] = []
    for line in handle:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("type") != "turn_context":
            continue
        model = (record.get("payload") or {}).get("model")
        if isinstance(model, str):
            out.append(model)
    return out


_MODEL_READERS = {"claude": _claude_models, "codex": _codex_models}


def measure_writer_model(path: Path, fmt: str = "claude") -> str | None:
    """저작 모델을 트랜스크립트에서 **실측**한다. 못 찾으면 None.

    **자기 신고를 쓰지 않는 이유는 실측으로 틀린 걸 봤기 때문이다.** E4 저장본의
    `writer_model` 이 `claude-opus-5` 였는데 저장을 실행한 것은 `claude-sonnet-5` 였다
    (같은 트랜스크립트에서 Opus 756 · Sonnet 37, 저장 시점 메시지는 전부 Sonnet).
    이 프로젝트가 신고를 세 번 불신하게 된 그 이유가 이 필드에도 그대로 있었다.

    **마지막 값을 쓴다** — 저장을 실행한 모델이 저작자다. 세션 전체 최빈값을 쓰면
    슬래시 커맨드가 모델을 갈아탄 경우(`model: sonnet` 핀)를 정확히 놓친다.
    """
    reader = _MODEL_READERS.get(fmt)
    if reader is None:
        return None
    try:
        with path.open(encoding="utf-8") as handle:
            models = reader(handle)
    except OSError:
        return None
    return models[-1] if models else None


def _codex_rows(handle) -> list[dict]:
    """Codex: `event_msg.payload.type == "user_message"` 의 `payload.message` 만.

    **`role == "user"` 를 쓰면 안 된다**(2026-08-17 Codex 실물 확인). `response_item` 쪽
    `role:user` 에는 `<environment_context>`·`AGENTS.md`·훅 프롬프트가 섞여 있다.

    **채널을 합치지 않는다.** 같은 발화가 `response_item`(9행)과 `event_msg`(10행) 양쪽에,
    그리고 `compacted.replacement_history` 에도 재기록된다. 이 한 채널만 읽으면 교차 중복이
    없고, 텍스트 해시로 dedup 하면 **진짜로 두 번 말한 것까지 지운다.**
    """
    rows: list[dict] = []
    malformed = 0
    for line in handle:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1  # 조용히 넘기지 않는다 — 불완전한 대장에 100% 판정이 나온다
            continue
        if record.get("type") != "event_msg":
            continue
        payload = record.get("payload") or {}
        # 압축 요약은 발화가 아니다. 앞 판은 Codex의 대장과 꼬리가 이 필드를 서로
        # 다르게 봤다. 지금은 `read_session` 한 판독이 둘을 함께 만들므로 레코드와
        # payload 양쪽 표시를 여기서 한 번 걸러 같은 입력을 보게 한다.
        if record.get("isCompactSummary") or payload.get("isCompactSummary"):
            continue
        if payload.get("type") != "user_message":
            continue
        text = payload.get("message")
        if not isinstance(text, str):
            continue
        rows.append({"ts": record.get("timestamp"), "text": text})
    return rows, malformed


_READERS = {"claude": _claude_rows, "codex": _codex_rows}


def count_malformed(path: Path, fmt: str = "claude") -> int:
    """트랜스크립트에서 **파싱 불가능한 줄 수**. 0이 아니면 대장이 불완전할 수 있다.

    이걸 밖으로 내는 이유: 깨진 줄을 조용히 건너뛰면 대장이 몇 건 빠진 채로도
    「모든 UID 를 처분했다」가 **100% 로 나온다.** 보증이 거짓이 되는 자리라 저장 게이트가
    이 값을 보고 거부한다.
    """
    reader = _READERS.get(fmt)
    if reader is None:
        return 0
    try:
        with path.open(encoding="utf-8") as handle:
            return reader(handle)[1]
    except OSError:
        return 0


#: 압축 경계 줄은 전사 **머리**에 있다(실측: 6번째 줄). 파일이 100MB 를 넘는 일이
#: 흔해 전체를 훑지 않는다.
#:
#: **줄 수 하나로 자르지 않는다.** 세션 시작 시 주입되는 메타 줄(`custom-title`·
#: `mode`·`atis-latch`·`queue-operation` …)의 개수는 호스트 버전·설정에 따라 달라져,
#: 실측 한 건에 맞춘 상한은 경계가 조금만 밀려도 조용히 못 찾는다. 바이트 상한을
#: 함께 두어 여유를 크게 잡고, 그래도 못 찾으면 `looks_compacted` 가 교차 확인한다.
_COMPACT_PEEK_LINES = 400
_COMPACT_PEEK_BYTES = 1 << 20

#: uuid 존재 검사용 청크. 파일이 커서 통째로 읽지 않는다.
_SCAN_CHUNK = 1 << 20

#: 체인 깊이 상한. 압축이 반복되면 길어지지만, 순환·손상 파일에서 무한히 돌지 않게 막는다.
_MAX_COMPACT_DEPTH = 32


def _peek_head(path: Path):
    """전사 **머리**를 줄 단위로 낸다. 줄 수와 바이트 둘 다 상한이다.

    `errors="ignore"` 를 쓴다. 안 쓰면 머리에 잘못된 바이트가 하나만 있어도
    `UnicodeDecodeError` 가 나고, 호출자가 그것을 「압축이 안 걸린 전사」와 구별하지
    못한다 — 앞부분을 통째로 잃고도 전수라고 보고하는 자리가 된다. 같은 모듈의
    `_file_contains` 는 처음부터 `errors="ignore"` 였다. 그 비대칭을 없앤다.
    """
    read = 0
    try:
        with path.open(encoding="utf-8", errors="ignore") as handle:
            for _ in range(_COMPACT_PEEK_LINES):
                line = handle.readline()
                if not line:
                    return
                read += len(line)
                yield line
                if read >= _COMPACT_PEEK_BYTES:
                    return
    except OSError:
        return


def looks_compacted(path: Path) -> bool:
    """경계 줄을 **못 찾았을 때** 압축 흔적이 있는지 교차 확인한다.

    압축된 전사는 요약을 `isCompactSummary: true` 인 사용자 메시지로 싣는다. 경계
    줄이 상한 밖으로 밀렸거나 손상돼 안 보여도 이 흔적은 남는다.

    **못 찾은 것과 없는 것을 가르는 것이 목적이다.** 둘이 같은 결과를 내면 증상이
    「가끔 앞부분이 빠진다」로만 보이고 원인을 못 찾는다.
    """
    for line in _peek_head(path):
        if "isCompactSummary" not in line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("isCompactSummary"):
            return True
    return False


def compact_boundary(path: Path) -> dict | None:
    """자동압축 경계 줄. 없으면 `None` — 이 전사가 체인의 **시작**이다.

    Claude Code 는 컨텍스트가 차면 대화를 **새 전사 파일로 잘라 옮긴다.** 그때 새 파일
    머리에 이런 줄을 남긴다.

        {"type": "system", "subtype": "compact_boundary",
         "logicalParentUuid": "929c711b-…",      ← 압축 **직전** 메시지의 uuid
         "compactMetadata": {"trigger": "auto", "preTokens": 999634, …}}

    **여기가 두 파일을 잇는 유일한 이음매다.** 잘린 앞부분은 자기 세션 아이디를 그대로
    달고 옛 파일에 남고(실측: 새 파일 안에 옛 세션 아이디를 단 발화 0건), 새 파일은
    `<새 세션 아이디>.jsonl` 이라 이름만으로는 앞을 찾을 수 없다.

    이 줄을 안 보면 대장이 뒷부분만 덮고도 「세션 전체」라고 말한다 — 실측으로
    277건 중 54건만 덮은 대장이 `scope: "full"` 로 나갔다(madi r75e).
    """
    for line in _peek_head(path):
        if '"compact_boundary"' not in line:
            continue                    # 파싱 전에 값싼 검사로 거른다
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("subtype") == "compact_boundary":
            return record
    return None


def _file_contains(path: Path, needles) -> bool:
    """큰 파일에서 문자열 **존재만** 본다. json 으로 파싱하지 않는다.

    선행 전사를 찾는 일은 「이 uuid 가 어느 파일에 있나」 하나뿐이라 구조가 필요 없다.
    실측 파일이 126MB 라 줄 단위 파싱은 비용이 맞지 않는다.

    **후보를 여럿 받아 한 번의 스캔으로 본다.** needle 마다 따로 부르면 첫 것이
    없을 때 같은 파일을 처음부터 다시 읽는다 — 선행을 못 찾는 경우 디렉터리 전체가
    두 배로 읽힌다(실측: 체인 탐색 1.53초 중 상당 부분).

    청크 경계에 걸친 문자열을 놓치지 않도록 **직전 꼬리를 이어 붙여** 검사한다.
    """
    if isinstance(needles, str):
        needles = (needles,)
    if not needles:
        return False
    span = max(len(n) for n in needles) - 1
    try:
        with path.open(encoding="utf-8", errors="ignore") as handle:
            tail = ""
            while True:
                chunk = handle.read(_SCAN_CHUNK)
                if not chunk:
                    return False
                window = tail + chunk
                if any(n in window for n in needles):
                    return True
                tail = chunk[-span:] if span else ""
    except (OSError, UnicodeError):
        return False


def _uuid_needles(uuid: str) -> tuple:
    """그 uuid 가 **레코드 필드**로 쓰인 형태. 본문 텍스트 언급과 구별한다.

    전사에는 훅 stdout·도구 결과가 그대로 실려 uuid 가 평문으로 박힌다 — 실측으로
    한 전사 안에 옛 세션 아이디가 809번 등장했고 **그 전부가 그런 언급**이었다
    (필드로는 0건). 맨 문자열로 찾으면 그 uuid 를 로그로 인용한 남의 세션 파일이
    선행으로 잡히고, 남의 발화가 대장에 섞인 채 진짜 앞부분은 여전히 빠진다.

    닫는 따옴표까지 포함하므로 더 긴 uuid 의 접두에 걸리지 않는다. 직렬화기에 따라
    콜론 뒤 공백이 있고 없으므로 두 형태를 본다.
    """
    return (f'"uuid":"{uuid}"', f'"uuid": "{uuid}"')


def find_predecessor(path: Path, uuid: str, seen: set) -> Path | None:
    """그 uuid 를 담은 형제 전사. 같은 프로젝트 디렉터리만 본다.

    **최근 수정 순으로 찾는다** — 압축의 선행은 거의 항상 직전 파일이라 첫 후보에서
    끝난다. 못 찾으면 `None` 이고, 호출자는 그것을 **끊긴 이음매**로 기록한다.
    """
    if not uuid:
        return None
    try:
        candidates = list(path.parent.glob("*.jsonl"))
    except OSError:
        return None
    siblings = []
    for sibling in candidates:
        # **후보 하나의 사고가 탐색 전체를 끝내면 안 된다.** 다른 세션이 쓰는 중이라
        # 잠긴 파일 하나 때문에 멀쩡한 선행이 「없음」으로 보고되면, 사용자는 옛
        # 전사가 지워진 줄 알고 찾지 않는다 — 경고가 떠도 원인이 거짓이면 소용없다.
        try:
            if sibling == path or sibling.resolve() in seen:
                continue
            siblings.append((sibling.stat().st_mtime, sibling))
        except OSError:
            continue
    siblings.sort(key=lambda item: item[0], reverse=True)
    needles = _uuid_needles(uuid)
    for _mtime, candidate in siblings:
        if _file_contains(candidate, needles):
            return candidate
    return None


def compact_chain(path: Path, fmt: str = "claude") -> tuple[list[Path], list[dict]]:
    """이 전사가 이어받은 **압축 이전 전사들까지** 포함한 경로 목록.

    반환은 `(경로들, 끊긴 이음매들)`. 경로는 **오래된 것부터** 시간순이라 그대로 이어
    읽으면 U-ID 가 대화 순서대로 붙는다.

    끊긴 이음매는 `{"reason", "logical_parent", "trigger", "after"}` 한 건이다.
    `reason` 셋 — 경계가 가리키는 파일을 못 찾음(`predecessor_missing`) · 압축 흔적은
    있는데 경계 줄을 못 봄(`boundary_not_found`) · 깊이 상한 소진(`depth_exhausted`).

    **셋 다 흔적을 남기는 것이 규율이다.** 하나라도 조용히 넘어가면 대장이 뒷부분만
    덮고도 전체라고 말하고, 증상은 「가끔 앞부분이 빠진다」로만 보인다.

    `codex` 형식에는 이 개념이 없다 — 그쪽은 rollout 파일이 한 스레드에 하나다.
    """
    if fmt != "claude":
        return [path], []
    chain = [path]
    gaps: list[dict] = []
    seen = set()
    try:
        seen.add(path.resolve())
    except OSError:
        pass
    current = path
    for _ in range(_MAX_COMPACT_DEPTH):
        record = compact_boundary(current)
        if record is None:
            # 경계 줄이 안 보인다. 압축 흔적까지 없으면 **체인의 시작**이고, 흔적이
            # 있으면 상한 밖으로 밀렸거나 손상된 것이라 못 찾았다고 적어야 한다.
            if looks_compacted(current):
                gaps.append({"reason": "boundary_not_found", "logical_parent": "",
                             "trigger": None, "after": current.name})
            break                       # 체인의 시작에 닿았다
        parent_uuid = record.get("logicalParentUuid") or ""
        previous = find_predecessor(current, parent_uuid, seen)
        if previous is None:
            meta = record.get("compactMetadata") or {}
            gaps.append({"reason": "predecessor_missing",
                         "logical_parent": parent_uuid,
                         "trigger": meta.get("trigger"),
                         "after": current.name})
            break
        try:
            seen.add(previous.resolve())
        except OSError:
            seen.add(previous)
        chain.append(previous)
        current = previous
    else:
        # 상한을 다 쓰고도 시작에 못 닿았다. 다른 두 종료 경로와 달리 여기만 흔적을
        # 안 남기면, 잘린 체인이 완전한 것으로 보고된다.
        gaps.append({"reason": "depth_exhausted", "logical_parent": "",
                     "trigger": None, "after": current.name})
    chain.reverse()                     # 오래된 것이 앞
    return chain, gaps


def extract_utterances(path: Path, since=None, fmt: str = "claude") -> list[dict]:
    """`U0001…` 을 붙인 사용자 발화 대장. 시간순, 판단 없음.

    **행의 필드를 여기서 세지 않는다.** 값이 늘 때마다 이 문장이 낡아 이 라운드에서만
    네 번 고쳤고, 그중 한 번은 판독이 새 필드를 흘리는 것을 아무 시험도 못 잡았다.
    필드는 코드가 정본이고, 이 문서는 **무엇을 뜻하는지**만 적는다.

    `kind` 는 `user`(사람이 친 것) · `system`(하네스 래퍼) · `peer`(다른 세션이 보낸 것)
    셋이고, `record` 는 호스트가 레코드에 단 아이디다(대화 꼬리와 잇는 값).

    `fmt` 는 **어댑터가 명시한다**(`claude`|`codex`) — 코어가 내용으로 「어느 호스트 것인가」를
    추측하지 않는다. 벤더 사정은 어댑터가 알고, 코어에는 번역된 값만 온다.

    `since` 를 주면 **그 시각 이후 발화만** 남긴다(한 세션에서 두 번째 이상 저장할 때
    「직전 핸드오프 이후」 구간만 정리하는 용도). 경계는 직전 저장본의 `created` 라
    사람 판단이 안 들어간다.

    **U-ID 는 필터 전에 세션 전체 기준으로 매긴다.** 그래야 저장본이 여럿일 때 같은 발화가
    항상 같은 번호를 갖고, 두 번째 저장본의 대장이 `U0043…` 로 시작하는 것만 봐도
    **델타임이 드러난다.** 필터 뒤에 다시 매기면 번호가 저장본마다 충돌한다.
    """
    return extract_utterances_from([path], since, fmt=fmt)


def read_session(paths, since=None, fmt: str = "claude", tail_limit: int = 30) -> dict:
    """체인을 **한 번 읽어** 대장·대화 꼬리·손상 줄·못 읽은 파일을 함께 낸다.

    반환 `{"rows", "tail", "malformed", "unreadable"}`.

    **꼬리 행은 대장 행과 같은 객체다.** 사람 발화면 `uid` 를 그대로 갖고 있으므로
    「대장의 어느 발화가 꼬리에 있나」를 **이을 필요가 없다.** 앞 판은 둘을 따로 읽어
    사후에 이었고, 그 잇기 단계가 이 라운드에서 여덟 번 터졌다 — 텍스트로 맞추다 짧은
    발화가 접두로 오탐되고(409건 소실), 구간으로 잘라 창 밖 발화가 사라지고(1건), 그
    수리가 문면·계약·시험을 차례로 어긋내며 회로가 돌았다. **이을 것이 없으면 그 자리가
    없다.**

    읽기 실패를 한 자리에서 처리하는 것도 그대로다 — 소비자마다 따로 읽으면 어디서는
    예외를 올리고 어디서는 0으로 세고 어디서는 조용히 건너뛴다.
    """
    reader = _READERS.get(fmt)
    if reader is None:
        raise ValueError(f"알 수 없는 트랜스크립트 형식: {fmt} (claude|codex)")

    collected: list[dict] = []
    carried = set()
    malformed = 0
    unreadable: list[str] = []
    for path in paths:
        try:
            with Path(path).open(encoding="utf-8") as handle:
                raw_rows, broken = reader(handle)
        except (OSError, UnicodeError):
            unreadable.append(Path(path).name)
            continue
        malformed += broken
        fresh = []
        for raw in raw_rows:
            text = (raw.get("text") or "").strip()
            if not text:
                continue
            fresh.append({"ts": raw.get("ts"), "text": text,
                          "kind": raw.get("kind"), "record": raw.get("record"),
                          "role": raw.get("role") or "user"})
        # **레코드 아이디로 가른다.** 압축이 옮겨 적는 것은 같은 레코드라 아이디가 같고,
        # 서로 다른 발화는 본문과 시각이 같아도 다르다.
        collected.extend(row for row in fresh if _carry_key(row) not in carried)
        carried.update(k for k in map(_carry_key, fresh) if k is not None)

    # 시각 순서로 한 번만 정렬한다 — 대장 번호와 꼬리 순서가 같은 기준을 쓴다.
    collected.sort(key=lambda r: parse_ts(r["ts"]) or _EPOCH)
    for row in collected:
        if row["role"] == "user" and not row.get("kind"):
            # 하네스 래퍼도 `role: user` 로 오지만 사람이 친 발화가 아니다. 대장에는
            # 남기되 `system` 으로 분류해야 그 UID 가 권위를 얻지 못한다. 수집한 쪽이
            # 신분을 정했으면(`peer`) 그것을 쓴다.
            row["kind"] = "system" if _is_harness_noise(row["text"]) else "user"

    # **U-ID 는 필터 전에 세션 전체 기준으로 매긴다** — 저장본이 여럿일 때 같은 발화가
    # 항상 같은 번호를 갖는다. 조수 행은 대장에 안 들어가므로 번호가 없다.
    numbered = 0
    ledger: list[dict] = []
    for row in collected:
        if row["role"] != "user":
            continue
        numbered += 1
        row["uid"] = f"U{numbered:04d}"
        ledger.append(row)

    # 꼬리는 **노이즈를 뺀** 대화의 마지막 `tail_limit` 건이다. 같은 목록에서 고르므로
    # 여기 든 사람 발화는 위에서 받은 `uid` 를 그대로 갖는다.
    tail = [r for r in collected if r.get("kind") != "system"][-tail_limit:]

    if since is not None:
        boundary = since
        ledger = [r for r in ledger if (parse_ts(r["ts"]) or _EPOCH) > boundary]
    return {"rows": ledger, "tail": tail,
            "malformed": malformed, "unreadable": unreadable}


def read_manifest(paths, since=None, fmt: str = "claude") -> dict:
    """대장만 필요한 호출자를 위한 껍질. 정본은 `read_session` 이다."""
    out = read_session(paths, since, fmt=fmt)
    return {"rows": out["rows"], "malformed": out["malformed"],
            "unreadable": out["unreadable"]}


def _carry_key(row: dict):
    """파일 경계를 넘는 **같은 레코드**를 가리키는 키. 못 가르면 `None`(= 접지 않는다).

    압축은 직전 메시지를 새 전사에 옮겨 적는데 그것은 같은 레코드라 아이디가 같다.
    아이디가 없는 형식은 시각+본문으로 물러서지만, **시각이 없으면 접을 근거가 없다** —
    본문만 같은 서로 다른 발화를 지우게 된다.

    `None` 은 `carried` 에 넣지 않으므로 `not in carried` 가 항상 참이 되어 접히지 않는다.

    **`uid` 와 `record` 는 다른 것이다.** `uid` 는 번호 매기기가 붙이는 `U0001` 이고
    `record` 는 호스트가 레코드에 단 아이디다. 한 이름이 단계마다 다른 뜻을 가지면
    어느 단계의 값인지가 호출 순서에 숨는다 — 이름 하나에 뜻 하나를 준다.

    **키는 해시 가능해야 한다** — 이 값들은 호스트 JSONL 에서 그대로 온다. 스키마가
    공개·안정된 것이 아니라 형태는 **가정이지 계약이 아니고**(모듈 머리 주석), 실제로
    아이디가 객체로 오면 집합에 넣는 순간 `TypeError` 로 명령 전체가 죽는다(외부 리뷰
    실행 확인). 이 파일이 `content`·`prompt` 에 이미 하는 타입 검사를 여기도 한다.
    """
    rec = row.get("record")
    if isinstance(rec, str) and rec:
        return ("record", rec)
    ts = row.get("ts")
    if isinstance(ts, str) and ts:
        return ("ts", ts, row["text"])
    return None


def extract_utterances_from(paths, since=None, fmt: str = "claude") -> list[dict]:
    """여러 전사를 **한 대장으로** 읽는다. 자동압축이 대화를 가른 경우에 쓴다.

    `paths` 는 오래된 것부터 시간순이라야 한다(`compact_chain` 이 그 순서로 낸다).
    다만 U-ID 는 파일 순서가 아니라 **시각 순서**로 붙는다 — 그래서 파일이 몇 개로
    갈렸든 대장 하나를 이어 읽은 것과 같은 번호가 나온다.

    합치는 지점을 하나로 둔 이유: 파일마다 U0001 부터 다시 세면 두 대장을
    **기계적으로 합칠 수 없다**(실측으로 사람이 손으로 합치려다 포기했다 — madi r75e).

    **읽기 실패를 삼키지 않는다.** 하나라도 못 읽으면 예외를 올린다 — 조용히
    건너뛰면 앞부분이 빠진 대장이 전수로 보고된다. 못 읽은 것을 결과로 받고 싶으면
    `read_manifest` 를 쓴다.
    """
    out = read_manifest(paths, since, fmt=fmt)
    if out["unreadable"]:
        raise OSError("전사를 읽지 못했다: " + ", ".join(out["unreadable"]))
    return out["rows"]

def human_utterance_uids(rows: list[dict]) -> set[str]:
    """대장 정본에서 **사람이 친 발화** UID만 낸다.

    사람이 아닌 것도 대장에는 보존하되 다른 `kind` 를 단다 — 하네스 래퍼는 `system`,
    다른 세션이 보낸 발화는 `peer`. 결정 인용·상시 규율의 걸기와 풀기는 모두 이 집합만
    권위로 쓴다. 소비 지점마다 `kind` 조건을 덧대면 같은
    권한 회로가 다시 커지므로, 사람이 누구인지는 여기서 한 번만 정한다.
    """
    return {str(row.get("uid")) for row in rows
            if row.get("kind") == "user" and row.get("uid")}


def excerpt(text: str, limit: int = 120) -> str:
    """대장 표에 실을 지문. 줄바꿈을 접고 길이를 자른다(원문은 트랜스크립트에 있다).

    **원문을 되돌려주지 않는 이유**: 저장하는 세션은 그 발화를 이미 컨텍스트에 들고 있으므로
    (핸드오프는 자동압축 전에 한다) 순수 중복이고, 실측 사고가 있었다 — 원문까지 실어 55KB
    (≈18k 토큰)를 반환했더니 컨텍스트 96% 세션에서 **그 자리에서 자동압축이 돌았다.**
    """
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"

#: 호스트가 사용자 역할로 밀어 넣는 **명령 실행 기록**. 사람이 친 말이 아니다.
#: 실측(2026-08-18): `/model` 한 번이 꼬리 30칸 중 2칸을 먹었다 — 실제 대화가 그만큼 밀린다.
#: 둘 다 대장에는 남기되 `system` 으로 분류하고, 꼬리에서는 뺀다. 역할이 `user` 인
#: 하네스 래퍼를 사람 발화로 분류하면 권위 검증의 입력 집합까지 오염된다.
_HARNESS_WRAPPERS = (
    "<local-command-caveat",
    "<command-name",
    "<command-message",
    "<command-args",
    "<local-command-stdout",
    "<local-command-stderr",
)


def _is_harness_noise(text: str) -> bool:
    """꼬리에서 빼야 할 하네스 산출물인가. 대장 기준(`_SYSTEM_PREFIXES`)도 함께 본다."""
    stripped = text.lstrip()
    return stripped.startswith(_SYSTEM_PREFIXES) or stripped.startswith(_HARNESS_WRAPPERS)


def read_dialogue_tail(paths, fmt: str = "claude", limit: int = 30) -> dict:
    """대화 꼬리만 필요한 호출자를 위한 껍질. 정본은 `read_session` 이다."""
    out = read_session(paths, fmt=fmt, tail_limit=limit)
    return {"rows": out["tail"], "unreadable": out["unreadable"]}


def extract_dialogue_tail(path: Path, fmt: str = "claude", limit: int = 30) -> list[dict]:
    """전사 하나의 대화 꼬리 — `Recent Dialogue` 절의 원료. `read_session` 껍질이다.

    자동압축 분석(2026-08-18)에서 가져온 부품이다: 압축이 97% 를 버리고도 맥락이 이어지는
    이유는 요약 옆에 **최근 대화 원문**이 붙어 있어서였다. 30 이라는 기본값은 압축의
    검증된 보존 규모(실측 30~45건)에서 왔다 — 1~2건 안은 사용자가 기각했다("방향까지
    전달하려면 15건 이상").

    **판독기를 따로 두지 않는다.** 대장과 꼬리를 다른 판독기로 읽으면 필터가 갈리고,
    실제로 갈렸다 — 새 경로는 다른 세션 발화를 꼬리에 담는데 옛 경로는 `isMeta` 에서
    걸렀다. 이 라운드의 결함 여덟 건이 전부 「같은 것을 두 곳에서 읽는다」에서 나왔다.
    """
    return read_session([path], fmt=fmt, tail_limit=limit)["tail"]
