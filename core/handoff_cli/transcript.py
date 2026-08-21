"""세션 트랜스크립트에서 **사용자 발화 대장**을 코드로 뽑는다 (R8 3안).

왜 코드인가:
    대장의 가치는 **믿을 수 있다**는 것 하나에 걸려 있다. 대장이 불완전하면 빠진 발화는
    여전히 조용히 사라지는데 겉보기엔 완전해 보여서 지금보다 나쁘다. 그래서 모델을 쓰지
    않는다 — 실측(2026-08-17): 같은 일을 Haiku 서브에이전트에 맡겼더니 긴 붙여넣기를
    잘라먹고 개수를 48개라 보고했는데 실제 43개였다. **LLM 을 쓰는 순간 자기 신고로 돌아간다.**

무엇이 「발화」인가 — 판단이 아니라 형식으로 가른다:
    - `message.content` 가 **문자열**이면 사용자가 친(또는 붙여넣은) 발화다.
    - `content` 가 `tool_result` 블록 리스트면 도구 출력이다 — 발화가 아니다.
    - `isMeta` 는 스킬·시스템 주입이다 — 발화가 아니다.
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

__all__ = ["derive_transcript_path", "extract_utterances", "human_utterance_uids",
            "extract_dialogue_tail", "parse_ts", "excerpt", "measure_writer_model", "count_malformed",
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
        if record.get("isMeta") or record.get("isSidechain"):
            continue

        attachment = record.get("attachment") or {}
        if attachment.get("type") == "queued_command":
            origin = attachment.get("origin") or {}
            prompt = attachment.get("prompt")
            if (origin.get("kind") == "human"
                    and attachment.get("commandMode") == "prompt"
                    and isinstance(prompt, str)):
                rows.append({"ts": record.get("timestamp") or attachment.get("timestamp"),
                             "text": prompt})
            continue

        message = record.get("message") or {}
        if message.get("role") != "user":
            continue
        text = _user_text(message.get("content"))
        if text:
            rows.append({"ts": record.get("timestamp"), "text": text})
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


def extract_utterances(path: Path, since=None, fmt: str = "claude") -> list[dict]:
    """`U0001…` 을 붙인 사용자 발화 대장. 시간순, 판단 없음.

    반환 항목: `{"uid", "kind", "ts", "text"}` — `kind` 는 `user` 또는 `system`.

    `fmt` 는 **어댑터가 명시한다**(`claude`|`codex`) — 코어가 내용으로 「어느 호스트 것인가」를
    추측하지 않는다. 벤더 사정은 어댑터가 알고, 코어에는 번역된 값만 온다.

    `since` 를 주면 **그 시각 이후 발화만** 남긴다(한 세션에서 두 번째 이상 저장할 때
    「직전 핸드오프 이후」 구간만 정리하는 용도). 경계는 직전 저장본의 `created` 라
    사람 판단이 안 들어간다.

    **U-ID 는 필터 전에 세션 전체 기준으로 매긴다.** 그래야 저장본이 여럿일 때 같은 발화가
    항상 같은 번호를 갖고, 두 번째 저장본의 대장이 `U0043…` 로 시작하는 것만 봐도
    **델타임이 드러난다.** 필터 뒤에 다시 매기면 번호가 저장본마다 충돌한다.
    """
    reader = _READERS.get(fmt)
    if reader is None:
        raise ValueError(f"알 수 없는 트랜스크립트 형식: {fmt} (claude|codex)")

    collected: list[dict] = []
    with path.open(encoding="utf-8") as handle:
        raw_rows, malformed = reader(handle)
    for raw in raw_rows:
        text = (raw.get("text") or "").strip()
        if not text:
            continue
        collected.append({"ts": raw.get("ts"), "text": text})

    # **시각으로 정렬한 뒤에 U-ID 를 매긴다.** 큐 발화(`queued_command`)는 전달 시점이 아니라
    # **입력 시점**의 타임스탬프를 달고 있어 파일 순서와 시간 순서가 어긋난다. 파일 순서로
    # 번호를 매기면 대장이 시간순이 아니게 되고, `since` 필터도 어긋난다.
    # 안정 정렬이라 같은 시각은 파일 순서를 유지한다.
    collected.sort(key=lambda r: parse_ts(r["ts"]) or _EPOCH)

    # 채널 간 dedup 은 **하지 않는다.** 두 채널은 서로 다른 레코드라 겹치지 않고(실측: 큐 6건
    # 중 `role=user` 에도 있는 것 0건), 텍스트로 지우면 **진짜로 두 번 말한 것까지 지운다.**
    rows: list[dict] = []
    for raw in collected:
        text = raw["text"]
        rows.append({
            "uid": f"U{len(rows) + 1:04d}",
            # 하네스 명령 래퍼도 `role=user` 로 오지만 사람이 친 발화가 아니다. 대장에는
            # 남기되 `system` 으로 분류해야, 그 UID 가 상시 규율의 폐기 권한을 얻지 못한다.
            "kind": "system" if _is_harness_noise(text) else "user",
            "ts": raw["ts"],
            "text": text,
        })
    if since is None:
        return rows
    return [r for r in rows if (parse_ts(r["ts"]) or _EPOCH) > since]


def human_utterance_uids(rows: list[dict]) -> set[str]:
    """대장 정본에서 **사람이 친 발화** UID만 낸다.

    하네스 래퍼도 대장에는 보존하지만 `system`이다. 결정 인용·상시 규율의 걸기와
    풀기는 모두 이 집합만 권위로 쓴다. 소비 지점마다 `kind` 조건을 덧대면 같은
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


