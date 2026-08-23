"""명령 디스패치 — 어댑터가 호출하는 공용 진입점.

모든 파일쓰기는 여기(및 하위 모듈)에서만 일어난다. 어댑터는 구조화 입력(dict/JSON)을
넘기고 결과 dict 를 받아 사용자에게 보고만 한다.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from . import atomicio, current, detail, messages, repo, status as status_mod, topics
from . import transcript as transcript_mod

__all__ = ["cmd_save", "cmd_list", "cmd_find", "cmd_resume", "cmd_archive", "cmd_reindex",
           "cmd_decisions", "cmd_negative", "cmd_utterances"]


_VALID_SOURCES = ("claude-code", "codex")

# 어댑터가 채우는 절 **11개**. `Git State`·`Files Touched`·`Utterance Ledger` 는 CLI 가
# 만드는 데이터 블록이라 여기 없다 — 그 셋을 합쳐 본문 헤딩은 14개다.
_CANONICAL_SECTION_KEYS = (
    "intent",
    "done", "open", "failed_attempts", "not_tried",
    "blockers", "decisions", "unapproved", "exact_next_step", "verification",
    "lessons", "standing", "session_recap", "incidents",
)

# 절 키 ↔ 본문 헤딩. 처분 대장의 `담긴 곳` 이 헤딩 이름으로 오므로 되돌릴 표가 필요하다.
# `detail.LEDGER_DESTINATIONS` 는 이 표의 **부분집합**이다(테스트가 검사한다) —
# `Session Recap` 만 목적지가 아니다: 대장 **전체의** 요약이라, 발화 하나를 「요약에
# 담았다」로 처분하는 것은 순환이고 회피다.
_SECTION_HEADINGS = {
    "intent": "Intent And Purpose",
    "done": "Done",
    "open": "Open",
    "failed_attempts": "Failed Attempts",
    "not_tried": "Not Tried Yet",
    "blockers": "Blockers And Questions",
    "decisions": "Decisions",
    "unapproved": "Unapproved Proposals",
    "exact_next_step": "Exact Next Step",
    "verification": "Verification",
    "incidents": "Incidents",
    "lessons": "Lessons",
    "standing": "Standing Directives",
    "session_recap": "Session Recap",
}

# alias 테이블은 "정규화 규칙을 거쳐도 canonical 과 형태가 다른 것"만 담는다.
# 대소문자·공백·하이픈 변형은 정규화 규칙 자체가 이미 처리하므로 alias 로 중복 등재하지 않는다
# (예: "Done"/"DONE"/"failed attempts"/"Failed Attempts" 는 alias 테이블에 넣지 않는다 —
#  아래 정규화만으로 이미 canonical 과 일치한다).
_SECTION_KEY_ALIASES = {
    "not_tried_yet": "not_tried",
    "blockers_and_questions": "blockers",
    "unapproved_proposals": "unapproved",
    "intent_and_purpose": "intent",
}


def _default_global_root(source: str = "claude-code") -> str:
    """Writer-local global index root.

    The project `.handoff/` tree remains the shared source of truth. The thin
    CURRENT.md index defaults to the writer's own app directory so Codex saves
    are not blocked or made noisy by a dirty/ahead Claude config repo.
    """
    if source == "codex":
        return os.path.expanduser("~/.codex")
    return os.path.expanduser("~/.claude")


def _validate_source(raw: str | None, warnings: list[str], lang: str) -> str:
    """source 를 화이트리스트로 게이트한다 (frontmatter 주입 방어).

    개행/미인식 값은 거부하고 claude-code 로 강등 + 경고.
    """
    value = (raw or "claude-code").strip()
    if value not in _VALID_SOURCES:
        warnings.append(
            messages.msg("warn_unknown_source", lang, value=value,
                        allowed=", ".join(_VALID_SOURCES))
        )
        return "claude-code"
    return value


def _normalize_section_key(raw_key: str) -> str | None:
    """strip → lowercase → 영숫자 외 문자는 전부 `_` 로 치환 → 연속/양끝 `_` 제거.

    "Blockers & Questions" -> "blockers_questions" (alias 미매치 → 미인식 키로 경고,
    크래시는 없음 — 특수문자 포함 입력에 대한 방어적 동작이지 신규 alias 요구사항 아님).
    "Not Tried Yet?" -> "not_tried_yet" (트레일링 특수문자에도 안전).
    """
    normalized = re.sub(r"[^a-z0-9]+", "_", raw_key.strip().lower()).strip("_")
    if normalized in _CANONICAL_SECTION_KEYS:
        return normalized
    return _SECTION_KEY_ALIASES.get(normalized)


def _normalize_sections(raw_sections: object, warnings: list[str], lang: str) -> dict:
    """반환값은 canonical 키만 key 로 갖는 *부분* dict 다 — 매핑 안 된 canonical 은
    key 자체가 없다(전체 9-key dict 로 미리 채우지 않는다). assemble_body() 의 기존
    `sections.get('done')` 류 `.get()` 호출이 그대로 기본값 fallback 을 처리하므로
    이 반환 계약과 100% 호환된다. assemble_body() 시그니처·로직은 수정하지 않는다.
    """
    if not isinstance(raw_sections, dict):
        if raw_sections:  # None/빈 값이 아닌데 dict 도 아니면 입력 오류
            warnings.append(messages.msg("warn_invalid_sections", lang))
        return {}

    # canonical 별로 (원본 키, 값) 을 raw_sections 순회 순서(=payload insertion order)대로 버킷팅.
    buckets: dict[str, list[tuple[str, object]]] = {}
    for raw_key, value in raw_sections.items():
        if not isinstance(raw_key, str):
            warnings.append(messages.msg("warn_unknown_section_key", lang, section_key=repr(raw_key)))
            continue
        canonical = _normalize_section_key(raw_key)
        if canonical is None:
            warnings.append(messages.msg("warn_unknown_section_key", lang, section_key=raw_key))
            continue
        buckets.setdefault(canonical, []).append((raw_key, value))

    result: dict[str, object] = {}
    for canonical, entries in buckets.items():
        if len(entries) == 1:
            kept_key, kept_value = entries[0]
        else:
            # 충돌 해소(2개든 3개 이상이든 동일 규칙): (1) 원본 키가 canonical 문자열과
            # 정확히 같은 항목이 있으면 값 유무와 무관하게 그 값을 채택(빈 문자열이어도
            # 채택 — 인지된 트레이드오프) (2) 없으면 buckets 진입 순서(=raw_sections 순회 중
            # 그 canonical 에 처음 도달한 원본 키) 채택 (3) 나머지는 전부 ignored_keys.
            exact = [e for e in entries if e[0] == canonical]
            kept_key, kept_value = exact[0] if exact else entries[0]
            ignored_keys = [k for k, _ in entries if k != kept_key]
            warnings.append(messages.msg(
                "warn_duplicate_section_key", lang,
                canonical=canonical, kept=kept_key, ignored=", ".join(ignored_keys),
            ))
        # value 자체가 문자열이 아니면(외부 payload 가 int/list/dict 등을 보낸 경우)
        # assemble_body()._section() 의 `(value or "").strip()` 에서 크래시하므로,
        # 여기서 버킷에 넣지 않고 경고만 남긴다(강제 str() 형변환은 하지 않는다 —
        # 계약 지시: dict/list 를 문자열화하면 쓰레기가 본문에 그대로 남는다).
        if not isinstance(kept_value, str):
            warnings.append(messages.msg(
                "warn_invalid_section_value", lang,
                canonical=canonical, section_key=kept_key,
            ))
            continue
        result[canonical] = kept_value
    return result


def _cross_project_files(root: str, files_touched: object) -> list[str]:
    """files_touched 중 프로젝트 루트 밖 경로를 찾아 정규화된 문자열 목록으로 반환한다.

    결정적 판정: `~` 전개 → 상대경로는 root 기준 결합 → `.resolve()`(심볼릭 해소) →
    `Path.is_relative_to(root.resolve())` 로 containment 판정(문자열 prefix 금지 —
    형제 `<root>-old` 오판 방지). path 가 str 이 아니거나 결측/빈문자면 skip.

    resolve() 실패(널바이트·overlong·심볼릭 루프 등)는 예외 종류(OSError·ValueError·
    RuntimeError)를 불문하고 **원본 경로를 그대로 플래그**한다 — 위치 판정 불가는 '루트
    안=안전'이 아니라 '의심'이므로 조용히 흘리지 않는다(fail toward warning). 이로써
    지원 Python 범위(3.10+)에서 resolve 예외로 save 가 크래시하는 경로도 함께 봉쇄된다.

    재개 컨텍스트 오염 방어의 보조 결정적 게이트다 — narrative(자유서술) 오염은 CLI 가
    판정 불가라 어댑터 규칙이 정본이고, 이 함수는 결정적으로 판정 가능한 경로 표면만 맡는다.
    """
    if not isinstance(files_touched, list):
        return []
    root_resolved = Path(root).resolve()
    out: list[str] = []
    for entry in files_touched:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        if not isinstance(path, str) or not path.strip():
            continue
        expanded = os.path.expanduser(path)
        candidate = Path(expanded)
        if not candidate.is_absolute():
            candidate = Path(root) / candidate
        try:
            resolved = candidate.resolve()
        except (OSError, ValueError, RuntimeError):
            # 판정 불가 → 안전으로 흘리지 않고 원본 경로를 플래그(크래시도 방지).
            out.append(path)
            continue
        if not resolved.is_relative_to(root_resolved):
            out.append(str(resolved))
    return out


def _resume_prompt(project_name: str, root: str, topic: str, summary: str, lang: str) -> str:
    """새 세션에 그대로 붙여넣어 이어가는 프롬프트. 결정적(같은 입력→바이트 동일).

    **짧게 유지한다.** 긴 재개 지시는 여기 싣지 않고 `resume_directives()` 가 재개 시점에
    직접 낸다 — 저장 세션이 긴 report 를 중계하다 빠뜨리는 사고가 반복됐고(사용자 실측),
    모델에게 긴 원문 중계를 시키는 구조 자체가 취약하기 때문이다. 부수 효과로 지시문이
    저장 시점에 동결되지 않아 옛 저장본을 재개해도 항상 최신 지시가 나간다.

    마지막 줄의 **위임 문장이 필수 부품**이다. 도구 출력은 「관찰된 데이터」라 사용자 발화보다
    권위가 낮은데, 이 문장이 그것을 사용자 지시로 격상시킨다(3벤더 실측으로 확인).

    프로젝트명 우선 + 절대경로는 힌트(크로스머신). summary 는 공백·개행을 1줄로 접고,
    사용자 미입력(=topic 폴백)이면 요약 줄을 생략한다.
    """
    # 공백·개행 1줄화 + 코드펜스(```) 무력화 — report 의 ```text 블록이 깨지지 않게.
    summary_line = " ".join(summary.split()).replace("```", "'''")
    lines = [
        messages.msg("resume_intro1", lang),
        "",
        messages.msg("resume_project_line", lang, project_name=project_name, root=root),
        messages.msg("resume_topic_line", lang, topic=topic),
    ]
    if summary_line and summary_line != topic:
        lines.append(messages.msg("resume_summary_line", lang, summary_line=summary_line))
    lines += [
        "",
        messages.msg("resume_pointer", lang, topic=topic),
    ]
    return "\n".join(lines)


def _defuse_block_markers(value: str) -> str:
    """적재된 원문 안의 **줄머리** 블록 표식을 한 칸 들여쓴다. 글자는 지우지 않는다.

    블록 표식(`━━━ N. … ━━━`)은 무엇으로 바꾸든, **그것을 논한 대화가 다음 세션의 꼬리에
    실리는 순간** 가짜 경계가 된다. 실제로 세 번 났고 세 번 다 「블록이 오염됐다」는 오보로
    이어졌다 — 문제를 설명한 기록이 다음 측정을 망가뜨리는 자기재생산 고리다.

    저장 측과 **같은 규칙을 쓴다**(`detail.defuse_boundary_lines`). 규칙이 두 벌이면 어느
    쪽이 무엇을 막는지 아무도 모르고, 옛 저장본은 저장 측 방어가 없던 시절 것이라 재개
    쪽에도 같은 방어가 필요하다.
    """
    return detail.defuse_boundary_lines(value)


def _decision_projection(resolved, topic: str) -> dict:
    """**대장 스냅샷이 확정한 재개 투영본을 고른다.** 여기서는 재판정하지 않는다.

    최신판·생사·완전성·공백은 `_decision_ledger_snapshot()` 안에서 함께 확정된다.
    스냅샷 호출 자체가 실패했을 때만 빈 **불완전** 투영으로 닫는다.
    """
    try:
        snapshot = _decision_ledger_snapshot(resolved)
    except Exception:
        return {"items": [], "span": None, "complete": False}
    return snapshot["resume"].get(topic, {
        "items": [], "span": None,
        "complete": topic not in snapshot["topology"]["incomplete_topics"]})


def _render_decisions(projection: dict, lang: str) -> str:
    """**투영본을 표시만 한다.** 사슬을 걷지 않고, 최신판을 고르지 않고, 생사를 추론하지
    않고, ID 를 해석하지 않고, 예외를 잡아 빈 자료구조에 의미를 부여하지 않는다.

    새 사례가 생기면 여기에 조건을 덧대지 말고 **그것이 대장의 새 도메인 상태인지
    기존 상태의 표시 변경인지 먼저 판정한다**(기획 v7 §4.6). 전자는 투영 계약에서,
    후자는 적재 목록 표에서 처리한다.
    """
    items, span, complete = projection["items"], projection["span"], projection["complete"]
    if not items:
        # 「완전하고 0건」과 「읽지 못해 0건」은 다른 결과다.
        return messages.msg("resume_none_recorded" if complete
                            else "resume_decisions_incomplete", lang)

    parts = []
    if span:
        # 동결 저장 형식에는 숫자 공백의 원인을 증명하는 source manifest가 없다.
        # `complete`를 원인 보증으로 재사용하지 않고 모든 공백을 미분류로 표시한다.
        key = ("resume_decision_span" if not span["gaps"]
               else "resume_decision_gaps_unproven")
        parts.append(messages.msg(
            key, lang, total=len(items), first=span["first"], last=span["last"],
            gaps=", ".join("D%d" % n for n in span["gaps"])))
    if not complete:
        parts.append(messages.msg("resume_decisions_incomplete", lang))

    for key, state, line_of in (
            ("resume_decisions_alive", "alive", lambda i: i["display"]),
            ("resume_decisions_unknown", "unknown", lambda i: i["display"]),
            ("resume_decisions_dead", "dead",
             lambda i: "- **%s** — %s" % (i["id"], ", ".join(i["killed_by"])
                                          or messages.msg("resume_relation_unknown", lang))),
    ):
        group = [i for i in items if i["state"] == state]
        if group:
            parts.append(messages.msg(key, lang, count=len(group))
                         + "\n" + "\n".join(line_of(i) for i in group))
    return "\n\n".join(part for part in parts if part)


def resume_directives(project_name: str, topic: str, lang: str, *,
                      standing_block: str = "", active_constraints: str = "",
                      intent_block: str = "", exact_block: str = "",
                      recap_block: str = "", dialogue_block: str = "",
                      verification_block: str = "", decisions_block: str = "",
                      open_block: str = "", blockers_block: str = "",
                      detail_path: str = "",
                      saved_git: str = "unknown", state_relation: str = "unknown",
                      live_changed_paths: list[str] | None = None,
                      trust_markers: list[str] | None = None,
                      constraint_paths: list[str] | None = None,
                      work_id: str = "") -> str:
    """재개 시점에 CLI 가 직접 내는 지시문. `cmd_resume` 결과의 `resume_directives`.

    save 가 만들어 세션이 중계하던 것을 여기로 옮겼다 — 중계 지점이 없으므로 줄어들 수 없고,
    저장본이 아니라 실행 시점의 문구가 나가므로 문구 개정이 옛 저장본에도 소급된다.

    v6은 이 문자열 자체를 전달 채널로 쓴다. 따라서 본문을 다시 요약하거나 자연어로
    해석하지 않고, 절 전문과 기계 관측값을 정해진 블록에 배치한다.

    **마지막 블록은 복명이다.** 뒤에 블록을 더 붙이면 「여기서 멈추고 지시를 기다린다」가
    마지막 줄이 아니게 되고, 재개가 다음 행동으로 미끄러진다.
    """
    live_changed_paths = live_changed_paths or []
    trust_markers = trust_markers or []
    constraint_paths = constraint_paths or []
    def source(label: str, value: str) -> str:
        if not value.strip():
            return ""
        return f"[{label}]\n{_defuse_block_markers(value.strip())}"

    authority = "\n\n".join(part for part in (
        source(messages.msg("resume_source_standing", lang), standing_block),
        source(messages.msg("resume_source_constraints", lang), active_constraints),
        source(messages.msg("resume_source_scope", lang), intent_block),
        source(messages.msg("resume_source_exact", lang), exact_block),
        # 「본문에서 읽어라」로 시키면 도구 호출이 붙고 결국 grep 으로 훑힌다.
        # 판정에 쓰는 절은 지시하지 않고 **적재**한다 — 같은 글자를 어차피 읽고
        # 있었으므로 총량은 그대로고 왕복만 사라진다(실측: 재개 1회 11~13회).
        source(messages.msg("resume_source_decisions", lang), decisions_block),
    ) if part) or messages.msg("resume_none_recorded", lang)
    # 규율을 「그대로 따른다」 블록에 실으면 재개 자체가 방아쇠가 된다(실측: 도구 46회).
    authority += "\n" + messages.msg("resume_standing_scope", lang)
    history = "\n\n".join(part for part in (
        source(messages.msg("resume_source_recap", lang), recap_block),
        source(messages.msg("resume_source_dialogue", lang), dialogue_block),
    ) if part) or messages.msg("resume_none_recorded", lang)
    changed = ", ".join(live_changed_paths) if live_changed_paths else messages.msg("resume_none", lang)
    observation = "\n".join([
        messages.msg("resume_git_observation", lang, saved_git=saved_git,
                     state_relation=state_relation),
        messages.msg("resume_changed_paths", lang, paths=changed),
        source(messages.msg("resume_source_verification", lang), verification_block)
        or messages.msg("resume_verification_unknown", lang),
        source(messages.msg("resume_source_open", lang), open_block),
        source(messages.msg("resume_source_blockers", lang), blockers_block),
        *trust_markers,
    ])
    lines = [
        messages.msg("resume_block_scope", lang),
        messages.msg("resume_scope_guard", lang, project_name=project_name, topic=topic),
        # 저장하는 세션이 적어 둔 작업 식별자를 **그대로** 실어 나른다. 재개는 이 값을
        # 다시 판단하지 않는다 — 토픽에서 유추하면 틀린 값이 매 기록에 박힌다.
        (messages.msg("resume_work_id", lang, work_id=work_id) if work_id
         else messages.msg("resume_work_id_unknown", lang)),
        "",
        messages.msg("resume_block_authority", lang),
        authority,
        "",
        messages.msg("resume_block_history", lang),
        history,
        messages.msg("resume_history_caution", lang),
        "",
        messages.msg("resume_block_observation", lang),
        observation,
        messages.msg("resume_observation_caution", lang),
        "",
        messages.msg("resume_block_read", lang),
        messages.msg("resume_read_instruction", lang, detail_path=detail_path,
                     constraint_paths=(", ".join(constraint_paths)
                                       if constraint_paths else messages.msg("resume_none", lang))),
        "",
        # 복명이 **마지막 블록**이다. 뒤에 블록을 더 붙이면 「여기서 멈추고 지시를
        # 기다린다」가 마지막 줄이 아니게 되고, 그 자리에 있던 로그 언어 안내가
        # 재개의 끝맺음을 덮었다. 옛 판(v6)도 복명 문단으로 끝났고 그래서 재개가
        # 다음 행동으로 미끄러지지 않았다 — 로그 언어는 복명 슬롯 꼬리로 옮겼다.
        messages.msg("resume_block_ack", lang),
        messages.msg("resume_ack_slots", lang),
    ]
    return "\n".join(lines)


def _save_report(topic: str, status: str, project_name: str, detail_path: str,
                 resume_prompt: str, warnings: list[str], lang: str) -> str:
    """사용자에게 보여줄 완성 보고. 어댑터는 이 문자열을 그대로 출력한다(자유서술 금지)."""
    lines = [
        messages.msg("save_report_title", lang, topic=topic, status=status),
        messages.msg("save_report_project", lang, project_name=project_name),
        messages.msg("save_report_detail", lang, detail_path=detail_path),
        "",
        messages.msg("save_report_next", lang),
        "",
        "```text",
        resume_prompt,
        "```",
    ]
    if warnings:
        lines += ["", messages.msg("warnings_header", lang)]
        lines += [f"- {w}" for w in warnings]
    return "\n".join(lines)


def _conflict_report(topic: str, project_name: str, detail_path: str, other: str | None,
                     warnings: list[str], lang: str) -> str:
    """동시 저장 충돌 보고. 신규 본문은 보존됐고 포인터 갱신만 중단된 상태."""
    other_label = other or messages.msg("conflict_none", lang)
    lines = [
        messages.msg("conflict_title", lang, topic=topic),
        messages.msg("conflict_project", lang, project_name=project_name),
        messages.msg("conflict_new_body", lang, detail_path=detail_path),
        messages.msg("conflict_existing_latest", lang, other=other_label),
        "",
        messages.msg("conflict_tail", lang),
    ]
    if warnings:
        lines += ["", messages.msg("warnings_header", lang)]
        lines += [f"- {w}" for w in warnings]
    return "\n".join(lines)


def _save_transcript_path(payload: dict, cwd: str):
    """저장 payload 로부터 트랜스크립트 경로를 얻는다. 없으면 None (조용히)."""
    session_id = payload.get("session_id")
    if not session_id:
        return None
    try:
        return transcript_mod.derive_transcript_path(
            session_id, cwd, payload.get("transcript"))
    except transcript_mod.TranscriptNotFound:
        return None


def _measured_writer_model(payload: dict, cwd: str) -> str | None:
    """저작 모델을 트랜스크립트에서 실측한다. 대장을 위해 이미 읽는 파일이라 공짜다."""
    path = _save_transcript_path(payload, cwd)
    if path is None:
        return None
    return transcript_mod.measure_writer_model(
        path, payload.get("transcript_format", "claude"))


def _save_manifest(payload: dict, cwd: str) -> list[dict]:
    """저장 시점에 **CLI 가 직접** 발화 대장을 다시 뽑는다. 없으면 빈 목록.

    어댑터가 개수를 신고하게 두지 않는 이유는 하나다 — 신고는 이 프로젝트에서 세 번 깨졌다
    (금지어 우회 3회 · Haiku 의 허위 개수 · 변곡점 7개). 대장도 밀도도 코어가 센다.

    `session_id` 가 없으면(옛 어댑터·런타임 미제공) 대장 검사와 밀도 줄을 **건너뛴다** —
    저장을 막지는 않는다. 전수 보증이 없다는 사실은 frontmatter 의 `writer_session: null` 이
    그대로 드러낸다.
    """
    path = _save_transcript_path(payload, cwd)
    if path is None:
        return []
    return transcript_mod.extract_utterances(
        path,
        transcript_mod.parse_ts(payload.get("covers_from")),
        fmt=payload.get("transcript_format", "claude"),
    )


def _merge_ledger(manifest: list[dict], disposals: list) -> list[dict]:
    """대장(코어가 뽑은 발화) + 처분(어댑터가 넘긴 배열) → 렌더·검사용 행 목록.

    **지문은 코어가 넣는다.** 어댑터가 지문을 다시 적게 하면 옮겨 적다 바뀌고, 그러면
    「원문 그대로」가 깨진 걸 아무도 모른다. 어댑터가 넘기는 것은 `uid`·`section`·`note` 뿐이다.

    순서는 **대장 순서**(=시간순)를 따른다. 어댑터가 넘긴 순서를 쓰면 정렬이 어댑터 손에 간다.
    """
    by_uid = {}
    for row in disposals:
        if isinstance(row, dict) and row.get("uid"):
            by_uid[str(row["uid"]).strip()] = row
    out = []
    for entry in manifest:
        placed = by_uid.get(entry["uid"], {})
        out.append({
            "uid": entry["uid"],
            "kind": entry["kind"],
            "excerpt": transcript_mod.excerpt(entry["text"]),
            # 공백을 접은 길이. `없음` 처분에 이유가 필요한지 가르는 값이라 검사에 쓴다.
            "length": len(" ".join(entry["text"].split())),
            "section": str(placed.get("section") or "").strip(),
            "note": str(placed.get("note") or "").strip(),
        })
    return out


def _check_ledger(manifest: list[dict], ledger: list[dict], sections: dict) -> list[dict]:
    """처분 대장을 **센다.** 의미는 판정하지 않는다.

    구조화 배열로 받으므로 파싱이 없다 — 마크다운을 정규식으로 훑던 검사가 형식이 바뀔 때마다
    조용히 안 돌던 사고를 두 번 냈다(표 전용 검사가 목록에서 무력, 앵커 수로 뭉침을 잡으려다 헛짚음).

    검사 넷:
    - `ledger_uid_missing` — 처분되지 않은 UID
    - `ledger_bad_destination` — 목적지가 절 이름도 `없음` 도 아니다
    - `ledger_empty_target` — 지목한 절이 실제로 비어 있다(가리키기만 하고 안 썼다)
    - `ledger_note_missing` — 절에 담았다면서 무엇이 남았는지 안 적었다

    **`없음` 은 거부하지 않는다.** 비율만 밀도 줄에 박아 보이게 한다 — 거부하면 우회를 유도한다.
    """
    problems: list[dict] = []
    valid = set(detail.LEDGER_DESTINATIONS)
    key_of = {name: key for key, name in _SECTION_HEADINGS.items()}

    for row in ledger:
        uid, section = row["uid"], row["section"]
        if not section:
            problems.append({"code": "ledger_uid_missing", "uid": uid})
            continue
        if section == detail.LEDGER_NONE:
            # **`없음` 은 전부 이유를 적는다. 길이 면제는 없다.**
            #
            # 처음엔 길이 문턱(20자 → 10자)을 뒀다. **전제가 틀렸다** — 사용자 지적:
            # *"가장 큰 변곡점이 일어나는 자리가 보통 내가 「멈춰.」 할때거든. 2글자야."*
            # 짧을수록 덜 중요하다는 가정이 **가장 중요한 부류를 정확히 면제**하고 있었다.
            # `멈춰.`·`아니`·`취소` 가 전부 문턱 아래다.
            #
            # 그래서 문턱을 없앤다. 검사가 하나 줄고 미승인 상수도 사라진다 —
            # 조항을 키운 게 아니라 **없애서** 고친 것이다.
            if not row["note"]:
                problems.append({"code": "ledger_none_reason_missing", "uid": uid,
                                 "found": f"{row['length']}자"})
            continue
        if section not in valid:
            problems.append({"code": "ledger_bad_destination", "uid": uid, "found": section})
            continue
        if not (sections.get(key_of[section]) or "").strip():
            problems.append({"code": "ledger_empty_target", "uid": uid, "found": section})
        if not row["note"]:
            problems.append({"code": "ledger_note_missing", "uid": uid, "found": section})
    return problems


def _is_placeholder(block: str, message_key: str) -> bool:
    """그 절이 기본 placeholder 그대로인가. 언어를 모를 수 있어 전 언어와 대조한다."""
    text = block.strip()
    return any(text == messages.msg(message_key, lang).strip()
               for lang in messages.SUPPORTED_LANGS)


def _chain_bodies(root: str, include_archived: bool = False):
    """**LATEST 체인에 이어진 본문만** 낸다. (topic, archived, path) 튜플.

    동시 저장에서 진 저장본은 파일이 남지만 `LATEST.md` 에 못 들어간다. 그 고아 본문까지
    색인이 훑으면 **서로 다른 두 결정이 같은 ID 를 갖게 되어 색인이 돌이킬 수 없이 꼬인다**
    (외부 리뷰 R8-004). 체인만 따라가면 진 쪽이 자연히 빠진다.

    한 세션을 여러 번 저장한 **정상 중복은 그대로 남는다** — 그건 체인 안에 있다.
    합치지 않는다는 규칙(판단 배제)은 바뀌지 않는다.
    """
    for tdir, topic, archived in detail.iter_topic_dirs(root, include_archived):
        target = detail.read_latest_target(tdir)
        seen: set[str] = set()
        while target and target not in seen:
            seen.add(target)
            path = tdir / target
            if not path.exists():
                break
            yield topic, archived, path
            try:
                front, _ = detail.parse_frontmatter(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError):
                break
            prev = front.get("prev")
            target = prev if prev and prev != "null" else None


def _chain_walk(root: str, include_archived: bool = False):
    """LATEST 사슬을 **한 번 읽고**, 읽은 본문과 멈춘 이유를 함께 낸다.

    반환하는 ``bodies`` 는 ``(topic, archived, path, front, body)``다. 판독 뒤 파일을
    다시 읽지 않으므로 topology와 표시·생사가 서로 다른 바이트에서 만들어지지 않는다.

    포인터 없는 레거시 본문형 ``LATEST.md``는 그 파일 자체를 본문으로 읽는다. 반면 별도
    본문 판본이 있는데 포인터를 해석할 수 없으면 도달해야 할 체인 머리가 끊긴 것이다.
    동시 저장 패자(포인터가 정상인 상태의 고아 파일)는 계속 의도적으로 제외한다.
    """
    bodies: list[tuple] = []
    # `incomplete_topics` 는 파생이 아니라 **판독자가 아는 사실**이다. 소비자가
    # 문자열 `"<topic>/<file>"` 을 되파싱하면 토픽명 규칙에 묶인다.
    topology = {"broken_links": [], "unreadable": [], "cycles": [],
                "incomplete_topics": set()}
    for tdir, topic, archived in detail.iter_topic_dirs(root, include_archived):
        latest_path = tdir / "LATEST.md"
        if not latest_path.exists():
            # `.handoff/` 아래의 증거·판본 디렉터리는 토픽이 아니다.
            continue
        try:
            latest_text = latest_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            topology["unreadable"].append(f"{topic}/LATEST.md")
            topology["incomplete_topics"].add(topic)
            continue

        target = detail.parse_latest_target(latest_text)
        seen: set[str] = set()
        if target is None:
            # 포인터 없는 LATEST는 지원되는 레거시 본문형이다. 다만 별도 판본이 이미
            # 존재하면 cmd_resume의 orphan 경고와 같은 상태 — 체인 머리를 증명 못 한다.
            if any(detail._BODY_FILE_RE.match(path.name)
                   for path in tdir.glob("*.md")):
                topology["broken_links"].append(f"{topic}/LATEST.md")
                topology["incomplete_topics"].add(topic)
            front, body = detail.parse_frontmatter(latest_text)
            bodies.append((topic, archived, latest_path, front, body))
            seen.add("LATEST.md")
            prev = front.get("prev")
            target = prev if prev and prev != "null" else None

        while target:
            if target in seen:
                topology["cycles"].append(f"{topic}/{target}")
                topology["incomplete_topics"].add(topic)
                break
            seen.add(target)
            path = tdir / target
            if not path.exists():
                # 사슬이 가리키는데 없다 — 그 앞 판이 전부 도달 불가다.
                topology["broken_links"].append(f"{topic}/{target}")
                topology["incomplete_topics"].add(topic)
                break
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                topology["unreadable"].append(f"{topic}/{target}")
                topology["incomplete_topics"].add(topic)
                break
            front, body = detail.parse_frontmatter(text)
            bodies.append((topic, archived, path, front, body))
            prev = front.get("prev")
            target = prev if prev and prev != "null" else None
    return bodies, topology


def _decision_ledger_snapshot(root, include_archived: bool = False) -> dict:
    """**결정 대장의 무결성 인식 스냅샷.** 같은 읽기에서 두 보기를 함께 낸다.

    - `rows` — 전체 이력 보기(중복 제거 없음). `cmd_decisions` 가 쓴다
    - `resume` — 토픽별 현재판·생사·공백 투영. 재개가 그대로 쓴다
    - `topology` — 왜 사슬이 멈췄는가
    - `complete` — 위 셋이 전부 온전한가

    **두 소비자가 각자 사슬을 걷지 않는다.** 앞 판은 재개가 사슬을 직접 걷고 색인을 따로
    불러 ID 로 접합했는데, 그러면 「최신판·생사·완전성이 바뀌면 어디를 고치나」의 답이
    둘이 된다(sol 지적). 여기가 유일한 주소다.
    """
    bodies, topology = _chain_walk(root, include_archived)
    rows: list[dict] = []
    latest: dict[str, dict] = {}
    order: list[str] = []

    for topic, archived, path, front, body in bodies:
        created = front.get("created") or ""
        # 사고도 관계 토큰을 쓴다(기획 §7-bis) — 재발은 `RETRIES`, 해결은 `RESOLVES`.
        for heading in ("Decisions", "Unapproved Proposals", "Incidents"):
            block = detail.extract_section_block(body, heading)
            if not block:
                continue
            parser = (detail.parse_incident_records
                      if heading == "Incidents" else detail.parse_decisions)
            for parsed in parser(block):
                item = {key: parsed[key]
                        for key in ("id", "text", "owner", "relations")}
                rows.append({"created": created, "topic": topic, "archived": archived,
                             "section": heading, "source": _rel(root, path), **item})
        # 재개용 표시 내용 — 체인은 최신순이므로 첫 등장이 최신판이다.
        for did, rendered in _split_rendered_items(
                detail.extract_section_block(body, "Decisions")):
            if did and did not in latest:
                latest[did] = {
                    "topic": topic,
                    # 인용 원문은 여기서 빠진다. 소비자가 Markdown 을 다시 파싱하지 않는다.
                    "display": "\n".join(line for line in rendered.splitlines()
                                          if not line.lstrip().startswith(">")),
                }
                order.append(did)

    rows.sort(key=lambda r: (r["created"], r["id"]))

    # 역방향 파생: 누가 나를 가리켰나. 가리킨 쪽의 `created` 가 있어야 **시간순**으로
    # 최종 상태를 정할 수 있다.
    incoming: dict[str, list[dict]] = {}
    for row in rows:
        for rel in row["relations"]:
            incoming.setdefault(rel["target"], []).append(
                {"token": rel["token"], "from": row["id"], "created": row["created"],
                 **({"note": rel["note"]} if rel.get("note") else {})})

    terminal = ("RESOLVES",) + detail.RELATION_KILLS
    for row in rows:
        row["incoming"] = sorted(incoming.get(row["id"], []),
                                 key=lambda r: (r["created"], r["from"]))
        row["resolved_by"] = [r["from"] for r in row["incoming"] if r["token"] == "RESOLVES"]

        # **가장 나중의 종결 관계가 이긴다.** 우선순위를 고정하면 둘 다 틀린다 —
        # `RETRIES → RESOLVES` 면 resolved 가 맞고, `RESOLVES → ABANDONS` 면 dead 가 맞다.
        last = None
        for rel in row["incoming"]:
            if rel["token"] in terminal:
                last = rel["token"]
        row["state"] = ("resolved" if last == "RESOLVES"
                        else "dead" if last else "alive")

    by_id = {row["id"]: row for row in rows}
    complete = not (topology["broken_links"] or topology["unreadable"]
                    or topology["cycles"])

    # 대장이 보증한 값 → 재개가 쓰는 세 값. 모르는 값은 `unknown` 으로 보존한다.
    # 「dead가 아니므로 alive」 같은 부정 판정은 두지 않는다.
    liveness = {"alive": "alive", "resolved": "alive", "dead": "dead"}
    items_by_topic: dict[str, list[dict]] = {}
    for index, did in enumerate(order):
        current = latest[did]
        row = by_id.get(did)
        items_by_topic.setdefault(current["topic"], []).append({
            "id": did,
            "display": current["display"],
            "state": liveness.get((row or {}).get("state"), "unknown"),
            "killed_by": [f"{rel['token']} by {rel['from']}"
                          for rel in (row or {}).get("incoming", [])
                          if rel["token"] in detail.RELATION_KILLS],
            "order": index,
        })

    resume: dict[str, dict] = {}
    for topic, items in items_by_topic.items():
        nums = [int(match.group(1)) for match in
                (re.search(r"-D(\d+)$", item["id"]) for item in items) if match]
        span = None
        if nums:
            present = set(nums)
            span = {"first": min(nums), "last": max(nums),
                    "gaps": [n for n in range(1, max(nums) + 1)
                             if n not in present]}
        # **전역 `complete` 를 그대로 쓰지 않는다.** 다른 토픽이 깨졌다고 이 토픽의
        # 결정을 「확인되지 않은 항목」으로 낮춰 읽게 하면 거짓 경고다(로컬 리뷰 실측).
        resume[topic] = {"items": items, "span": span,
                         "complete": topic not in topology["incomplete_topics"]}

    return {"rows": rows, "resume": resume, "topology": topology,
            "complete": complete}


def _known_decision_ids(root: str) -> set[str]:
    """프로젝트에 이미 존재하는 결정·교훈·사고 ID. 참조 검증(P-05)의 사전이다."""
    out: set[str] = set()
    # **재개·색인과 같은 판독자를 쓴다.** 옛 판독자는 포인터 없는 레거시 본문형
    # `LATEST.md` 를 못 읽어 사전이 비었고, 그러면 저장 게이트가 **실재하는 관계
    # target 을 미지 ID 로 판정**했다(로컬 리뷰 실측). 본문도 이미 읽혀 온다.
    bodies, _topology = _chain_walk(root, include_archived=True)
    for _topic, _archived, _path, _front, body in bodies:
        for heading in ("Decisions", "Unapproved Proposals", "Lessons",
                        "Standing Directives", "Incidents"):
            block = detail.extract_section_block(body, heading)
            parser = (detail.parse_incident_records
                      if heading == "Incidents" else detail.parse_decisions)
            for item in parser(block):
                out.add(item["id"])
    return out


_LESSON_SLOTS = ("언제", "무엇", "왜", "증거", "대신")
_LESSON_HEAD_RE = re.compile(r"^###\s+(\S+)", re.MULTILINE)

#: 사고 대장 칸 (기획 §7-bis). 「잡은 것」은 값까지 검사한다.
_INCIDENT_SLOTS = ("증상", "원인", "수명", "잡은 것", "처방")
_INCIDENT_CATCHER_RE = re.compile(r"\*\*잡은 것\*\*\s*[:：]\s*([^\n]+)")


def _split_decisions(payload: dict, project: str, topic: str, manifest: list[dict]):
    """구조화 결정 배열 → (사용자 결정, chair 제안, 인용표, 위반).

    **주체는 파생이지 입력이 아니다.** `source` 에 사용자 UID 가 있으면 `user`, 없으면
    `chair` 다. 모델이 `누가:` 를 쓸 필드가 없으므로 **주체 오기가 구조적으로 불가능**해진다
    (3회 재발한 결함의 절반이 여기였다 — chair 가 뒤집은 것을 `누가: 사용자` 로 귀속시켰다).

    반환 인용표는 UID → 원문. 모델이 옮겨 적지 않고 CLI 가 대장에서 그대로 넣는다.
    """
    raw = payload.get("decisions")
    if not isinstance(raw, list):
        return None, None, {}, []

    # 인용은 **대장의 지문(120자 절단)이 아니라 원문 전체**여야 한다 — 잘린 인용은
    # 권위 노릇을 못 한다. 그래서 `manifest`(원문 보유)를 쓴다.
    manifest_by_uid = {r["uid"]: r for r in manifest}
    human_uids = transcript_mod.human_utterance_uids(manifest)
    by_uid = manifest_by_uid
    user_rows, chair_rows, problems = [], [], []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        entry = dict(entry)
        entry["id"] = detail.normalize_decision_id(
            str(entry.get("id") or ""), project, topic)
        uids = [str(u).strip() for u in (entry.get("source") or []) if str(u).strip()]
        entry["source"] = uids
        # 관계 대상도 완전형으로. 축약(`D1`)을 허용해놓고 검사만 완전형을 요구하면
        # 어댑터가 허용된 표기를 썼다는 이유로 거부된다.
        entry["relations"] = [
            {**r, "target": detail.normalize_decision_id(
                str(r.get("target") or ""), project, topic)}
            for r in (entry.get("relations") or []) if isinstance(r, dict)]
        for uid in uids:
            if uid not in human_uids:
                problems.append({"code": "decision_source_unknown",
                                 "uid": entry["id"], "found": uid})
        (user_rows if uids else chair_rows).append(entry)

    # 인용표: UID → 발화 원문. **모델이 옮겨 적지 않는다** — 옮겨 적다 바뀌면
    # 「원문 그대로」가 깨진 걸 아무도 모른다.
    quotes = {r["uid"]: r["text"] for r in manifest_by_uid.values()}
    return user_rows, chair_rows, quotes, problems


def _check_decision_ledger_link(user_rows: list, ledger: list[dict],
                                section: str = "Decisions",
                                code: str = "decision_ledger_mismatch") -> list[dict]:
    """**대장과 결정이 서로를 가리키는가.** 한쪽만 고치는 것을 불가능하게 만든다.

    지금까지 대장의 `note` 는 *"madi-r48f-D4 원문"* 이라고 **적혀만 있고 아무도 대조하지
    않았다.** 그래서 대장은 통과하는데 결정 본문이 반대로 쓰이는 일이 생겼다.
    """
    placed = {r["uid"] for r in ledger if r["section"] == section}
    cited = {u for e in user_rows for u in e["source"]}
    problems = []
    for uid in sorted(placed - cited):
        problems.append({"code": code, "uid": uid,
                         "found": f"대장은 {section} 로 처분했는데 인용한 항목이 없다"})
    for uid in sorted(cited - placed):
        problems.append({"code": code, "uid": uid,
                         "found": f"항목이 인용했는데 대장 처분이 {section} 가 아니다"})
    return problems


def _split_standing(payload: dict, project: str, topic: str,
                    manifest: list[dict]):
    """구조화 상시 규율 배열 → (렌더용 행, 위반). Decisions 파이프(D-8)의 재사용이다.

    상시 규율은 **사용자가 건 것만** 성립한다 — `source` UID 가 없으면 chair 가 스스로
    규율을 만든 것이므로 위반이다(결정과 달리 「미승인 상시 규율」이라는 개념 자체가 모순:
    다음 세션을 구속하는 힘이 사용자 발화에서 나온다).
    """
    raw = payload.get("standing")
    if not isinstance(raw, list):
        return None, []
    human_uids = transcript_mod.human_utterance_uids(manifest)
    rows, problems = [], []
    seen_ids: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        entry = dict(entry)
        entry["id"] = detail.normalize_decision_id(
            str(entry.get("id") or ""), project, topic)
        uids = [str(u).strip() for u in (entry.get("source") or []) if str(u).strip()]
        entry["source"] = uids
        entry["relations"] = [
            {**r, "target": detail.normalize_decision_id(
                str(r.get("target") or ""), project, topic)}
            for r in (entry.get("relations") or []) if isinstance(r, dict)]
        if not uids:
            problems.append({"code": "standing_source_missing", "uid": entry["id"],
                             "found": "상시 규율에 사용자 발화 출처가 없다"})
        for uid in uids:
            if uid not in human_uids:
                # **결정과 다른 코드를 쓴다.** 규율의 미확인 출처는 오염 집합에 들어가
                # 승계·주입을 막아야 하는데, `decision_source_unknown` 을 쓰면 그 판정에
                # 안 걸려 출처가 존재하지 않는 규율이 다음 세션의 지시로 나갔다(외부 리뷰).
                problems.append({"code": "standing_source_unknown",
                                 "uid": entry["id"], "found": uid})
        # ID 는 승계·폐기가 겨누는 **유일한 손잡이**다. 형식이 어긋나거나 겹치면 어느
        # 항목을 죽이라는 것인지 정해지지 않는다 — 저장 전에 막는다.
        if not re.fullmatch(r"\S+-S\d+", entry["id"]):
            problems.append({"code": "standing_id_malformed", "uid": entry["id"],
                             "found": "상시 규율 ID 는 -S<번호> 로 끝나야 한다"})
        elif entry["id"] in seen_ids:
            problems.append({"code": "standing_id_duplicate", "uid": entry["id"],
                             "found": "같은 payload 안에 같은 ID 가 둘"})
        seen_ids.add(entry["id"])
        rows.append(entry)
    return rows, problems


def _split_rendered_items(block: str) -> list[tuple[str, str]]:
    """렌더된 절에서 최상위 목록 항목을 (id, 항목 전문) 으로 쪼갠다. 승계용."""
    items: list[tuple[str, str]] = []
    current_id, current_lines = None, []
    for line in (block or "").splitlines():
        if line.startswith("- **"):
            if current_id is not None:
                items.append((current_id, "\n".join(current_lines)))
            match = detail._DECISION_ID_RE.match(line)
            current_id = match.group(1) if match else ""
            current_lines = [line]
        elif current_id is not None:
            current_lines.append(line)
    if current_id is not None:
        items.append((current_id, "\n".join(current_lines)))
    return items


#: 「이 본문의 상시 규율을 믿을 수 없다」를 뜻하는 위반들. **승계 실패 표식은 넣지 않는다** —
#: `standing_carry_*` 는 이 본문의 규율이 나쁘다는 게 아니라 *직전* 본문을 못 읽었다는 기록이다.
#: 접두 문자열(`"standing_" in ...`)로 판정했더니 그 표식이 자기 자신을 다시 트리거해
#: **한 번 강등되면 이후 모든 저장이 영원히 막혔다**(실측 4판까지). 집합으로 못박는다.
_STANDING_TAINT_CODES = frozenset({
    "standing_source_missing",
    "standing_source_unknown",
    "standing_id_malformed",
    "standing_id_duplicate",
    "standing_ledger_mismatch",
})


def _standing_demoted(front: dict) -> bool:
    """직전 저장본의 **상시 규율 자체가** 강등 사유였나.

    강등 전체를 배척하지 않는다 — 무관한 이유(교훈 형식 등)로 강등된 저장본의 규율까지
    버리면 정상 규율이 사라진다.
    """
    if str(front.get("schema_demoted", "")).strip().lower() != "true":
        return False
    recorded = {entry.split(":", 1)[0].strip()
                for entry in str(front.get("schema_problems") or "").split(",")}
    return bool(recorded & _STANDING_TAINT_CODES)


def _carry_standing(tdir, prev: str | None, new_ids: set[str],
                    killed: set[str]) -> tuple[list[str], list[dict]]:
    """직전 저장본의 `Standing Directives` 항목을 **CLI 가 자동 승계**한다.

    자동압축 분석(2026-08-18)에서 가져온 원칙이다 — 압축 프롬프트 3종이 전부 보안 지시를
    「축자 그대로, 압축 후에도 효력이 살게」 보존한다. 여기서는 보안에 한정하지 않고
    사용자가 강조한 규칙 전반으로 넓혔다. 승계는 어댑터가 아니라 CLI 가 한다 — 어댑터에게
    맡기면 옮겨 적다 빠뜨리는 그 사고(발화 대장이 막은 것)가 규율에서 재발한다.

    빠지는 것 둘뿐: ⓐ 이번 저장이 같은 ID 를 다시 선언(새 판이 이긴다)
    ⓑ 이번 저장의 관계 토큰이 죽였다(REVERSES/SUPERSEDES/ABANDONS/RETRIES).

    **직전 본문을 못 읽으면 조용히 빈 목록을 내지 않는다.** 그러면 관계 토큰 없이 규율
    전체가 새 정본에서 사라지고, 위 두 조건이 통째로 우회된다(외부 리뷰 지적). 소리 나게
    막고 사용자가 판단하게 한다.
    """
    if not prev:
        return [], []
    prev_path = tdir / prev
    try:
        prev_body = prev_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        # 디코딩 실패도 **읽기 실패다.** `OSError` 만 잡으면 깨진 바이트를 만난 저장이
        # 승계 실패 결과 대신 `cmd_save` 전체를 예외로 끝낸다(외부 리뷰 지적).
        return [], [{"code": "standing_carry_failed", "uid": prev,
                     "found": "직전 본문을 읽지 못해 상시 규율 승계를 보증할 수 없다"}]
    front, _ = detail.parse_frontmatter(prev_body)
    block = detail.extract_section_block(prev_body, "Standing Directives")
    items = _split_rendered_items(block)
    if _standing_demoted(front):
        # **복구 경로가 있어야 한다.** 오염된 항목을 이번 판이 전부 유효하게 재선언했으면
        # 승계할 것이 없으므로 막을 이유도 없다. 이 검사가 `new_ids` 보다 먼저 반환하던
        # 탓에, 오류 메시지가 요구하는 「재선언」이 정상 경로에서 불가능했다(기획 §7.3).
        leftover = [i for i, _ in items if i and i not in new_ids and i not in killed]
        if leftover:
            return [], [{"code": "standing_carry_demoted", "uid": prev,
                         "found": f"직전 저장본의 상시 규율이 강등 상태다 — "
                                  f"재선언해야 승계된다: {', '.join(leftover)}"}]
        return [], []
    carried = []
    for item_id, text in items:
        if item_id and item_id not in new_ids and item_id not in killed:
            carried.append(text)
    return carried, []


def _check_decisions(sections: dict, project: str, topic: str,
                     known_ids: set[str]) -> list[dict]:
    """P-05·P-06 — 관계 토큰이 닫힌 집합인가, 참조한 ID 가 실존하는가.

    **이 검사가 저장 경로에 안 물려 있었다.** 파서 수준에서만 확인하고 `cmd_save` 에
    연결하지 않아, 닫힌 집합 밖 토큰은 오류가 아니라 `relations: []` 로 **조용히 사라졌고**
    없는 ID 참조도 그대로 저장됐다(외부 리뷰가 재현).
    """
    problems: list[dict] = []
    seen = set(known_ids)
    parsed = []
    for key in ("decisions", "unapproved", "standing"):
        block = sections.get(key) or ""
        for item in detail.parse_decisions(block):
            parsed.append((key, item))
            seen.add(item["id"])

    for key, item in parsed:
        # P-06: 완전형인가. 축약(`D1`)은 CLI 가 정규화하므로 여기 오면 이미 완전형이다.
        if not detail.is_full_decision_id(item["id"]):
            problems.append({"code": "decision_id_not_normalized", "uid": item["id"],
                             "found": key})
        for rel in item["relations"]:
            target = rel["target"]
            if target in seen:
                continue
            # 형태가 ID 가 아니면 프로젝트와 무관하게 거부한다 — 이건 확인이 아니라 문법이다.
            if not detail.is_full_decision_id(target):
                problems.append({"code": "relation_target_malformed", "uid": item["id"],
                                 "found": f"{rel['token']}: {target}"})
                continue
            # **다른 프로젝트 ID 는 거부하지 않는다** — 경험이 프로젝트를 넘어 흐르는 것이
            # ID 에 프로젝트를 넣은 이유인데, 여기서 막으면 그 인용이 통째로 불가능해진다.
            # 이 프로젝트 소속인데 없는 것만 거부한다(확인할 수 있는 범위).
            if target.startswith(f"{project}-"):
                problems.append({"code": "relation_target_missing", "uid": item["id"],
                                 "found": f"{rel['token']}: {target}"})

    # 닫힌 집합 밖 토큰: 파서가 관계로 안 잡으므로 **원문에서** 따로 센다.
    for key in ("decisions", "unapproved", "standing"):
        for token in re.findall(r"\b([A-Z]{4,})\s*[:：]\s*[A-Za-z0-9_.\-]+",
                                sections.get(key) or ""):
            if token not in detail.RELATION_TOKENS:
                problems.append({"code": "relation_token_unknown", "uid": key,
                                 "found": token})
    return problems


def _check_slotted_block(sections: dict, key: str, slots: tuple,
                         head_code: str, slot_code: str) -> tuple[list[dict], list[str]]:
    """`### <ID> — 제목` + 칸 목록 형식을 검사한다. `Lessons`·`Incidents` 공용.

    **같은 기계를 공유한다.** 사고 대장을 넣으면서 검사를 새로 쓰면 조항이 둘이 되고,
    한쪽만 고치는 사고가 생긴다 — 이 프로젝트가 이미 겪은 형태다(표 전용 검사가 목록에서
    안 돌던 일). 항목 본문 목록도 함께 돌려주어 호출자가 값 검사를 얹을 수 있게 한다.
    """
    block = (sections.get(key) or "").strip()
    if not block or _is_placeholder(block, f"{key}_default"):
        return [], []
    heads = list(_LESSON_HEAD_RE.finditer(block))
    if not heads:
        return [{"code": head_code, "uid": "", "found": "### <ID> — 제목"}], []
    problems: list[dict] = []
    bodies: list[str] = []
    for i, head in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(block)
        body = block[head.start():end]
        bodies.append(body)
        missing = [s for s in slots if f"**{s}**" not in body]
        if missing:
            problems.append({"code": slot_code, "uid": head.group(1),
                             "found": ", ".join(missing)})
    return problems, bodies


def _check_lessons(sections: dict) -> list[dict]:
    """P-08 — 교훈은 다섯 칸을 다 채운다. **0건은 통과**(배운 게 없는 세션이 정상이다)."""
    problems, _ = _check_slotted_block(sections, "lessons", _LESSON_SLOTS,
                                       "lesson_head_missing", "lesson_slot_missing")
    return problems


def _check_incidents(sections: dict) -> list[dict]:
    """사고는 다섯 칸을 다 채우고 **「잡은 것」은 닫힌 집합**이어야 한다 (기획 §7-bis).

    닫힌 집합인 이유는 관계 토큰과 같다 — 자유 서술이면 셀 수 없고, 세지 못하면
    「검증 절차가 실제로 무엇을 잡나」에 답할 수 없다. 그 분포가 이 대장의 존재 이유다.

    **0건도 통과시킨다.** 「사고 없음」이 거짓일 가능성은 게이트가 아니라 절 문구가
    경고한다(`0건은 의심 신호다`) — 개수를 강제하면 지어내게 된다.
    """
    problems, bodies = _check_slotted_block(sections, "incidents", _INCIDENT_SLOTS,
                                            "incident_head_missing",
                                            "incident_slot_missing")
    for body in bodies:
        match = _INCIDENT_CATCHER_RE.search(body)
        if match and match.group(1).strip() not in detail.INCIDENT_CATCHERS:
            head = _LESSON_HEAD_RE.search(body)
            problems.append({"code": "incident_catcher_unknown",
                             "uid": head.group(1) if head else "",
                             "found": match.group(1).strip()})
    return problems


def _check_incident_relations(sections: dict, project: str,
                              known_ids: set[str]) -> list[dict]:
    """사고의 관계는 **유효한 사고 헤딩**에서만 생산해 검사한다.

    사고 본문은 다섯 칸의 산문이라 절 전체 정규식으로 훑으면 `NOTE:` 같은 정상 설명을
    관계 토큰으로 오인한다. 반대로 목록형 가짜 ID를 `seen`에 넣으면 없는 대상도 실재처럼
    보인다. 관계는 표준 `### <ID> … (TOKEN: target)` 헤딩의 첫 항목에서만 읽고, 대상은
    실제 사고 헤딩과 기존 색인으로만 확인한다.
    """
    items = detail.parse_incident_records(sections.get("incidents") or "")
    problems: list[dict] = []
    seen = set(known_ids) | {item["id"] for item in items}
    for item in items:
        for rel in item["relations"]:
            target = rel["target"]
            if target in seen:
                continue
            if not detail.is_full_decision_id(target):
                problems.append({"code": "relation_target_malformed", "uid": item["id"],
                                 "found": f"{rel['token']}: {target}"})
            elif target.startswith(f"{project}-"):
                problems.append({"code": "relation_target_missing", "uid": item["id"],
                                 "found": f"{rel['token']}: {target}"})

        for token in item["_unknown_relation_tokens"]:
            problems.append({"code": "relation_token_unknown", "uid": item["id"],
                             "found": token})
    return problems


def _density_line(ledger: list[dict], lang: str) -> str:
    """`없음` 비율을 CLI 가 계산해 박는다. 직전 `유지` 비율 69% 가 비교 기준이다."""
    total = len(ledger)
    none_count = sum(1 for r in ledger if r["section"] == detail.LEDGER_NONE)
    placed = sum(1 for r in ledger
                 if r["section"] and r["section"] != detail.LEDGER_NONE)
    pct = round(none_count * 100 / total) if total else 0
    return messages.msg("ledger_density", lang, total=total, placed=placed,
                        none_count=none_count, none_pct=pct)


_ACTIVE_CONSTRAINTS = "ACTIVE-CONSTRAINTS.md"


def _active_constraints_path(root: str) -> Path:
    return Path(root) / ".handoff" / _ACTIVE_CONSTRAINTS


def _active_constraints_skeleton(project_name: str, lang: str) -> str:
    """프로젝트 전역 제약의 단일 append-only 목록 골격."""
    return messages.msg("active_constraints_skeleton", lang, project_name=project_name)


def _ensure_active_constraints(root: str, project_name: str, lang: str) -> Path:
    """파일이 없을 때만 만든다. 기존 파일은 어떤 절도 재생성하지 않는다."""
    path = _active_constraints_path(root)
    if not path.exists():
        atomicio.atomic_write_text(str(path), _active_constraints_skeleton(project_name, lang))
    return path


def _active_constraint_entries(entries: object, body: str) -> tuple[list[dict], bool]:
    """기존 handoff 산출물을 전역 제약으로 **명시적으로** 승격한 항목만 받는다.

    ``text``와 ``source``는 이미 조립된 본문에 실제로 있어야 한다. CLI는 제약 문장을
    새로 만들거나 Incident/RETRIES를 자동 승격하지 않는다. ``source``는 기존 UID/ID이고
    append 중복 방지에만 쓴다.
    """
    if entries is None:
        return [], True
    if not isinstance(entries, list):
        return [], False
    out: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict):
            return [], False
        text = detail.sanitize_line(entry.get("text"))
        source = detail.sanitize_line(entry.get("source")).replace("`", "")
        document = detail.sanitize_line(entry.get("document")).replace("`", "")
        if not text or not source or text not in body or source not in body:
            return [], False
        out.append({"text": text, "source": source, "document": document})
    return out, True


def _append_active_constraints(path: Path, entries: list[dict],
                               handoff_pointer: str) -> bool:
    """단일 Markdown 목록 끝에만 기존 handoff 원문을 append한다.

    기존 파일의 문장·주석·개행을 다시 렌더하거나 해석하지 않는다. 같은 source 표식은
    같은 저장을 다시 append하지 않게 하는 좁은 기계 경계다.
    """
    if not entries:
        return True
    try:
        raw = path.read_bytes()
        existing = raw.decode("utf-8")
    except (OSError, UnicodeError):
        return False
    additions: list[str] = []
    seen_sources: set[str] = set()
    for entry in entries:
        source_marker = f"  - 출처: `{entry['source']}`"
        if source_marker in existing or entry["source"] in seen_sources:
            continue
        seen_sources.add(entry["source"])
        line = f"- {entry['text']}\n{source_marker} · `{handoff_pointer}`"
        if entry["document"]:
            line += f" · 문서: `{entry['document']}`"
        additions.append(line)
    if not additions:
        return True
    newline = b"" if not raw or raw.endswith((b"\n", b"\r")) else b"\n"
    updated = raw + newline + ("\n\n".join(additions) + "\n").encode("utf-8")
    atomicio.atomic_write_bytes(str(path), updated)
    return True


def _frontmatter_list(values: object) -> list[str]:
    """frontmatter JSON 배열로 남길 구조 입력. 새 산문 파서는 만들지 않는다."""
    if not isinstance(values, list):
        return []
    return [detail.sanitize_line(value) for value in values if detail.sanitize_line(value)]


def cmd_save(payload: dict, cwd: str, global_root: str | None = None) -> dict:
    """상세 정본 저장 → 글로벌 CURRENT.md best-effort 재생성."""
    warnings: list[str] = []
    lang = messages.resolve_lang(payload.get("lang"))

    root = repo.resolve_root(cwd, payload.get("root"))
    name = repo.project_name(root)
    topic = topics.normalize_topic(payload["topic"])
    source = _validate_source(payload.get("source"), warnings, lang)
    global_root = global_root or _default_global_root(source)
    summary = (payload.get("summary") or "").strip() or topic
    status_val = _normalize_save_status(payload.get("status"))
    if not (payload.get("summary") or "").strip():
        warnings.append(messages.msg("warn_summary_missing", lang))

    project_id = repo.ensure_project_id(root)
    if repo.project_id_uncommitted(root):
        warnings.append(messages.msg("warn_project_id_uncommitted", lang))

    git = repo.git_meta(root)
    now = repo.now_local()
    created_iso = repo.iso8601(now)
    created_human = now.strftime("%Y-%m-%d %H:%M")

    tdir = detail.topic_dir(root, topic)
    prev = detail.read_latest_target(tdir)
    latest_path = tdir / "LATEST.md"
    snapshot = latest_path.read_bytes() if latest_path.exists() else None

    orphan = detail.detect_orphan(tdir, prev, lang)
    if orphan:
        warnings.append(orphan)

    cross_project = _cross_project_files(root, payload.get("files_touched", []))
    if cross_project:
        examples = cross_project[:5]
        warnings.append(messages.msg(
            "warn_cross_project_files", lang,
            count=len(cross_project), paths=", ".join(examples),
        ))

    meta = {
        "topic": topic,
        "created": created_iso,
        "project_root": root,
        "status": status_val,
        "prev": prev,
        "source": source,
        # 저장 언어를 기록한다. resume 이 지시문을 재개 시점에 생성하므로(R7), 이게 없으면
        # 재개 머신의 env/OS 로케일이 언어를 다시 정해 **저장 언어와 갈린다** — en 으로 저장한
        # 핸드오프를 한국어 환경에서 재개하면 프롬프트는 영어인데 지시는 한국어가 됐다(실측).
        "lang": lang,
        # 저작 조건(R8). **payload 가 정본, env 는 폴백이다** — 코어가 특정 호스트의
        # 변수 이름을 알면 벤더가 늘 때마다 코어가 바뀐다. 어댑터가 자기 호스트 값을
        # 이 이름으로 번역해 넘기고, 코어는 이 이름만 안다(승인 5: 표면층이 걸러서 넘긴다).
        # **실측이 정본, 신고가 폴백이다.** 순서를 뒤집으면 안 된다 — E4 에서 신고값
        # `claude-opus-5` 가 실제 저작자 `claude-sonnet-5` 와 달랐다(트랜스크립트 실측).
        "writer_model": (_measured_writer_model(payload, cwd)
                         or payload.get("writer_model") or payload.get("model")),
        "writer_effort": payload.get("writer_effort") or os.environ.get("HANDOFF_EFFORT"),
        "writer_session": payload.get("session_id") or os.environ.get("HANDOFF_SESSION_ID"),
        # 조직이 이 작업을 부르는 이름(라운드·티켓·에픽). **저장하는 세션이 적는다** —
        # 그 세션은 사용자와 대화하며 확정한 값을 들고 있다. 재개는 이 값을 새로
        # 판단하지 않고 실린 것을 옮기기만 한다.
        #
        # **토픽에서 유추하지 않는다.** 토픽은 파일 축이고 한 작업이 여러 토픽에
        # 흩어진다 — 실측으로 한 라운드가 토픽 셋, 다른 라운드는 열여덟 개였고
        # 작업이 아닌 토픽도 섞여 있었다. 유추하면 틀린 값이 매 기록에 박힌다.
        # `sanitize_line` 이지 `sanitize_frontmatter_value` 가 아니다. 후자는 `code:uid,
        # code:uid` 로 직렬화되는 `schema_problems` 용이라 `:`·`,` 를 지우는데, 이 파서는
        # **첫 콜론만** 나누므로(`detail.parse_frontmatter`) 값 안의 콜론은 무해하다.
        # 지우면 `R48f:H1` 이 `R48f H1` 이 되어 「그대로 실어 나른다」가 깨진다.
        "work_id": detail.sanitize_line(payload.get("work_id") or "") or None,
        "covers_from": payload.get("covers_from"),
        "summary": summary,
        "git": git,
    }
    sections = _normalize_sections(payload.get("sections", {}), warnings, lang)

    # ── 스키마 게이트 (R8) ──────────────────────────────────────────────
    # **디스크에 쓰기 전에** 판정한다. 거부된 payload 가 LATEST 를 이미 건드린 뒤면
    # 재시도·강등 경로 전체가 오염된다.
    #
    # 흐름: 위반 → 거부(ok=false) → 어댑터가 해당 항목만 쪼개 **1회** 재제출 →
    #       그래도 위반이면 `force_schema: true` 로 **강등 저장**(위반이 frontmatter 에 박힌다).
    #
    # 재시도가 1회인 이유: 검사가 위반을 **한꺼번에 전부** 돌려주므로 한 번에 다 고칠 수 있다.
    # 1회가 실패했다면 같은 루프의 2회가 다르게 나올 근거가 없고, 80~95% 컨텍스트에서
    # 전 절 재발화는 회당 수천~수만 토큰이라 창만 태운다.
    # P-06: 축약 ID 를 완전형으로. **검사보다 먼저** 한다 — 정규화 전에 검사하면
    # 어댑터가 허용된 축약을 썼다는 이유로 거부된다.
    for key in ("decisions", "unapproved", "lessons", "incidents"):
        if sections.get(key):
            sections[key] = detail.normalize_decision_ids_in_block(
                sections[key], name, topic)

    manifest = _save_manifest(payload, cwd)
    ledger = _merge_ledger(manifest, payload.get("utterance_ledger") or [])

    # ── 결정: 인용이 권위, 해석은 비권위 (D-8) ──────────────────────────
    # 모델은 UID 만 가리킨다. 인용문·주체·절 배치는 CLI 가 만든다.
    user_rows, chair_rows, quotes, dec_problems = _split_decisions(
        payload, name, topic, manifest)
    if user_rows is not None:
        sections["decisions"] = detail.render_decisions(user_rows, quotes, lang)
        sections["unapproved"] = detail.render_decisions(chair_rows, quotes, lang)
        if manifest:
            dec_problems += _check_decision_ledger_link(user_rows, ledger)

    # ── 상시 규율: 인용(권위) + CLI 자동 승계 (압축의 「축자 보존」 이식) ──
    quotes_all = {r["uid"]: r["text"] for r in manifest}
    standing_rows, standing_problems = _split_standing(payload, name, topic, manifest)
    standing_rows = standing_rows or []
    # **폐기 권한은 사용자 출처가 있는 항목에만 있다.** `chair_rows` 를 넣으면 미승인
    # 제안 하나가 사용자 규율을 지울 수 있다(외부 리뷰 재현: `source: []` + `REVERSES: S1`
    # 로 사용자 규율이 사라졌다). 규율의 구속력이 사용자 발화에서 나오므로 해제도 같아야 한다.
    # 출처 UID 가 **매니페스트에 실재하는 사람 발화**여야 한다. 가짜 UID와 하네스
    # 래퍼는 대장에 남아도 `system`으로 분류돼 공용 사람 UID 집합에 들어가지 않는다.
    # 새 규율을 거는 `_split_standing`도 같은 집합을 쓴다.
    human_uids = transcript_mod.human_utterance_uids(manifest)
    authorized = [e for e in standing_rows + (user_rows or [])
                  if e.get("source") and set(e["source"]) <= human_uids]
    killed = {r["target"] for e in authorized
              for r in (e.get("relations") or [])
              if r.get("token") in detail.RELATION_KILLS}
    carried, carry_problems = _carry_standing(
        tdir, prev, {e["id"] for e in standing_rows}, killed)
    standing_problems += carry_problems
    # **어댑터가 넘긴 산문을 그대로 싣지 않는다.** 이 절은 항상 구조화 입력에서 렌더한다 —
    # `sections.standing` 에 마크다운을 직접 넣어 출처 검사(`standing_source_missing`)와
    # 대장 대조를 통째로 우회하고 resume 까지 주입되던 경로를 막는다(외부 리뷰 재현).
    standing_parts = carried + ([detail.render_decisions(standing_rows, quotes_all, lang)]
                                if standing_rows else [])
    sections["standing"] = "\n".join(standing_parts)
    if manifest:
        standing_problems += _check_decision_ledger_link(
            standing_rows, ledger, section="Standing Directives",
            code="standing_ledger_mismatch")
    dec_problems += standing_problems

    # 다음 행동의 근거 발화 — 지시가 원문으로 있어야 손이 바로 움직인다(5요건 ②).
    next_src = [str(u).strip() for u in (payload.get("next_step_source") or [])
                if str(u).strip()]
    # v6 writer contract: authority UID와 대상 경로는 본문 산문을 재파싱하지 않아도 되는
    # frontmatter 구조로 함께 남긴다. 경로는 저장·resume이 해석하지 않고 활성화 계층이
    # `live_changed_paths`와 대조할 수 있는 기계 사실이다.
    meta["exact_source_uids"] = _frontmatter_list(next_src)
    meta["exact_target_paths"] = _frontmatter_list(payload.get("next_step_targets"))
    meta["body_contract"] = 1
    quote_lines = []
    for uid in next_src:
        if uid not in quotes_all:
            dec_problems.append({"code": "decision_source_unknown",
                                 "uid": "exact_next_step", "found": uid})
            continue
        # **권위는 사람 발화에서만 나온다.** 결정·상시 규율은 이미 `human_uids` 로
        # 거르는데 Exact 만 `quotes_all` 만 봤다 — 하네스 레코드(`<command-name>` 같은
        # `kind: system` 줄)의 UID 를 대면 인용이 붙고 미승인 표시는 안 붙었다.
        # 사람이 시키지 않은 행동이 사용자 근거가 있는 것처럼 보였다(외부 리뷰 실측).
        if uid not in human_uids:
            dec_problems.append({"code": "exact_source_not_human",
                                 "uid": "exact_next_step", "found": uid})
            continue
        quote_lines += [f"> {line}".rstrip() for line in quotes_all[uid].splitlines()]
    if quote_lines:
        sections["exact_next_step"] = (
            (sections.get("exact_next_step") or "").rstrip()
            + "\n\n" + "\n".join(quote_lines)).strip()
    elif (sections.get("exact_next_step") or "").strip():
        # **출처가 없으면 chair 가 정한 것이다.** 결정은 이 경우 자동으로
        # `Unapproved Proposals` 로 가고 「출처: chair(미승인)」이 박히며, 상시 규율은
        # 아예 거부된다. 그런데 Exact 만 표시 없이 통과했다 — 그래서 chair 가 스스로
        # 정한 다음 행동이 근거 원문 없이 명령형으로 실렸고, 세션이 저장 직후 그것을
        # 실행했다(2026-08-22 madi r75e 실측: 벤더 한 판이 그대로 나갔다).
        #
        # 거부하지는 않는다 — 다음 행동을 지시받지 않은 세션이 흔하고 chair 추정은
        # 쓸모가 있다. 다만 **사용자 지시로 위장되면 안 된다.** 재개는 이 절을 본문에서
        # 그대로 읽어 블록 2 에 실으므로, 여기 한 줄이 재개까지 따라간다.
        sections["exact_next_step"] = (
            messages.msg("exact_unapproved_marker", lang) + "\n\n"
            + sections["exact_next_step"].strip())
    # 결정·규율과 **같은 규칙**을 적용한다. 이게 없으면 대장은 「다음 행동으로 담았다」고
    # 하는데 정작 근거 원문이 없는 저장이 통과하고, 반대로 다른 절로 처분한 UID 를
    # 인용해도 통과했다(외부 리뷰 재현).
    if manifest:
        dec_problems += _check_decision_ledger_link(
            [{"source": next_src}], ledger, section="Exact Next Step",
            code="next_step_ledger_mismatch")

    schema_problems = _check_ledger(manifest, ledger, sections) if manifest else []
    schema_problems += dec_problems
    # P-05·P-06·P-08 — 기획이 정해둔 게이트 셋. **이게 안 물려 있었다**(외부 리뷰 R8-001).
    # 대장 유무와 무관하게 돈다: 세션 식별자가 없어도 결정·교훈 형식은 검사할 수 있다.
    known_ids = _known_decision_ids(root)
    schema_problems += _check_decisions(sections, name, topic, known_ids)
    schema_problems += _check_lessons(sections)
    schema_problems += _check_incidents(sections)
    schema_problems += _check_incident_relations(sections, name, known_ids)

    # 트랜스크립트에 파싱 불가능한 줄이 있으면 **대장이 불완전할 수 있다.** 그대로 두면
    # 「모든 UID 를 처분했다」가 100% 로 나와 보증이 거짓이 된다 — 소리 나게 막는다.
    tpath = _save_transcript_path(payload, cwd)
    # 대화 꼬리(Recent Dialogue)는 페이로드 입력이 없다 — 전부 CLI 실측.
    dialogue = (transcript_mod.extract_dialogue_tail(
                    tpath, payload.get("transcript_format", "claude"))
                if tpath is not None else [])
    if tpath is not None:
        broken = transcript_mod.count_malformed(
            tpath, payload.get("transcript_format", "claude"))
        if broken:
            schema_problems.append({"code": "transcript_corrupt", "uid": "",
                                    "found": f"{broken}줄"})

    if schema_problems and not payload.get("force_schema"):
        out = _result("save", root, name, project_id,
                      warnings + [messages.msg("warn_schema_rejected", lang,
                                               count=len(schema_problems))],
                      {"saved": False, "schema_problems": schema_problems,
                       "retry_hint": "빠진 UID 를 처분하고 목적지를 절 이름으로 고쳐 1회 "
                                     "재제출. 그래도 걸리면 force_schema: true 로 강등 저장."})
        out["ok"] = False
        return out
    if schema_problems:
        meta["schema_demoted"] = True
        # 기획 §8 — frontmatter 한 줄에 실리므로 구분자·개행을 뺀다. uid 는 어댑터가
        # 통제하는 값이라, 콤마를 심으면 **가짜 오염 코드**가 되고 개행을 심으면
        # **`schema_demoted: true` 를 덮는 새 줄**이 된다(둘 다 실측).
        meta["schema_problems"] = ", ".join(
            f"{detail.sanitize_frontmatter_value(p['code'])}:"
            f"{detail.sanitize_frontmatter_value(p.get('uid'))}"
            for p in schema_problems)
        warnings.append(messages.msg("warn_schema_demoted", lang,
                                     count=len(schema_problems)))

    body = detail.assemble_body(meta, sections, payload.get("files_touched", []),
                               created_human, lang, ledger=ledger, dialogue=dialogue)
    if manifest:
        # 밀도 줄은 **CLI 가** 계산해 대장 머리에 박는다 — 어댑터가 쓰면 자기 신고다.
        body = body.replace("## Utterance Ledger\n\n",
                            "## Utterance Ledger\n\n"
                            + _density_line(ledger, lang) + "\n\n", 1)

    existing = {p.name for p in tdir.glob("*.md")} if tdir.exists() else set()
    detail_path = None
    for _ in range(5):  # 동시 충돌 시 새 UUID 로 재시도.
        filename = detail.detail_filename(now, existing)
        try:
            detail_path = detail.write_detail(tdir, filename, body)
            break
        except FileExistsError:
            existing.add(filename)
    if detail_path is None:
        raise FileExistsError("파일명 충돌이 반복됨 — 저장 중단.")

    # 동시성: 본문 저장 사이 LATEST 가 바뀌었으면 포인터 갱신 중단 (test 10).
    #
    # **전역 제약 append 는 이 판정 뒤에 온다.** 앞에 두면 경쟁에서 진 저장본의 제약도
    # 파일에 남는데, `_chain_bodies()` 는 LATEST 에 안 이어진 그 본문을 의도적으로
    # 제외한다 — 제약만 살아남아 존재하지 않는 체인의 경로를 가리키며 모든 재개의
    # 블록 2 에 주입된다. 두 권위 경계가 어긋나는 자리였다(외부 리뷰 2026-08-21).
    current_latest = latest_path.read_bytes() if latest_path.exists() else None
    if snapshot != current_latest:
        # 충돌 메시지는 _conflict_report 의 lead 문구가 전달하므로, report 의 ⚠경고 블록엔
        # 그 외 경고(source·project-id·orphan 등)만 넣는다(중복 방지). result.warnings 에는 포함.
        pre_conflict_warnings = list(warnings)
        warnings.append(messages.msg("warn_concurrent_save", lang))
        return _result("save", root, name, project_id, warnings, {
            "topic": topic,
            "status": status_val,
            "summary": summary,
            "detail_path": _rel(root, detail_path),
            "concurrent_conflict": True,
            "latest_target_other": prev,
            "git": git,
            "report": _conflict_report(topic, name, _rel(root, detail_path), prev,
                                       pre_conflict_warnings, lang),
        })

    detail.write_latest(tdir, filename, summary)
    detail.regenerate_index(root, lang)

    # 프로젝트 전역 제약 문서는 이 저장이 LATEST 경쟁에서 이긴 뒤에만 만진다. 없으면
    # 단일 목록 골격을 만들고, 명시적으로 지정된 **기존 본문 산출물**만 끝에 복사한다.
    constraints_path = _ensure_active_constraints(root, name, lang)
    constraint_entries, entries_valid = _active_constraint_entries(
        payload.get("active_constraint_entries"), body)
    if not entries_valid or not _append_active_constraints(
            constraints_path, constraint_entries, _rel(root, detail_path)):
        warnings.append(messages.msg("warn_active_constraints_append_failed", lang))

    # 글로벌 CURRENT.md — 분리 실패 경계: 실패해도 상세는 보존 (test 25).
    global_info: dict = {"written": False, "skipped_reason": None}
    try:
        cur_meta = current.build_meta(root, git, created_iso)
        recent_entry = f"- {created_iso[:16].replace('T', ' ')} · {source} · {topic}: {summary}"
        gresult = current.regenerate_current(
            global_root, name, project_id, root, cur_meta,
            recent_entry=recent_entry, add_writers=[source], lang=lang,
        )
        warnings.extend(gresult.warnings)
        global_info = {
            "written": gresult.written,
            "path": gresult.path,
            "mode": gresult.mode,
            "skipped_reason": gresult.skipped_reason,
        }
    except Exception as exc:  # noqa: BLE001 — 글로벌 실패는 상세를 롤백하지 않는다.
        warnings.append(messages.msg("warn_global_write_failed", lang, exc=exc))

    resume_prompt = _resume_prompt(name, root, topic, summary, lang)
    report = _save_report(topic, status_val, name, _rel(root, detail_path), resume_prompt,
                          warnings, lang)
    return _result("save", root, name, project_id, warnings, {
        "topic": topic,
        "status": status_val,
        "summary": summary,
        "detail_path": _rel(root, detail_path),
        "latest_path": _rel(root, latest_path),
        "concurrent_conflict": False,
        "resume_prompt": resume_prompt,
        "report": report,
        "git": git,
        "active_constraints_path": _rel(root, constraints_path),
        "global": global_info,
    })


def _normalize_save_status(raw: str | None) -> str:
    """신규 저장은 4-value 만 쓴다(미지정 시 active). 미인식은 active 로."""
    group, _ = status_mod.normalize_status(raw)
    return group


def _topic_source(root: str, summary) -> str | None:
    """토픽 LATEST 본문의 `source` 를 읽는다(reindex writers 도출용). 없으면 None."""
    tdir = detail.topic_dir(root, summary.topic)
    target = summary.latest_target
    if not target or not (tdir / target).exists():
        return None
    front, _ = detail.parse_frontmatter((tdir / target).read_text(encoding="utf-8"))
    src = front.get("source")
    return src if src in _VALID_SOURCES else None


def cmd_reindex(cwd: str, root: str | None = None, global_root: str | None = None,
                source: str | None = None) -> dict:
    """기존 active 토픽만 스캔해 글로벌 CURRENT.md 만 백필한다.

    새 detail·LATEST·INDEX 를 쓰지 않는다(정본 read-only). `.project-id` 가 없으면
    생성. 멱등(같은 입력 2회 → updated_at 외 바이트 동일). active 토픽이
    없거나 `.handoff/` 가 없으면 글로벌을 만들지 않고 사유를 반환한다.
    """
    warnings: list[str] = []
    lang = messages.resolve_lang(None)  # reindex 는 payload 가 없음 → env/locale 체인.
    writer = _validate_source(source, warnings, lang)  # None/빈값 → claude-code (cmd_save 와 동일 경로)
    global_root = global_root or _default_global_root(writer)
    resolved = repo.resolve_root(cwd, root)
    name = repo.project_name(resolved)

    base = Path(resolved) / ".handoff"
    if not base.is_dir():
        return _result("reindex", resolved, name, repo.read_project_id(resolved),
                       [messages.msg("warn_no_handoff_dir", lang)],
                       {"reindexed": False, "reason": "no .handoff", "active_topics": 0})

    topics_list = detail.scan_topics(resolved, include_archived=False, lang=lang)
    active = [t for t in topics_list if t.group in status_mod.ACTIVE_GROUPS]
    for summary in topics_list:
        if summary.warning:
            warnings.append(f"[{summary.topic}] {summary.warning}")
    if not active:
        return _result("reindex", resolved, name, repo.read_project_id(resolved),
                       warnings + [messages.msg("warn_no_active_topics", lang)],
                       {"reindexed": False, "reason": "no active topics", "active_topics": 0})

    project_id = repo.ensure_project_id(resolved)
    if repo.project_id_uncommitted(resolved):
        warnings.append(messages.msg("warn_project_id_uncommitted_reindex", lang))

    git = repo.git_meta(resolved)
    created_iso = repo.iso8601(repo.now_local())
    add_writers = sorted({s for t in active if (s := _topic_source(resolved, t))})

    global_info: dict = {"written": False}
    try:
        cur_meta = current.build_meta(resolved, git, created_iso)
        gresult = current.regenerate_current(
            global_root, name, project_id, resolved, cur_meta,
            recent_entry=None, add_writers=add_writers, lang=lang,
        )
        warnings.extend(gresult.warnings)
        global_info = {
            "written": gresult.written, "path": gresult.path,
            "mode": gresult.mode, "skipped_reason": gresult.skipped_reason,
        }
    except Exception as exc:  # noqa: BLE001 — 백필 실패는 정본을 건드리지 않는다.
        warnings.append(messages.msg("warn_reindex_failed", lang, exc=exc))

    return _result("reindex", resolved, name, project_id, warnings, {
        "reindexed": global_info.get("written", False),
        "global": global_info,
        "active_topics": len(active),
    })


def cmd_list(cwd: str, root: str | None = None, include_archived: bool = False) -> dict:
    resolved = repo.resolve_root(cwd, root)
    name = repo.project_name(resolved)
    lang = messages.resolve_lang(None)  # list 는 payload 가 없음 → env/locale 체인.
    topics_list = detail.scan_topics(resolved, include_archived=include_archived, lang=lang)
    items = [
        {
            "topic": t.topic, "status": t.group, "summary": t.summary,
            "date": t.date, "archived": t.archived, "warning": t.warning,
        }
        for t in topics_list
    ]
    return _result("list", resolved, name, repo.read_project_id(resolved), [], {"topics": items})


def cmd_find(cwd: str, keyword: str, root: str | None = None,
             global_roots: list[str] | None = None) -> dict:
    """프로젝트 로컬 검색. global_roots 가 주어지면 그 루트들을 read-only 검색."""
    resolved = repo.resolve_root(cwd, root)
    name = repo.project_name(resolved)
    matches: list[dict] = []
    needle = keyword.lower()

    if global_roots:
        # 글로벌: 각 스코프(예: ~/projects) 하위 트리에서 모든 `.handoff/` 를 찾아 검색.
        handoff_dirs: list[Path] = []
        for scope in global_roots:
            scope_path = Path(scope)
            if not scope_path.is_dir():
                continue
            direct = scope_path / ".handoff"
            if direct.is_dir():
                handoff_dirs.append(direct)
            for found in scope_path.rglob(".handoff"):
                if found.is_dir():
                    handoff_dirs.append(found)
        bases = list(dict.fromkeys(handoff_dirs))  # dedup, 순서 유지
    else:
        local = Path(resolved) / ".handoff"
        bases = [local] if local.is_dir() else []

    for base in bases:
        for path in base.rglob("*.md"):
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            if needle in text.lower():
                matches.append({"root": str(base.parent), "path": str(path)})
    return _result("find", resolved, name, repo.read_project_id(resolved), [],
                   {"keyword": keyword, "matches": matches, "read_only": True})


def _previous_save_boundary(root: str, topic: str) -> tuple[str | None, int, str | None]:
    """직전 저장본의 `created` · 마지막 변곡점 번호 · 그것을 쓴 세션(`writer_session`).

    한 세션에서 두 번 이상 저장할 때 「직전 핸드오프 이후」만 정리하려면 경계가 필요하다.
    그 경계는 **직전 저장본의 저장 시각**이라 사람 판단이 안 들어간다. 번호도 함께 돌려서
    이어지는 저장본이 1번부터 다시 세지 않게 한다(색인에서 번호가 충돌하면 못 읽는다).

    `writer_session` 을 같이 돌려주는 이유: **직전 저장이 같은 세션이면 중복이 아니라 델타다.**
    이 값이 없으면 어댑터가 「이미 LATEST 가 있다」만 보고 재저장을 거부한다 — 실제로
    그렇게 거부했다(2026-08-17 실측). 두 경우는 처분이 정반대인데 겉모습이 같다:

    | 직전 저장 | 뜻 | 처분 |
    |---|---|---|
    | `writer_session` 이 지금 세션과 **같다** | 같은 세션의 2회차 | **델타 저장**(진행) |
    | **다르다**(또는 없다) | 다른 세션의 체인 | 기존 동시성 경로 |
    """
    tdir = detail.topic_dir(root, topics.normalize_topic(topic))
    target = detail.read_latest_target(tdir)
    if not target or not (tdir / target).exists():
        return None, 0, None
    front, body = detail.parse_frontmatter((tdir / target).read_text(encoding="utf-8"))
    # 이어 매길 번호는 **결정 ID** 의 최대값이다(변곡점 번호였던 것을 대체).
    numbers = []
    for heading in ("Decisions", "Unapproved Proposals"):
        for item in detail.parse_decisions(detail.extract_section_block(body, heading)):
            tail = item["id"].rsplit("-", 1)[-1]
            if tail[:1] in ("D", "L") and tail[1:].isdigit():
                numbers.append(int(tail[1:]))
    last_number = max(numbers, default=0)

    def _val(key):
        v = front.get(key)
        return v if v and v != "null" else None

    return _val("created"), last_number, _val("writer_session")


def cmd_utterances(cwd: str, session_id: str, root: str | None = None,
                   transcript: str | None = None, topic: str | None = None,
                   since: str | None = None, fmt: str = "claude",
                   scope: str = "auto") -> dict:
    """사용자 발화 대장 — 어댑터가 **다른 절을 쓰기 전에** 받아 간다.

    어댑터는 이 대장의 모든 `uid` 를 한 번씩 처분해야 한다(`#n` 또는 `유지`). 그러면
    **빠뜨린 변곡점이 사라지는 대신 잘못된 라벨로 남는다** — 지금은 누락이 흔적을 안 남긴다.

    **U-ID 를 CLI 가 발급하는 것이 핵심이다.** 모델이 스스로 매기면 다시 자기 신고라
    증거가 안 된다.

    못 찾으면 `ok: false` 로 소리 나게 실패한다 — 빈 대장을 조용히 내면 「전수」가 거짓이 된다.

    `scope` 가 범위를 정한다. **사용자가 못박으면 기존 저장본이 있든 없든 그대로 실행한다** —
    「이거 중복 아닌가」를 모델이 추론하다 저장을 통째로 거부한 사고가 있었다(2026-08-17).

    | `scope` | 뜻 |
    |---|---|
    | `full` | 세션 전체. 직전 저장이 있어도 무시하고 번호를 1부터 다시 센다 |
    | `delta` | 직전 저장 이후만. **직전 저장이 다른 세션이어도** 강제한다 |
    | `auto` | 직전 저장이 같은 세션이면 델타, 아니면 전체(기본값) |

    `auto` 를 기본으로 둔 이유: 대부분은 한 세션 한 번 저장이고, 그때 `full` 을 매번 치게 하면
    플래그가 의례가 된다. 판단이 필요한 자리에서만 사용자가 못박는다.
    """
    resolved = repo.resolve_root(cwd, root)
    name = repo.project_name(resolved)
    try:
        path = transcript_mod.derive_transcript_path(session_id, cwd, transcript)
    except transcript_mod.TranscriptNotFound as exc:
        out = _result("utterances", resolved, name, repo.read_project_id(resolved),
                      [messages.msg("warn_transcript_not_found", messages.resolve_lang(None),
                                    session_id=session_id)],
                      {"found": False, "tried": exc.tried, "utterances": [], "count": 0})
        out["ok"] = False
        return out

    # 경계: 명시 `since` > 토픽의 직전 저장본 `created` > 없음(세션 전체).
    prev_created, prev_last_number, prev_session = (None, 0, None)
    if topic:
        prev_created, prev_last_number, prev_session = _previous_save_boundary(resolved, topic)
    # 같은 세션이 이미 이 토픽에 저장했다 = 중복이 아니라 **델타 2회차**다.
    same_session = bool(prev_session) and prev_session == session_id

    if scope not in ("auto", "delta", "full"):
        raise ValueError(f"알 수 없는 scope: {scope} (auto|delta|full)")
    # `auto` 만 추론한다. 사용자가 못박은 `delta`·`full` 은 직전 저장본의 존재·소유와 무관하게
    # 그대로 실행한다 — 그게 이 플래그의 존재 이유다.
    resolved_scope = scope if scope != "auto" else ("delta" if same_session else "full")

    if since:
        boundary = since          # 명시 시각이 가장 세다
    elif resolved_scope == "delta":
        boundary = prev_created   # 직전 저장이 없으면 None → 결과적으로 전체
    else:
        boundary = None
    # `full` 이면 번호를 1부터 다시 센다 — 이어 쓸 것이 없다.
    continue_from = prev_last_number if resolved_scope == "delta" else 0
    rows = transcript_mod.extract_utterances(
        path, transcript_mod.parse_ts(boundary), fmt=fmt)
    # 원문(`text`)은 **돌려주지 않는다.** 저장 시점의 세션은 이미 그 발화를 컨텍스트에
    # 들고 있으므로(핸드오프는 자동압축 전에 한다) 원문을 되돌려주는 것은 순수 중복이고,
    # 대장의 일은 「내용 공급」이 아니라 「전수 열거」다.
    #
    # 실측된 사고: 원문까지 실어 55KB(≈18k 토큰)를 반환했고, 컨텍스트 96% 세션에서 대장을
    # 부르자 **그 자리에서 자동압축이 돌았다** — 대장이 압축을 막으려고 만든 장치인데
    # 스스로 압축을 일으켰다. 원문을 빼면 같은 세션이 20KB 로 떨어진다.
    rows = [{"uid": r["uid"], "kind": r["kind"], "ts": r["ts"],
             "excerpt": transcript_mod.excerpt(r["text"])} for r in rows]
    user_rows = [r for r in rows if r["kind"] == "user"]
    return _result("utterances", resolved, name, repo.read_project_id(resolved), [], {
        "found": True,
        "transcript": str(path),
        "session_id": session_id,
        "format": fmt,
        # 델타 저장 지원: 이 대장이 어느 시점 이후인지, 번호를 몇 번부터 이어야 하는지.
        "since": boundary,
        "is_delta": bool(boundary),
        "continue_from": continue_from,
        # 어댑터가 「이미 저장돼 있으니 중복」이라고 스스로 거부하지 않도록, 판별을 값으로 준다.
        "prev_session": prev_session,
        "same_session_resave": same_session,
        # `auto` 가 무엇으로 갈렸는지, 사용자가 못박았는지를 그대로 드러낸다.
        "scope": resolved_scope,
        "scope_forced": scope != "auto",
        "utterances": rows,
        "count": len(rows),
        "user_count": len(user_rows),
    })


def cmd_decisions(cwd: str, root: str | None = None, decision_id: str | None = None,
                  include_archived: bool = False) -> dict:
    """결정 색인 — 모든 저장본의 `## Decisions`·`## Unapproved Proposals` 에서 ID 를 모은다.

    읽기 전용이다. 파일을 만들지 않고 인덱스를 갱신하지 않는다.

    **관계는 새 결정 쪽에 적히고 옛 문서는 안 건드린다**(소급 재작성 금지). 그래서 역방향은
    여기서 **파생**한다 — `madi-r75-D1` 을 조회하면 `[RESOLVED BY madi-r75e-D3]` 가 뜬다.
    `INDEX 는 파생` 원칙과 같다.

    `decision_id` 를 주면 그 결정의 **일생**만 낸다 — 언제 정해졌고, 무엇을 가리켰고,
    무엇이 그것을 뒤집었나.

    **중복 제거를 하지 않는다.** 한 세션을 여러 번 저장하면 같은 결정이 여러 본문에 나타나는데,
    합치려면 「같은 결정인가」를 판정해야 하고 그 판단이 끼는 순간 기준이 흔들린다.
    """
    resolved = repo.resolve_root(cwd, root)
    name = repo.project_name(resolved)
    snapshot = _decision_ledger_snapshot(resolved, include_archived)
    rows = snapshot["rows"]

    if decision_id:
        rows = [r for r in rows
                if r["id"] == decision_id
                or any(rel["target"] == decision_id for rel in r["relations"])]
    return _result("decisions", resolved, name, repo.read_project_id(resolved), [],
                   {"items": rows, "count": len(rows), "decision_id": decision_id,
                    "include_archived": include_archived, "read_only": True,
                    # 재개와 같은 무결성 포함 스냅샷의 증거다. 부분 행을 완전한
                    # 결정 색인으로 오해하지 않도록 소비자에게도 보존한다.
                    "complete": snapshot["complete"],
                    "topology": snapshot["topology"]})

#: frontmatter 만 보려고 파일 머리에서 읽는 바이트. 저장본은 100KB 를 넘기도 하는데
#: 프로젝트 하나에 수백 개가 쌓인다 — 전문을 읽으면 조회 한 번이 수십 MB 가 된다.
_FRONTMATTER_PEEK = 4096


def _front_value(front: dict, key: str) -> str | None:
    """frontmatter 값 하나. 문자열 `"null"` 은 **없음**이다.

    파서가 yaml 을 안 쓰고 `key: value` 를 그대로 읽으므로 `null` 이 문자열로 온다.
    이 변환을 빼먹으면 「없음」이 「값이 'null' 인 것」으로 읽혀 미상 안내가 안 뜬다.
    """
    value = (front.get(key) or "").strip()
    return value if value and value != "null" else None


def cmd_last_saved(cwd: str, session: str, root: str | None = None,
                   include_archived: bool = False) -> dict:
    """그 세션이 **마지막으로 저장한** 토픽 하나. 읽기 전용이다.

    **재개 기준이 아니라 저장 기준이다.** 이름을 그렇게 지은 이유가 있다 — 재개는
    아무 파일도 쓰지 않는 것이 계약이라 「누가 무엇을 재개했나」는 남는 자리가 없다.
    남는 것은 `save` 가 frontmatter 에 박는 `writer_session` 뿐이고, 이 명령은 그
    이미 있는 사실을 조회 표면 하나로 낼 뿐 새 상태를 만들지 않는다.

    소비자(madi R48f)는 훅에서 받은 세션 아이디로 「이 세션이 어느 라운드인가」를
    묻는다. **그 아이디는 런타임 축이어야 한다** — Claude Code 훅 payload 의
    `session_id`, 전사 파일명과 같은 값이다. cross-session 메시지 주소(`local_…`)는
    다른 축이라 그것으로 조회하면 전부 못 찾는다(실측으로 두 값이 완전히 다르다).

    못 찾으면 **오류가 아니라 `topic: None`** 이다. 아직 한 번도 저장하지 않은
    세션은 정상 상태이지 실패가 아니다.
    """
    resolved = repo.resolve_root(cwd, root)
    wanted = (session or "").strip()
    best: tuple[str, str, dict] | None = None       # (created, topic, meta)

    for tdir, topic, archived in detail.iter_topic_dirs(resolved, include_archived):
        for path in sorted(tdir.glob("*.md")):
            if not detail._BODY_FILE_RE.match(path.name):
                continue                            # LATEST.md 등 포인터는 본문이 아니다
            try:
                with path.open(encoding="utf-8") as handle:
                    head = handle.read(_FRONTMATTER_PEEK)
            except (OSError, UnicodeError):
                continue
            front, _body = detail.parse_frontmatter(head)
            if (front.get("writer_session") or "").strip() != wanted or not wanted:
                continue
            # 정본은 frontmatter 의 `created` 다. 다만 그 값은 **초 단위**라 한 세션이
            # 두 토픽을 잇달아 저장하면 같은 값이 나온다(시험이 실제로 잡았다). 그때는
            # 파일 mtime 을 보조로 쓴다 — 완벽하진 않지만(복사·이관에서 바뀐다) 초 안쪽을
            # 가를 유일한 사실이고, 못 가르는 채 앞의 것을 이기게 두는 것보다 낫다.
            created = (front.get("created") or path.name)
            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = 0.0
            if best is None or (created, mtime) > best[0]:
                best = ((created, mtime), topic,
                        {"archived": archived, "source": _rel(resolved, path),
                         "created": front.get("created") or None})

    name = repo.project_name(resolved)
    payload = ({"session": wanted, "topic": None, "found": False, "read_only": True}
               if best is None else
               {"session": wanted, "topic": best[1], "found": True,
                "read_only": True, **best[2]})
    return _result("last-saved", resolved, name,
                   repo.read_project_id(resolved), [], payload)


def cmd_negative(cwd: str, root: str | None = None,
                 include_archived: bool = False) -> dict:
    """부정 색인 — 실패·폐기만 모은다. **「이거 해봤나?」** 에 답한다.

    이 프로젝트가 이미 겪은 *"다음 세션이 같은 것을 다시 제안한다"* 를 직접 막는 것이 목적이다.

    모으는 곳 넷:
    - `## Failed Attempts` 전문
    - `## Not Tried Yet` 전문 (검토했으나 아직 안 한 것)
    - `## Incidents` 전문 — **「이거 전에도 났나」** 가 「이거 해봤나」와 같은 질문이다
    - 죽은 결정 — 관계 토큰 Ⓐ군(`REVERSES`·`SUPERSEDES`·`ABANDONS`)이 가리킨 것

    읽기 전용이다.
    """
    resolved = repo.resolve_root(cwd, root)
    name = repo.project_name(resolved)
    rows: list[dict] = []
    for topic, archived, path in _chain_bodies(resolved, include_archived):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        front, body = detail.parse_frontmatter(text)
        created = front.get("created") or ""
        base = {"created": created, "topic": topic, "archived": archived,
                "source": _rel(resolved, path)}
        for heading, key in (("Failed Attempts", "failed_attempts"),
                             ("Not Tried Yet", "not_tried"),
                             ("Incidents", "incidents")):
            block = detail.extract_section_block(body, heading)
            # 기본 placeholder(「특별히 막힌 시도 없음」)를 실패로 담으면 색인이 소음으로 찬다.
            # 저장 언어를 모를 수 있으므로 **모든 언어의 기본값**과 대조한다.
            # 절 주석(`>` 줄)은 CLI 가 넣은 안내라 내용이 아니다 — 벗기고 대조한다.
            # 안 벗기면 주석 있는 절(Incidents 등)의 기본값이 영영 placeholder 로 안 잡힌다.
            content = "\n".join(line for line in block.splitlines()
                                if not line.lstrip().startswith(">")).strip()
            if content and not _is_placeholder(content, f"{key}_default"):
                rows.append({**base, "kind": heading, "body": content})

    # `dead` 만 — `resolved` 는 달성된 목표라 부정 색인에 들어가면 정반대 답이 된다.
    # 관계 기반 부정 결과는 반드시 결정 색인에서 파생한다. 사고 원문 블록은 위에서
    # 가시성만 보존하며 ID나 관계를 다시 해석하지 않는다.
    killed = [r for r in cmd_decisions(cwd, root, None, include_archived)["items"]
              if r["state"] == "dead"]
    for row in killed:
        rows.append({"created": row["created"], "topic": row["topic"],
                     "archived": row["archived"], "source": row["source"],
                     "kind": "Killed Decision", "body": f"{row['id']} {row['text']}",
                     "killed_by": row["incoming"]})
    rows.sort(key=lambda r: (r["created"], r["kind"]))
    return _result("negative", resolved, name, repo.read_project_id(resolved), [],
                   {"items": rows, "count": len(rows),
                    "include_archived": include_archived, "read_only": True})


def _body_contract_generation(front: dict) -> str:
    """v6 세대 표지. 키 없음만 legacy이고 나머지 불명값은 추측하지 않는다."""
    if "body_contract" not in front:
        return "legacy"
    return "current" if str(front.get("body_contract")).strip() == "1" else "unsupported"


def _live_changed_paths(root: str, git: dict) -> list[str]:
    """`git status`가 보고한 경로 목록만 반환한다. 해시·fingerprint는 만들지 않는다."""
    if not git["is_git"]:
        return []
    raw = repo.run_git(root, "status", "--porcelain=v1", "-z").stdout
    parts = raw.split("\0")
    paths: list[str] = []
    i = 0
    while i < len(parts):
        entry = parts[i]
        if len(entry) >= 4:
            paths.append(entry[3:])
            # rename/copy는 두 경로를 모두 보존한다. status v1 -z의 두 번째 토큰은
            # 이전 경로이며, 대상 겹침 판단에 어느 쪽도 버리면 안 된다.
            if entry[:2].strip() in {"R", "C"} and i + 1 < len(parts) and parts[i + 1]:
                paths.append(parts[i + 1])
                i += 1
        i += 1
    return paths


def _live_relation(front: dict, git: dict) -> str:
    """v6 §4.3의 여섯 단계 우선순위. 비교 불능은 같음으로 추정하지 않는다."""
    if not git["is_git"]:
        return "not_git"
    saved_commit = front.get("git_commit")
    saved_branch = front.get("git_branch")
    saved_dirty = str(front.get("git_dirty") or "").lower()
    if not saved_commit or saved_commit == "null" or not saved_branch or saved_branch == "null" \
            or saved_dirty not in {"true", "false"}:
        return "unknown"
    current_commit, current_branch = git.get("commit"), git.get("branch")
    if not current_commit or not current_branch:
        return "unknown"
    commit_same = current_commit.startswith(saved_commit) or saved_commit.startswith(current_commit)
    if not commit_same or saved_branch != current_branch:
        return "diverged"
    current_dirty = "true" if git.get("dirty") else "false"
    if saved_dirty != current_dirty:
        return "diverged"
    return "uncertain" if current_dirty == "true" else "same"


def _constraint_paths(constraints: str) -> list[str]:
    """CLI가 append한 ``문서: `path``` 표식만 읽기 목록으로 올린다."""
    return re.findall(r"(?m)^  - 출처: .*? · 문서: `([^`\r\n]+)`\s*$", constraints)


def cmd_resume(cwd: str, topic: str, root: str | None = None) -> dict:
    resolved = repo.resolve_root(cwd, root)
    name = repo.project_name(resolved)
    topic = topics.normalize_topic(topic)
    tdir = detail.topic_dir(resolved, topic)
    warnings: list[str] = []
    lang = messages.resolve_lang(None)  # resume 은 payload 가 없음 → env/locale 체인.

    target = detail.read_latest_target(tdir)
    if target is None and not (tdir / "LATEST.md").exists():
        return _result("resume", resolved, name, repo.read_project_id(resolved),
                       [messages.msg("warn_no_handoff_for_topic", lang, topic=topic)],
                       {"found": False})

    if target and not (tdir / target).exists():
        warnings.append(messages.msg("warn_broken_handoff", lang, target=target))
        return _result("resume", resolved, name, repo.read_project_id(resolved),
                       warnings, {"found": True, "broken": True})

    orphan = detail.detect_orphan(tdir, target, lang)
    if orphan:
        warnings.append(orphan)

    body_path = (tdir / target) if target else (tdir / "LATEST.md")
    text = body_path.read_text(encoding="utf-8")
    front, body = detail.parse_frontmatter(text)

    # 지시문 언어는 **저장 언어**를 따른다. R7 로 지시문이 재개 시점에 생성되면서, 이걸
    # 안 하면 재개 머신의 env/OS 로케일이 언어를 다시 정해 저장 언어와 갈린다(실측:
    # en 저장 → ko 환경 재개 시 프롬프트는 영어, 지시는 한국어). 옛 파일엔 lang 이 없으므로
    # 그때만 언어체인으로 폴백한다.
    saved_lang = front.get("lang")
    if saved_lang and saved_lang != "null":
        lang = messages.resolve_lang(saved_lang)

    # git_drift는 기존 소비자를 위해 그대로 유지한다. v6의 세부 관계는 별도 필드다.
    drift = None
    git = repo.git_meta(resolved)
    if git["is_git"] and front.get("git_commit") and front["git_commit"] != "null":
        saved_commit = front.get("git_commit") or ""
        cur_commit = git["commit"] or ""
        # 라이브 레거시는 short SHA 를 기록했다 — prefix 일치면 같은 커밋으로 본다
        # (그렇지 않으면 short vs full 이 항상 drift 로 오발).
        commit_same = bool(saved_commit) and bool(cur_commit) and (
            cur_commit.startswith(saved_commit) or saved_commit.startswith(cur_commit)
        )
        if not commit_same or front.get("git_branch") != git["branch"]:
            drift = {
                "saved_branch": front.get("git_branch"),
                "saved_commit": front.get("git_commit"),
                "saved_dirty": front.get("git_dirty"),
                "current_branch": git["branch"],
                "current_commit": git["commit"],
            }
            warnings.append(messages.msg("warn_git_drift", lang))

    format_generation = _body_contract_generation(front)
    live_relation = _live_relation(front, git)
    live_changed_paths = _live_changed_paths(resolved, git)
    unknown_fields = (["exact_source_uids", "exact_target_paths", "current_scope",
                       "open_status", "blocker_release_evidence", "verification_as_of"]
                      if format_generation == "legacy" else [])

    trust_reasons: list[str] = []
    if str(front.get("schema_demoted", "")).lower() == "true":
        trust_reasons.append("schema_demoted")
    if not front.get("writer_session") or front.get("writer_session") == "null":
        trust_reasons.append("utterance_ledger_absent")
    if format_generation == "unsupported":
        trust_reasons.append("body_contract_unsupported")
    if orphan:
        trust_reasons.append("orphan_or_stale_latest")
    trust_markers: list[str] = []
    if trust_reasons:
        trust_markers.append(messages.msg("resume_incomplete_marker", lang,
                                          reasons=", ".join(trust_reasons)))
    if live_relation != "same":
        changed = ", ".join(live_changed_paths) if live_changed_paths else messages.msg("resume_none", lang)
        saved_git = f"{front.get('git_branch')} / {front.get('git_commit')}"
        trust_markers.append(messages.msg("resume_code_moved_marker", lang,
                                          relation=live_relation, paths=changed,
                                          saved_git=saved_git))

    prev_chain = []
    prev = front.get("prev")
    hops = 0
    while prev and prev != "null" and hops < 2 and (tdir / prev).exists():
        prev_chain.append(prev)
        ptext = (tdir / prev).read_text(encoding="utf-8")
        pfront, _ = detail.parse_frontmatter(ptext)
        prev = pfront.get("prev")
        hops += 1

    constraints_path = _active_constraints_path(resolved)
    active_constraints = (constraints_path.read_text(encoding="utf-8")
                          if constraints_path.exists() else "")
    standing_block = "" if _standing_demoted(front) else "\n".join(
        text for _, text in _split_rendered_items(
            detail.extract_section_block(body, "Standing Directives")))
    intent_block = detail.extract_section_block(body, "Intent And Purpose")
    exact_block = detail.extract_section_block(body, "Exact Next Step")
    recap_block = detail.extract_section_block(body, "Session Recap")
    dialogue_block = detail.extract_section_block(body, "Recent Dialogue")
    verification_block = detail.extract_section_block(body, "Verification")
    # LATEST 본문만 보면 델타 저장에서 누적이 사라진다 — 체인을 거슬러 모은다.
    decisions_block = _render_decisions(_decision_projection(resolved, topic), lang)
    open_block = detail.extract_section_block(body, "Open")
    blockers_block = detail.extract_section_block(body, "Blockers And Questions")
    saved_git = (f"{front.get('git_branch')} / {front.get('git_commit')} / "
                 f"dirty={front.get('git_dirty')}")

    return _result("resume", resolved, name, repo.read_project_id(resolved), warnings, {
        "found": True,
        "broken": False,
        "detail_path": _rel(resolved, body_path),
        "status": front.get("status"),
        "git_drift": drift,
        "format_generation": format_generation,
        "live_relation": live_relation,
        # v5 reader consumers used this name; v6의 설명은 live relation으로 바뀌었어도
        # 기계값 집합은 같으므로 별칭을 함께 유지한다.
        "state_relation": live_relation,
        "live_changed_paths": live_changed_paths,
        "unknown_fields": unknown_fields,
        "prev_chain": prev_chain,
        "body": body,
        # scope_guard 는 기존 필드 계약이라 유지한다(옛 어댑터·타 벤더 호환).
        # resume_directives 가 이를 포함한 전체 지시문이며, 어댑터는 이쪽을 출력한다.
        "scope_guard": messages.msg("resume_scope_guard", lang, project_name=name, topic=topic),
        "work_id": _front_value(front, "work_id"),
        "resume_directives": resume_directives(
            name, topic, lang, work_id=_front_value(front, "work_id") or "",
            standing_block=standing_block,
            active_constraints=active_constraints, intent_block=intent_block,
            exact_block=exact_block, recap_block=recap_block,
            dialogue_block=dialogue_block, verification_block=verification_block,
            decisions_block=decisions_block, open_block=open_block,
            blockers_block=blockers_block,
            detail_path=_rel(resolved, body_path), saved_git=saved_git,
            state_relation=live_relation, live_changed_paths=live_changed_paths,
            trust_markers=trust_markers, constraint_paths=_constraint_paths(active_constraints)),
    })


def cmd_archive(cwd: str, topic: str, root: str | None = None) -> dict:
    resolved = repo.resolve_root(cwd, root)
    name = repo.project_name(resolved)
    topic = topics.normalize_topic(topic)
    lang = messages.resolve_lang(None)  # archive 는 payload 가 없음 → env/locale 체인.
    src = detail.topic_dir(resolved, topic)
    dst = detail.topic_dir(resolved, topic, archived=True)
    if not src.is_dir():
        return _result("archive", resolved, name, repo.read_project_id(resolved),
                       [messages.msg("warn_topic_not_active", lang, topic=topic)],
                       {"moved": False})
    if dst.exists():
        return _result("archive", resolved, name, repo.read_project_id(resolved),
                       [messages.msg("warn_archive_exists", lang, topic=topic)],
                       {"moved": False})
    dst.parent.mkdir(parents=True, exist_ok=True)
    # 디렉토리 이동(같은 볼륨). os.rename 은 dst 부재 시 디렉토리도 단위 이동.
    os.rename(str(src), str(dst))
    detail.regenerate_index(resolved, lang)
    return _result("archive", resolved, name, repo.read_project_id(resolved), [],
                   {"moved": True, "from": _rel(resolved, src), "to": _rel(resolved, dst)})


def _rel(root: str, path) -> str:
    try:
        return str(Path(path).resolve().relative_to(Path(root).resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def _result(command: str, root: str, name: str, project_id: str | None,
            warnings: list[str], extra: dict) -> dict:
    out = {
        "ok": True,
        "command": command,
        "project_root": root,
        "project_name": name,
        "project_id": project_id,
        "warnings": warnings,
    }
    out.update(extra)
    return out