def _claude_dialogue(handle) -> list[dict]:
    """Claude JSONL 에서 **대화만** — 사용자 발화 + assistant 텍스트 블록.

    도구 결과(`tool_result`)·도구 호출(`tool_use`)·attachment·sidechain·메타는 뺀다.
    부피의 주범은 도구 출력이지 대화가 아니다(실측: 꼬리 30건이 도구 포함 3,196 tok,
    대화만 3,156 tok — 대화 자체는 가볍다).
    """
    rows: list[dict] = []
    for line in handle:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("isMeta") or record.get("isSidechain"):
            continue
        if record.get("isCompactSummary"):
            # 자동압축 요약은 하네스 산출물이지 이 세션의 대화가 아니다.
            continue

        # **큐 채널도 대화다.** `queued_command` 는 `message.role` 이 없어 아래 분기에서
        # 통째로 빠졌다 — 대장에는 있는데 꼬리에는 없어서, 마지막 방향 전환이 큐 입력이면
        # 「직전 대화의 육성」에서 정확히 그것만 사라졌다(외부 리뷰 지적, 실측 7건).
        attachment = record.get("attachment") or {}
        if attachment.get("type") == "queued_command":
            origin = attachment.get("origin") or {}
            prompt = attachment.get("prompt")
            if (origin.get("kind") == "human"
                    and attachment.get("commandMode") == "prompt"
                    and isinstance(prompt, str) and prompt.strip()
                    and not _is_harness_noise(prompt)):
                rows.append({"role": "user",
                             "ts": record.get("timestamp") or attachment.get("timestamp"),
                             "text": prompt.strip()})
            continue

        message = record.get("message") or {}
        role = message.get("role")
        if role == "user":
            text = _user_text(message.get("content"))
            if text and not _is_harness_noise(text):
                rows.append({"role": "user", "ts": record.get("timestamp"), "text": text})
        elif role == "assistant":
            if record.get("isApiErrorMessage"):
                # API 오류 문구는 조수의 말이 아니다 — 꼬리 30칸을 먹으면 실제 대화가 밀린다.
                continue
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
                rows.append({"role": "assistant", "ts": record.get("timestamp"),
                             "text": text})
    return rows


def _codex_dialogue(handle) -> list[dict]:
    """Codex rollout 에서 대화만 — `event_msg` 의 `user_message`/`agent_message`."""
    rows: list[dict] = []
    for line in handle:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (record.get("isMeta") or record.get("isSidechain")
                or record.get("isCompactSummary") or record.get("isApiErrorMessage")):
            continue
        if record.get("type") != "event_msg":
            continue
        payload = record.get("payload") or {}
        if (payload.get("isMeta") or payload.get("isSidechain")
                or payload.get("isCompactSummary") or payload.get("isApiErrorMessage")):
            continue
        kind = payload.get("type")
        if kind not in ("user_message", "agent_message"):
            continue
        text = payload.get("message")
        if isinstance(text, str) and text.strip() and not _is_harness_noise(text):
            rows.append({"role": "user" if kind == "user_message" else "assistant",
                         "ts": record.get("timestamp"), "text": text.strip()})
    return rows


_DIALOGUE_READERS = {"claude": _claude_dialogue, "codex": _codex_dialogue}


def extract_dialogue_tail(path: Path, fmt: str = "claude", limit: int = 30) -> list[dict]:
    """대화 꼬리 `limit` 건 — `Recent Dialogue` 절의 원료.

    자동압축 분석(2026-08-18)에서 가져온 부품이다: 압축이 97% 를 버리고도 맥락이 이어지는
    이유는 요약 옆에 **최근 대화 원문**이 붙어 있어서였다. 단, 압축과 달리 여기서는
    도구 결과를 빼고 **대화만** 남긴다 — 방향은 대화의 육성으로 전달되고, 상태는
    `Git State`·`Files Touched` 가 따로 든다.

    30 이라는 기본값은 압축의 검증된 보존 규모(실측 30~45건)에서 왔다. 1~2건 안은
    사용자가 기각했다 — "방향까지 전달하려면 15건 이상".
    """
    reader = _DIALOGUE_READERS.get(fmt)
    if reader is None:
        return []
    try:
        with path.open(encoding="utf-8") as handle:
            rows = reader(handle)
    except OSError:
        return []
    rows.sort(key=lambda r: parse_ts(r["ts"]) or _EPOCH)
    return rows[-limit:]
