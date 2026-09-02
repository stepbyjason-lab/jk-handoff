"""사용자 언어 출력 — ko/en 메시지 테이블 + 언어 해석 체인.

체인: `payload.lang` > env `HANDOFF_LANG` > OS locale(`ko*`/`Korean*` → ko) > `en`.
저장 포맷 정체성(frontmatter 키·섹션 헤딩 13개·파일명 규칙)은 언어 무관 불변이다 —
번역 대상은 기본값(placeholder)·report·resume_prompt·경고·인덱스 장식 텍스트뿐이다.

**언어중립 파싱 지원**: `BLOCKER_DEFAULTS`(ko+en 전부의 "블로커 없음" 계열 상수집합)를
공개해, detail._read_topic_summary 가 어떤 언어로 저장된 본문이든 기본값-플레이스홀더를
정확히 빈 값 처리하도록 한다.
"""

from __future__ import annotations

import locale
import os

__all__ = [
    "resolve_lang",
    "msg",
    "BLOCKER_DEFAULTS",
    "GROUP_HEADINGS",
]

_SUPPORTED = ("ko", "en")


def resolve_lang(payload_lang: str | None) -> str:
    """언어 체인: payload > env HANDOFF_LANG > OS locale > en.

    OS locale 매핑: `ko` 로 시작하거나(`ko_KR`, `ko-KR`) `Korean` 으로 시작하면
    (Windows `Korean_Korea.949` 형태 포함) 'ko'. 그 외 전부 'en'. 알 수 없는
    값(payload/env 포함)은 무조건 'en' 으로 폴백한다 — 지원 언어 외 값을 그대로
    통과시키지 않는다.
    """
    for candidate in (payload_lang, os.environ.get("HANDOFF_LANG")):
        if candidate:
            normalized = candidate.strip().lower()
            if normalized in _SUPPORTED:
                return normalized
            # 지원 목록 밖이면 이 단계는 무시하고 체인의 다음 단계로 넘어간다
            # (완전 미인식 값이라도 즉시 en 으로 확정하지 않고 계속 체인을 탄다는
            # 뜻은 아니다 — payload/env 가 명시값을 줬는데 오탈자면 en 이 안전한
            # 기본값이므로 여기서 en 확정).
            return "en"

    loc = _detect_os_locale()
    if loc and (loc.lower().startswith("ko") or loc.startswith("Korean")):
        return "ko"
    return "en"


def _detect_os_locale() -> str | None:
    """OS locale 문자열을 얻는다. Windows 는 `Korean_Korea` 형태를 돌려준다."""
    try:
        lang, _ = locale.getlocale()
    except (ValueError, TypeError):
        lang = None
    if lang:
        return lang
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var)
        if value:
            return value
    return None


#: 지원 언어. 파싱측은 **어떤 언어로 저장된 본문이든** 인식해야 하므로 전 언어를 훑는다
#: (`BLOCKER_DEFAULTS` 가 같은 이유로 상수집합이다).
SUPPORTED_LANGS = ("ko", "en")


def msg(key: str, lang: str, **kwargs) -> str:
    """메시지 테이블에서 `key` 를 `lang` 으로 조회, 포맷 인자 적용."""
    table = _MESSAGES.get(lang) or _MESSAGES["en"]
    template = table.get(key) or _MESSAGES["en"].get(key, key)
    return template.format(**kwargs) if kwargs else template


# --- 본문 섹션 기본값(placeholder) ---
# 저장 시 lang 에 맞는 값 하나만 쓰이지만, 파싱측(detail._read_topic_summary) 은
# 어떤 언어로 저장된 본문이든 인식해야 하므로 두 언어 전부를 상수집합으로 노출한다.

BLOCKER_DEFAULTS = frozenset({
    "현재 블로커 없음.",
    "No blockers.",
})

GROUP_HEADINGS = {
    "ko": {
        "active": "## 진행 중",
        "waiting": "## 대기",
        "watching": "## 관망",
    },
    "en": {
        "active": "## In Progress",
        "waiting": "## Waiting",
        "watching": "## Watching",
    },
}

_MESSAGES: dict[str, dict[str, str]] = {
    "ko": {
        # detail.assemble_body 기본값
        "intent_default": "- 시작: (이 세션의 의도·목적 미기록)",
        "intent_note": (
            "> 세션이 무엇을 하려던 것인가. 중간에 바뀌었으면 전부 순서대로 — 마지막 것만 "
            "남기면 「왜 여기까지 왔나」가 사라진다. 아래 ## Utterance Ledger 를 먼저 채우고, "
            "그중 「지금 뭐 하는 중이야」의 답을 바꾼 것만 골라 적는다. 안 바뀐 세션이면 "
            "전환을 비운다 — 없는 전환을 만들지 말 것."
        ),
        "warn_schema_rejected": (
            "스키마 위반 {count}건 — 저장하지 않았다. 지적된 항목만 고쳐 "
            "쪼개 1회 재제출하라. 그래도 걸리면 force_schema 로 강등 저장된다."
        ),
        "warn_schema_demoted": (
            "⚠ 강등 저장: 스키마 위반 {count}건이 남은 채 기록됐다. "
            "frontmatter 의 schema_problems 를 보라."
        ),
        "warn_transcript_not_found": (
            "발화 대장 실패: 세션 {session_id} 의 트랜스크립트를 못 찾았다. "
            "빈 대장을 쓰면 「전수 처분」이 거짓이 되므로 진행하지 않는다 — "
            "`--transcript <경로>` 로 직접 주거나 호스트 경로 규칙을 갱신하라."
        ),
        "warn_compact_chain_broken": (
            "대장 부분 커버리지: {after} 는 자동압축으로 갈린 뒷부분인데, 앞부분 전사를 "
            "못 찾았다(직전 메시지 {logical_parent}). 앞 구간의 발화는 이 대장에 없다 — "
            "「세션 전체를 정리했다」고 쓰지 마라. 저장본에 덮은 범위를 명시하고, 옛 전사가 "
            "남아 있으면 `--transcript <경로>` 로 직접 줘라."
        ),
        "warn_compact_chain_incomplete": (
            "대장 부분 커버리지: {after} 는 자동압축으로 갈린 뒷부분인데, 앞 구간까지 "
            "거슬러 올라가지 못했다. 앞 구간의 발화는 이 대장에 없다 — 「세션 전체를 "
            "정리했다」고 쓰지 마라. 저장본에 덮은 범위를 명시하라."
        ),
        "warn_compact_chain_unreadable": (
            "대장 부분 커버리지: {after} 의 앞 전사를 찾았으나 **읽지 못했다**(잠겼거나 "
            "권한이 없거나 손상). 앞 구간의 발화는 이 대장에 없다 — 「세션 전체를 "
            "정리했다」고 쓰지 마라. 그 파일을 읽을 수 있게 한 뒤 다시 받아라."
        ),
        "incidents_default": "- 기록된 사고 없음. **0건은 의심 신호다** — 정말 없었는지 다시 본다.",
        "incidents_note": (
            "> **만들어 놓은 것이 의도대로 안 돌아간 것.** 코드 결함·절차 위반·잘못된 측정·"
            "거짓 보고 전부. 내가 낸 것과 남이 낸 것을 가리지 않는다. 아직 안 해본 것"
            "(Not Tried Yet)·막힌 것(Blockers)·접은 설계 선택(Failed Attempts)은 사고가 "
            "아니다. 다섯 칸을 다 채운다: **증상**(겉으로 보인 것 — 「아무것도 안 보였다」도 "
            "값이다) · **원인**(파일·함수까지) · **수명**(언제 심었고 언제 잡혔나) · "
            "**잡은 것**(자체검증/테스트/외부리뷰/사용자지적/운영중 중 하나) · **처방**. "
            "재발은 RETRIES, 해결은 RESOLVES 로 이전 사고를 가리킨다. 개수 상한 없음."
        ),
        "lessons_default": "- 이 세션에서 뽑을 교훈 없음.",
        "lessons_note": (
            "> 이 프로젝트를 몰라도 쓸모 있는 것만. 프로젝트 맥락이 필요하면 교훈이 아니라 "
            "상태다. **0건이 정상이다** — 배운 게 없는 세션이 훨씬 많다. 다섯 칸을 다 채운다: "
            "**언제**(꺼내 쓸 상황) · **무엇**(하라/하지 마라) · **왜** · "
            "**증거**(실측/추론/1회 관측) · **대신**(막았으면 대안). 개수 상한은 없다."
        ),
        "ledger_empty": "(발화 대장 없음 — 세션 식별자가 없어 전수 처분을 검증하지 못했다.)",
        "recap_goal_line": "> 목표: {summary}",
        "resume_standing_header": (
            "다음은 직전 세션까지 누적된 상시 규율이다 — 이 세션 전체에 그대로 적용하라. "
            "각 항목의 인용이 사용자 원문이다:"
        ),
        "session_recap_note": (
            "> **발화 → 응답/행동의 쌍**으로 쓴 산문. 사용자 쪽 전수는 위 대장이 보증하고, "
            "응답 쪽은 이 절이 채운다 — 사용자 말만 요약하면 답변이 상실된다. 덮을 범위는 "
            "아래 줄이 정한다(CLI 가 계산한다)."
        ),
        "recap_carried_uids": (
            "> **꼬리에 실린 대장 UID(JSON): {uids}.** 저장본의 대장 UID에서 이 목록을 "
            "빼면 요약 대상 UID 집합을 정확히 재구성할 수 있다."
        ),
        # **구간이 아니라 나머지다.** 꼬리가 덮는 발화가 연속이라는 보장이 없어
        # 「처음 ~ N 직전」으로 적으면 창 밖으로 밀린 발화를 쓰지 말라고 읽힌다.
        "recap_scope_bounded": (
            "> **덮을 구간(CLI 계수): 대장 {total}건 중 꼬리에 이미 원문으로 있는 {carried}건을 "
            "뺀 나머지 {covers}건.** 그 {carried}건은 아래 Recent Dialogue 에 있다 — "
            "**요약에서 다시 쓰지 마라.** 같은 구간을 두 번 실으면 후반이 이중 가중되고 "
            "전반이 두 번 밀린다. 꼬리는 「원문 참조」 한 줄로 넘기고, 지면은 나머지에 쓴다."
        ),
        "recap_scope_full": (
            "> **덮을 구간: 대장 전체.** 대화 꼬리와 겹치는 구간을 못 찾았으므로 전 구간을 요약한다."
        ),
        # 대장 전체가 꼬리 안에 들면 요약이 덮을 구간이 **없다**. 그때도 「전반·중반에
        # 쓴다」를 그대로 두면 없는 전반을 지어내게 된다 — 이 프로젝트가 「빈칸
        # boilerplate 채우기 금지」를 규율로 둘 만큼 겪은 실패다. 짧은 세션·델타에서 흔하다.
        "recap_scope_empty": (
            "> **덮을 구간 없음.** 대장이 통째로 아래 Recent Dialogue 안에 있다 — "
            "**요약을 쓰지 마라.** 원문이 이미 전부 실려 있으므로 여기 무엇을 쓰든 이중 "
            "가중이고, 없는 전반을 지어내는 자리가 된다."
        ),
        "session_recap_default": "(세션 요약 미작성)",
        "standing_note": (
            "> **다음 세션에도 참이어야 하는 규칙만.** 일회성 결정은 Decisions 에 남는다. "
            "재저장 시 CLI 가 이전 항목을 자동 승계한다 — 폐기는 REVERSES/SUPERSEDES 로만. "
            "resume 가 이 절의 원문을 재개 지시에 실어 나간다."
        ),
        "standing_default": "이 세션에서 새로 걸린 상시 규율 없음.",
        "dialogue_note": (
            "> 최근 대화 원문(도구 결과 제외) — CLI 가 트랜스크립트에서 그대로 삽입. "
            "요약이 아니라 육성이다. 방향과 어조는 여기서 읽는다."
        ),
        "dialogue_empty": "(대화 꼬리 없음 — 세션 식별자가 없어 추출하지 못했다.)",
        "dialogue_user_label": "사용자",
        "dialogue_assistant_label": "조수",
        "decision_interp_prefix": "해석(비권위): ",
        "ledger_density": (
            "> 밀도(CLI 계수): 발화 {total}건 → 담김 {placed}건 · 없음 {none_count}건({none_pct}%)"
        ),
        "done_default": "이번 세션에서 확정 완료된 것 없음 - 진행 중 또는 검토 단계.",
        "open_default": "- [ ] (다음 행동 미정)",
        "failed_attempts_default": "특별히 막힌 시도 없음.",
        "not_tried_default": "특별히 미시도 후보 없음.",
        "blockers_default": "현재 블로커 없음.",
        "decisions_default": "특기할 결정 없음.",
        "decisions_note": "> 사용자 확정 원문만. 요약·의역 금지 — Chair 판단은 ## Unapproved Proposals 로.",
        "unapproved_default": "- 미승인 제안 없음.",
        "unapproved_note": "> Chair 가 스스로 정한 것. 사용자가 승인한 적 없다 — 진행 전 확인할 것.",
        "exact_next_step_default": "(다음 세션이 수행할 단계 미정)",
        "exact_unapproved_marker": (
            "> ⚠ **이 다음 행동은 chair 가 정한 것이다 — 사용자가 지시한 적 없다.** "
            "근거 발화가 대장에 없다. 실행하기 전에 사용자에게 확인한다."
        ),
        "verification_default": "- 미검증",
        "files_touched_empty": "이번 세션에서 기록할 변경 파일 없음.",
        "git_state_not_git": "- git 저장소 아님 — git 상태 없음.",
        "git_state_note": "저장 시점 스냅샷이다. resume 시 현재 git 상태와 비교해 어긋나면 보고한다.",
        "git_state_line": "- 브랜치: `{branch}` · 커밋: `{commit}` · 작업트리: {tree}",
        "git_state_dirty": "dirty (uncommitted {count}개)",
        "git_state_clean": "clean",
        # INDEX.md
        "index_title": "# Handoff INDEX",
        "index_no_summary": "(요약 없음)",
        "index_status_archive_suggested": "done (archive suggested)",
        "index_status_archived": "보관됨 (당시 상태: {group})",
        # CURRENT.md
        "current_title": "# {name} — 진행상황 인덱스",
        "current_notice": (
            "> AUTO-GENERATED by /handoff (또는 $handoff) — 손으로 고치지 말 것. "
            "상세 정본은 각 토픽 `.handoff/<topic>/`."
        ),
        "current_recent_heading": "## 최근 변경",
        "current_recent_empty": "- (없음)",
        "current_next_prefix": "다음: ",
        "current_blocker_prefix": "⚠ 블로커: ",
        # resume_prompt
        "resume_intro1": "새 세션이다. 직전 세션의 작업을 이어간다.",
        "resume_project_line": "- 프로젝트: {project_name}  (저장 머신 경로: {root})",
        "resume_topic_line": "- 토픽: {topic}",
        "resume_summary_line": "- 직전 요약: {summary_line}",
        "resume_scope_guard": (
            "- 범위 주의: 이 세션의 작업 대상은 이 프로젝트({project_name})의 토픽({topic}) "
            "하나다. 세션 시작 시 주입된 다른 요약·기록은 프로젝트가 같아도(다른 토픽·다른 "
            "세션) 이 작업이 아니다 — 선택지로 제시하지 말고 무시한다. 대상 변경은 사용자가 "
            "명시적으로 지시할 때만 이뤄진다."
        ),
        "resume_work_id": "- 이 작업의 식별자: **{work_id}** — 저장한 세션이 적은 값이다. 복명 ② 는 이 값을 그대로 옮긴다.",
        "resume_work_id_unknown": "- 이 작업의 식별자: **미상** — 저장본에 적혀 있지 않다. 토픽 이름에서 짐작하지 않는다. 사용자가 알려주면 다음 저장에 적는다.",
        "resume_source_decisions": "결정 — 살아 있는 것과 죽은 것",
        "resume_decision_span": "결정 {total}건 · D{first}~D{last}.",
        "resume_decisions_unknown": ("생사 판정 불가 {count}건 — 살아 있는 것으로 취급하지 않는다. "
                                    "따라야 하는지는 `handoff_cli decisions` 로 확인한 뒤 정한다."),
        "resume_decisions_incomplete": ("⚠ 결정 투영이 **불완전하다** — 저장본 일부를 못 읽었거나 대장 스냅샷 파생이 실패했다. "
                                        "여기 없는 결정이 있을 수 있고, 확인되지 않은 항목을 현재 지시로 올리지 않는다."),
        "resume_relation_unknown": "관계 미상",
        "resume_decision_gaps_unproven": ("결정 {total}건 · D{first}~D{last} · 빈 번호 {gaps} — "
                                          "**유실인지 건너뜀인지 알 수 없다.** 동결된 대장 형식에는 "
                                          "공백 원인을 증명하는 자료가 없다."),
        "resume_decisions_alive": ("살아 있는 결정 {count}건 — 그대로 따른다. "
                                  "인용 원문이 필요하면 그때만 본문 `## Decisions` 를 편다."),
        "resume_decisions_dead": "죽은 결정 {count}건 — 다시 제안하지 않는다.",
        "resume_source_open": "미완료(Open)",
        "resume_source_blockers": "차단(Blockers)",
        "resume_block_scope": "━━━ 1. 범위 ━━━",
        "resume_block_authority": "━━━ 2. 현재 권한 — 그대로 따른다 ━━━",
        "resume_block_history": "━━━ 3. 역사 근거 — 이유를 이해하는 것이지 현재 지시가 아니다 ━━━",
        "resume_block_observation": "━━━ 4. 현재 관측 — 재확인 대상이다 ━━━",
        "resume_block_read": "━━━ 5. 반드시 전체를 읽을 것 (키워드 검색으로 훑지 않는다) ━━━",
        "resume_block_ack": "━━━ 6. 복명 — 아래를 채운 뒤에 멈춘다 ━━━",
        "resume_standing_scope": ("※ 위 규율과 결정은 일할 때 지킬 규칙이다. "
                                 "네가 무언가를 고쳤을 때 발동하지, 재개했다는 이유로 발동하지 않는다."),
        "resume_source_standing": "상시 규율 원문 — 사용자가 건 것, 영구",
        "resume_source_constraints": "현행 전역 제약 전문 — 지정된 기존 핸드오프 산출물",
        "resume_source_scope": "이번 범위 — 포함/제외와 권한 근거. 「미확정」이면 확정 범위로 쓰지 않는다",
        "resume_source_exact": "다음에 할 행동 하나 — Exact 전문 + 근거 발화 원문",
        "resume_source_recap": "직전 세션 요약 — Session Recap 전문",
        "resume_source_dialogue": "직전 대화 원문 — Recent Dialogue 30건",
        "resume_source_verification": "검증 — 저장 시점 결과와 각 as-of. 현재 효력은 미확인이다",
        "resume_none_recorded": "(기록 없음 — 지어내지 않는다.)",
        "resume_none": "없음",
        "resume_git_observation": "[Git: 저장 시점 {saved_git} · live 관계 {state_relation}]",
        "resume_changed_paths": "[변경된 경로: {paths}]",
        "resume_verification_unknown": "[검증: 미기록 — 현재 효력은 미확인이다]",
        "resume_history_caution": "※ 여기 적힌 과거의 판단·완료·PASS 를 현재 명령이나 현재 상태로 올리지 않는다.",
        "resume_observation_caution": "※ 위 관측은 저장 시점의 것이다. 지금도 그런지 확인한 뒤 판단한다.",
        "resume_read_instruction": (
            "판정에 쓰는 절(Decisions · Open · Blockers · Verification)은 **위 2·4 블록에 이미 원문으로 "
            "실려 있다.** 본문을 다시 열지 않는다 — 열면 같은 글자를 두 번 읽고 도구 왕복만 는다.\n\n"
            "  아래 문서는 전문을 읽는다: {constraint_paths}\n"
            "  **위 2 의 현행 전역 제약이 이름을 댄 문서도 전부 읽는다** — 이 줄이 「없음」이어도\n"
            "  그 표가 이긴다. CLI 는 사용자 소유 문서를 파싱하지 않으므로 경로를 못 뽑을 뿐이다.\n"
            "  본문 {detail_path} 는 역사 원장이다. 특정 UID·경위를 되짚을 때만 편다.\n\n"
            "※ `prev` 판본은 기본으로 읽지 않는다. 출처 단절·승계 충돌·판본 비교가 필요할 때만 편다.\n"
            "   정상 재개마다 이전 판을 다시 요약하는 것은 비용만 늘리고 세대 손실을 만든다."
        ),
        "resume_ack_slots": (
            "① 무엇을 이루려는 작업인가: 의도와 목적을 네 말로 한두 줄.\n"
            "   범위(무엇을 하고 안 하나)가 아니라 **무엇을 달성하면 끝인가**다.\n"
            "   위 2 의 「이번 범위」 절에서 답한다.                              [권한]\n"
            "② 이 작업을 조직이 뭐라 부르나: **위 1 의 「이 작업의 식별자」 줄에서\n"
            "   `**…**` 안에 든 값만 옮긴다.** 그 줄의 설명·주석은 버린다 — 줄을 통째로\n"
            "   옮기면 식별자가 한 문장이 되고, 그것이 다음 저장에 그대로 실린다.\n"
            "   `**미상**` 이면 「미상」이라고만 답한다.\n"
            "   **다시 고르지 마라** — 목록에서도, 토픽 이름에서도, 본문에서도 아니다.\n"
            "   그 값은 저장한 세션이 사용자와 확정한 것이고, 고르는 일은 저장하는 쪽의\n"
            "   몫이다. 여기서 새로 판단하면 매 재개마다 값이 흔들린다.          [권한]\n"
            "③ 잡무 위임 계획: 이 작업에서 생길 잡무를 열거하고 각각 어디로 보낼지 적는다.\n"
            "   잡무는 둘로 갈린다 — 단순 실행·조회(빌드·grep·설치·로그 파싱)와\n"
            "   추론이 조금 섞인 소작업(정독+해석, 작은 수정, 요약·번역).\n"
            "   라우팅 규칙은 네 전역 룰에 있다. **적기만 하고 지금 보내지 않는다.**  [권한]\n"
            "④ 개입 규칙: 리뷰를 판정하다가 「위반·scope 밖·무한확장」이라고 적는 순간\n"
            "   다음 이터레이션을 열지 않고 그 자리에서 멈춰 보고한다 — 한 줄로 복명한다.\n"
            "                                                                   [권한]\n"
            "⑤ 이전 세션에서 무슨 일이 있었나: 시작이 무엇이었고 방향이 몇 번 바뀌었나.\n"
            "   각 전환의 계기가 된 사용자 발화 UID 를 댄다.                       [역사]\n"
            "⑥ 살아 있는 결정이 총 몇 건인가. 죽은 것의 ID 를 전부 댄다.\n"
            "   죽은 것을 다시 제안하지 않는다.                                  [권한]\n"
            "⑦ 이번 범위: 포함과 제외, 각각의 권한 근거. 「미확정」이면 그렇게 적는다. [권한]\n"
            "⑧ 진행 상태: Open 이 몇 건이고 ACTIVE/WAITING/DEFERRED 로 어떻게 갈리나.\n"
            "   Blocker 가 있으면 해제 증거가 무엇인지 한 줄.                      [관측]\n"
            "⑨ 검증 상태: 저장본 `## Verification` 을 그대로 옮긴다. 코드가 움직였으면\n"
            "   재확인이 필요한 항목의 이름만 댄다. 지금 다시 재지 않는다.         [관측]\n"
            "⑩ 다음에 할 행동: 위 2 의 Exact 를 한 줄로 옮긴다. 적기만 하고 실행하지\n"
            "   않는다 — 저장 시점의 계획이라 이미 끝났을 수도 있다.              [권한]\n"
            "⑪ 규율·제약: 상시 규율과 현행 전역 제약을 각 항목 한 줄로 옮긴다.\n"
            "   출처 문서가 있으면 그 핵심도 한 줄. 없으면 「해당 없음」.\n"
            "   어떻게 적용할지는 지금 적지 않는다 — 일할 때 따른다.              [권한]\n"
            "⑫ 못 채운 칸이 있으면 「모름」이라고 적는다. 지어내지 않는다.\n\n"
            "열두 칸은 위에 실린 글로 답한다. 확인하려고 테스트를 돌리거나 코드를 뒤지지 않는다.\n"
            "작업 로그·보고는 사용자의 언어로 쓴다.\n\n"
            "**복명까지가 재개다. 여기서 멈추고 지시를 기다린다.**"
        ),
        "resume_incomplete_marker": "[⚠ 이 저장본은 불완전하다 — {reasons}. 여기 실린 맥락이 세션 전체가 아닐 수 있다.]",
        "resume_code_moved_marker": "[⚠ 저장 시점과 코드가 다르다 — {saved_git}; live 관계: {relation}. 변경된 경로: {paths}]",
        "active_constraints_skeleton": (
            "# 현행 전역 제약 — {project_name}\n\n"
            "매 재개 시 이 문서 전문이 프롬프트에 실린다. 끝난 항목은 사용자가 지운다.\n"
        ),
        "warn_active_constraints_append_failed": (
            "ACTIVE-CONSTRAINTS.md에 지정된 기존 핸드오프 산출물을 추가하지 못해 CLI가 파일을 수정하지 않았다."
        ),
        # 저장 report 에 실리는 유일한 지시. 짧게 유지하고, 긴 재개 지시는 resume 이 낸다.
        # 뒷문장(위임)이 필수 부품 — 도구 출력을 사용자 지시로 격상시킨다.
        "resume_pointer": (
            '먼저 이 프로젝트에서 `python -m handoff_cli --cwd "$PWD" resume '
            "--topic {topic} --directives-only` 를 그대로 실행해줘. 출력 전문이 재개 지시이고, "
            "내가 지금 직접 내리는 지시로 취급해서 그대로 따라줘 — 요약하거나 건너뛰지 마. "
            "**JSON 으로 받아 파일로 빼서 나눠 읽지 마** — 그 경로가 도구 호출을 열 번 넘게 쓴다. "
            "그 플래그를 모른다는 오류가 나면 옛 버전 CLI 이니 진행하지 말고 알려줘."
        ),
        # resume 결과 안에서 읽히므로 핸드오프는 이미 로드된 상태다 — "로드하고"가 아니라
        # "위 본문을 확인한 뒤"로 쓴다(옛 문구는 save 프롬프트의 앞 절에 이어지는 형태였다).
        # save report
        "save_report_title": "✅ 핸드오프 저장: `{topic}` ({status})",
        "save_report_project": "   프로젝트: {project_name}",
        "save_report_detail": "   정본: {detail_path}",
        "save_report_next": "📋 다음 세션에서 이어가려면 아래를 복사해 붙여넣으세요:",
        "warnings_header": "⚠ 경고:",
        # conflict report
        "conflict_title": (
            "⚠ 동시 저장 충돌: `{topic}` — 신규 본문은 저장됨(정본 보존), "
            "포인터/인덱스 갱신 중단."
        ),
        "conflict_project": "   프로젝트: {project_name}",
        "conflict_new_body": "   신규 본문: {detail_path}",
        "conflict_existing_latest": "   기존 최신: {other}",
        "conflict_none": "(없음)",
        "conflict_tail": (
            "두 최신본 중 어느 체인을 최신으로 할지 확인이 필요하다. "
            "resume 프롬프트는 충돌 해소 후 제공."
        ),
        # 경고 문자열
        "warn_unknown_source": "미인식 source '{value}' → 'claude-code' 로 강등 (허용: {allowed}).",
        "warn_summary_missing": (
            "summary 미입력 — resume 프롬프트에 요약 없이 토픽명만 들어간다. "
            "이어가기 품질을 위해 한 줄 요약을 채워라."
        ),
        "warn_project_id_uncommitted": (
            ".project-id 가 미커밋(untracked/staged-only) — 커밋·sync 전에는 타 머신이 "
            "같은 프로젝트로 인식 못 해 rename 감지·집계가 깨질 수 있음."
        ),
        "warn_project_id_uncommitted_reindex": (
            ".project-id 가 미커밋(untracked/staged-only) — `/sync` 커밋 전에는 타 머신이 "
            "같은 프로젝트로 인식 못 함."
        ),
        "warn_concurrent_save": (
            "저장 도중 LATEST.md 가 다른 writer 에 의해 변경됨 — 신규 본문은 보존, "
            "포인터/인덱스 갱신 중단. 두 최신본 중 어느 체인을 최신으로 할지 확인 필요."
        ),
        "warn_global_write_failed": "글로벌 CURRENT.md 갱신 실패(상세 정본은 보존됨): {exc}",
        "warn_no_handoff_dir": "`.handoff/` 없음 — 백필할 정본이 없어 글로벌 인덱스 미생성.",
        "warn_no_active_topics": "active 토픽 0개 — 빈 글로벌 인덱스를 만들지 않음.",
        "warn_reindex_failed": "글로벌 reindex 실패(정본 불변): {exc}",
        "warn_no_handoff_for_topic": "토픽 '{topic}' 의 handoff 가 없음.",
        "warn_broken_handoff": "broken handoff — LATEST 가 가리키는 {target} 가 없음.",
        "warn_git_drift": "git 상태가 저장 시점과 다름 — 이어가기 전 어느 상태에서 이어갈지 확인 필요.",
        "warn_topic_not_active": "토픽 '{topic}' 가 active 에 없음.",
        "warn_archive_exists": "이미 archived/{topic} 존재 — 덮어쓰지 않음.",
        "orphan_no_pointer": "LATEST.md 포인터 없음 — 본문 {newest} 가 가리켜지지 않음 (orphan).",
        "orphan_stale_pointer": (
            "LATEST.md 는 {latest_target} 를 가리키지만 더 새 본문 {newest} 가 존재 "
            "(orphan — 포인터 갱신 제안)."
        ),
        "warn_unrecognized_status": "미인식 status '{value}' → active 로 취급 (신 taxonomy: active/waiting/watching/done).",
        "warn_conflict_marker": "글로벌 CURRENT.md 에 머지충돌 마커 — 갱신 skip. `/sync` 후 `/handoff` 재실행.",
        "warn_remote_ahead": (
            "글로벌 `{pid_global}` remote-tracking ref 가 로컬보다 앞섬 — 갱신 skip. "
            "`/sync` 후 `/handoff` 재실행."
        ),
        "warn_legacy_project_id": "기존 글로벌 CURRENT.md 에 project_id 헤더 없음(레거시) — 재생성으로 정규화.",
        "warn_divergent_project_id": (
            "divergent project_id — 로컬 '{project_id}' vs 글로벌 '{existing_id}'. "
            "자동선택 안 함. 글로벌 갱신 중단, 수동 확인 필요."
        ),
        "warn_rename_suggested": (
            "글로벌 폴더명 '{matched_name}' ≠ 현재 basename '{name}' 이지만 project_id 동일 "
            "→ 기존 폴더에 기록. rename 제안: '{matched_name}' → '{name}'."
        ),
        "warn_secret_redacted": (
            "잠재 secret 라인 {idx} 를 글로벌 CURRENT.md 에서 [REDACTED] 처리 "
            "(git 히스토리는 안 지워짐 — 이미 커밋된 secret 은 filter-repo 필요)."
        ),
        "warn_invalid_sections": (
            "sections 필드가 dict 가 아님 — 무시하고 전 섹션 기본값으로 저장."
        ),
        "warn_unknown_section_key": (
            "미인식 section 키 '{section_key}' — 매핑되는 표준 섹션이 없어 버림(내용 유실 방지를 위한 경고)."
        ),
        "warn_duplicate_section_key": (
            "section '{canonical}' 에 중복 키 충돌 — '{kept}' 값을 채택, 무시된 키: {ignored}."
        ),
        "warn_invalid_section_value": (
            "section '{canonical}'(원본 키 '{section_key}') 의 값이 문자열이 아님 "
            "— 무시하고 기본값 사용."
        ),
        "warn_cross_project_files": (
            "files_touched 에 프로젝트 루트 밖 경로 {count}개 발견: {paths} — "
            "다른 프로젝트 작업이 섞였는지, `--root` 가 맞는지 확인하라."
        ),
    },
    "en": {
        "intent_default": "- Start: (session intent not recorded)",
        "intent_note": (
            "> What the session set out to do. If it shifted, list every shift in order — "
            "keeping only the last one loses why you got here. Fill ## Utterance Ledger first, "
            "then pick the ones that changed the answer to \"what am I doing right now\". "
            "If it never shifted, leave the transitions empty — do not invent one."
        ),
        "warn_schema_rejected": (
            "{count} schema violations — not saved. Fix only the flagged "
            "items by transition and resubmit once; if it still fails, force_schema saves "
            "in demoted form."
        ),
        "warn_schema_demoted": (
            "⚠ Demoted save: {count} schema violations recorded. "
            "See schema_problems in frontmatter."
        ),
        "warn_transcript_not_found": (
            "Utterance manifest failed: no transcript found for session {session_id}. "
            "Writing an empty manifest would make the \"account for every utterance\" "
            "guarantee false, so this stops — pass `--transcript <path>` or update the "
            "host path rule."
        ),
        "warn_compact_chain_broken": (
            "Partial manifest coverage: {after} is the tail half of a conversation split "
            "by auto-compaction, and the earlier transcript was not found (last message "
            "{logical_parent}). Utterances from that earlier stretch are absent — do not "
            "claim the whole session was accounted for. State the covered range in the "
            "handoff, and pass `--transcript <path>` if the old transcript still exists."
        ),
        "warn_compact_chain_incomplete": (
            "Partial manifest coverage: {after} is the tail half of a conversation "
            "split by auto-compaction, and the walk back to the earlier stretch did "
            "not complete. Those utterances are absent — do not claim the whole "
            "session was accounted for. State the covered range in the handoff."
        ),
        "warn_compact_chain_unreadable": (
            "Partial manifest coverage: the earlier transcript for {after} was found "
            "but could not be read (locked, no permission, or damaged). Those "
            "utterances are absent — do not claim the whole session was accounted "
            "for. Make that file readable and take the manifest again."
        ),
        "incidents_default": (
            "- No incidents recorded. **Zero is a suspicious signal** — look again."
        ),
        "incidents_note": (
            "> **Something built did not behave as intended.** Code defects, procedure "
            "violations, wrong measurements, false reports — all of it, whether you caused "
            "it or someone else did. Not incidents: things not yet tried (Not Tried Yet), "
            "things blocked (Blockers), design choices abandoned on judgement (Failed "
            "Attempts). Fill all five slots: **symptom** (what was visible — \"nothing was "
            "visible\" is itself a finding) · **cause** (down to file and function) · "
            "**lifetime** (when planted, when caught) · **caught by** (self-check / tests / "
            "external review / user / in production) · **fix**. Point at an earlier incident "
            "with RETRIES when it recurs, RESOLVES when it is settled. No cap on count."
        ),
        "lessons_default": "- No lessons to extract from this session.",
        "lessons_note": (
            "> Only what is useful to someone who does not know this project. If project "
            "context is required it is state, not a lesson. **Zero is normal** — most "
            "sessions teach nothing new. Fill all five slots: **when** (the situation that "
            "should recall it) · **what** (do / do not) · **why** · **evidence** "
            "(measured / inferred / single observation) · **instead** (the alternative). "
            "There is no cap on count."
        ),
        "ledger_empty": (
            "(No utterance ledger — no session id, so full accounting was not verified.)"
        ),
        "recap_goal_line": "> Goal: {summary}",
        "resume_standing_header": (
            "The following standing directives have accumulated through the previous "
            "sessions — apply them to this entire session as-is. The quote under each "
            "item is the user's own words:"
        ),
        "session_recap_note": (
            "> Prose written as **utterance → response/action pairs**. The ledger above "
            "guarantees user-side completeness; this section supplies the response side — "
            "summarizing only the user's words loses the answers. The line below sets the "
            "range to cover (the CLI computes it)."
        ),
        "recap_carried_uids": (
            "> **Ledger UIDs carried by the dialogue tail (JSON): {uids}.** Subtract this "
            "list from the saved ledger UIDs to reconstruct the exact recap UID set."
        ),
        "recap_scope_bounded": (
            "> **Range to cover (counted by the CLI): of {total} ledger entries, the "
            "{covers} that remain after removing the {carried} already carried verbatim "
            "by the dialogue tail.** Those {carried} appear under Recent Dialogue — "
            "**do not restate them here.** Carrying the same span twice double-weights "
            "the late session and pushes the early session out twice. Refer to the tail "
            "in one line and spend the space on the rest."
        ),
        "recap_scope_empty": (
            "> **Nothing to cover.** The whole ledger already sits inside Recent Dialogue "
            "below — **do not write a recap.** Everything is there verbatim, so anything "
            "written here is double-weighted and invents an early session that is not there."
        ),
        "recap_scope_full": (
            "> **Range to cover: the whole ledger.** No overlap with the dialogue tail was "
            "found, so summarize every segment."
        ),
        "session_recap_default": "(session recap not written)",
        "standing_note": (
            "> **Only rules that must stay true in the next session.** One-off decisions "
            "belong in Decisions. On re-save the CLI carries prior items forward — retire "
            "them only via REVERSES/SUPERSEDES. Resume ships this section verbatim in its "
            "directives."
        ),
        "standing_default": "No new standing directives from this session.",
        "dialogue_note": (
            "> Recent dialogue verbatim (tool results excluded) — inserted by the CLI "
            "straight from the transcript. Not a summary but the actual voice; read "
            "direction and tone here."
        ),
        "dialogue_empty": "(No dialogue tail — no session id, so nothing was extracted.)",
        "dialogue_user_label": "User",
        "dialogue_assistant_label": "Assistant",
        "decision_interp_prefix": "Reading (non-authoritative): ",
        "ledger_density": (
            "> Density (counted by CLI): {total} utterances -> {placed} placed · "
            "{none_count} none ({none_pct}%)"
        ),
        "done_default": "Nothing confirmed complete this session - in progress or under review.",
        "open_default": "- [ ] (next action not decided)",
        "failed_attempts_default": "No notably blocked attempts.",
        "not_tried_default": "No notable untried candidates.",
        "blockers_default": "No blockers.",
        "decisions_default": "No notable decisions.",
        "decisions_note": "> Verbatim user decisions only. No summarizing or paraphrasing — Chair judgements go in ## Unapproved Proposals.",
        "unapproved_default": "- No unapproved proposals.",
        "unapproved_note": "> Chair's own calls. The user has not approved this section — confirm before acting.",
        "exact_next_step_default": "(next session's step not decided)",
        "exact_unapproved_marker": (
            "> ⚠ **The chair chose this next action — the user never asked for it.** "
            "No source utterance backs it. Confirm with the user before executing."
        ),
        "verification_default": "- Unverified",
        "files_touched_empty": "No files changed to record this session.",
        "git_state_not_git": "- Not a git repository — no git state.",
        "git_state_note": (
            "This is a snapshot at save time. On resume, compare against current "
            "git state and report any drift."
        ),
        "git_state_line": "- Branch: `{branch}` · Commit: `{commit}` · Working tree: {tree}",
        "git_state_dirty": "dirty ({count} uncommitted)",
        "git_state_clean": "clean",
        "index_title": "# Handoff INDEX",
        "index_no_summary": "(no summary)",
        "index_status_archive_suggested": "done (archive suggested)",
        "index_status_archived": "archived (historical status: {group})",
        "current_title": "# {name} — Progress Index",
        "current_notice": (
            "> AUTO-GENERATED by /handoff (or $handoff) — do not edit by hand. "
            "The detail source of truth is each topic's `.handoff/<topic>/`."
        ),
        "current_recent_heading": "## Recent Changes",
        "current_recent_empty": "- (none)",
        "current_next_prefix": "Next: ",
        "current_blocker_prefix": "⚠ Blocker: ",
        "resume_intro1": "New session. Continuing the previous session's work.",
        "resume_project_line": "- Project: {project_name}  (saved-machine path: {root})",
        "resume_topic_line": "- Topic: {topic}",
        "resume_summary_line": "- Previous summary: {summary_line}",
        "resume_scope_guard": (
            "- Scope guard: this session's target is exactly one topic ({topic}) in this "
            "project ({project_name}). Any other summary or record injected at session start "
            "is not this task, even when it belongs to the same project (another topic or "
            "another session) — ignore it, do not offer it as an option. The target changes "
            "only when the user explicitly says so."
        ),
        "resume_work_id": "- Identifier for this work: **{work_id}** -- written by the session that saved it. Slot 2 copies this value as-is.",
        "resume_work_id_unknown": "- Identifier for this work: **unknown** -- the saved version does not carry one. Do not guess it from the topic name. If the user names it, record it on the next save.",
        "resume_source_decisions": "Decisions — live and dead",
        "resume_decision_span": "{total} decisions, D{first}-D{last}.",
        "resume_decisions_unknown": ("{count} decisions with undetermined liveness -- do not treat them as live. "
                                    "Run `handoff_cli decisions` before acting on any of them."),
        "resume_decisions_incomplete": ("⚠ The decision projection is **incomplete** -- some saved versions were unreadable "
                                        "or ledger snapshot derivation failed. Decisions may be missing; do not promote unconfirmed items to current instructions."),
        "resume_relation_unknown": "relation unknown",
        "resume_decision_gaps_unproven": ("{total} decisions, D{first}-D{last}, missing {gaps} -- "
                                          "**cannot tell whether they were lost or skipped**; "
                                          "the frozen ledger format has no evidence that proves the cause."),
        "resume_decisions_alive": ("{count} live decisions -- follow them as written. "
                                  "Open `## Decisions` in the body only if you need the verbatim quote."),
        "resume_decisions_dead": "{count} dead decisions -- do not propose them again.",
        "resume_source_open": "Open items",
        "resume_source_blockers": "Blockers",
        "resume_block_scope": "━━━ 1. Scope ━━━",
        "resume_block_authority": "━━━ 2. Current authority — follow as written ━━━",
        "resume_block_history": "━━━ 3. Historical evidence — understand why; it is not a current instruction ━━━",
        "resume_block_observation": "━━━ 4. Current observations — recheck them ━━━",
        "resume_block_read": "━━━ 5. Read everything (do not skim by keyword search) ━━━",
        "resume_block_ack": "━━━ 6. Acknowledge the following, then stop ━━━",
        "resume_standing_scope": ("※ The directives and decisions above are rules to follow while "
                                 "working. They fire when you change something, not because you resumed."),
        "resume_source_standing": "Standing directives verbatim — user-set and durable",
        "resume_source_constraints": "Current global constraints verbatim — designated existing handoff output",
        "resume_source_scope": "This scope — inclusion/exclusion and authority evidence; do not treat ‘unconfirmed’ as confirmed scope",
        "resume_source_exact": "One next action — Exact text and its source utterance",
        "resume_source_recap": "Previous-session recap — full Session Recap",
        "resume_source_dialogue": "Recent dialogue verbatim — 30 messages",
        "resume_source_verification": "Verification — saved results and each as-of; current validity is unconfirmed",
        "resume_none_recorded": "(Not recorded — do not invent it.)",
        "resume_none": "none",
        "resume_git_observation": "[Git: saved {saved_git} · live relation {state_relation}]",
        "resume_changed_paths": "[Changed paths: {paths}]",
        "resume_verification_unknown": "[Verification: not recorded — current validity is unconfirmed]",
        "resume_history_caution": "※ Do not promote a past judgement, completion, or PASS here into a current command or current state.",
        "resume_observation_caution": "※ These are observations as of save time. Re-confirm before acting on them.",
        "resume_read_instruction": (
            "The sections you judge from (Decisions, Open, Blockers, Verification) are **already carried "
            "verbatim in blocks 2 and 4 above.** Do not reopen the body -- rereading the same bytes only adds "
            "tool round-trips.\n\n"
            "  Read these documents in full: {constraint_paths}\n"
            "  **Also read every document named by the active constraints in block 2** -- even if this\n"
            "  line says none. The CLI does not parse user-owned documents, so it cannot list them.\n"
            "  The body {detail_path} is the historical ledger. Open it only to trace a specific UID.\n\n"
            "※ Do not read a `prev` version by default. Open it only for broken provenance, inheritance conflict, "
            "or version comparison. Re-summarizing a previous version on every normal resume only adds cost and generation loss."
        ),
        "resume_ack_slots": (
            "① What is this work trying to achieve: state the intent and purpose in your own words, one or two lines. Not the scope (what is in and out) but **what counts as done**. Answer from the scope section in block 2. [authority]\n"
            "② What does the organisation call this work: **copy only the value inside `**…**` on the \"Identifier for this work\" line in block 1.** Drop the explanatory text on that line -- copying the whole line turns the identifier into a sentence, and that sentence goes into the next save. If it reads `**unknown**`, answer just unknown. **Do not pick again** -- not from a list, not from the topic name, not from the body. That value was settled with the user by the session that saved it, and choosing is the saving side's job. Deciding again here makes the value drift on every resume. [authority]\n"
            "③ Chore delegation plan: list the chores this work will produce and where each one goes. Chores split two ways -- plain execution/lookup (builds, grep, installs, log parsing) and small tasks with some reasoning in them (close reading plus interpretation, small edits, summarizing, translating). The routing rules live in your global rules. **Write the plan; do not dispatch anything now.** [authority]\n"
            "④ Intervention rule: the moment you write \"violation / out of scope / runaway\" while judging a review, you do not open the next iteration -- you stop right there and report. Restate that rule in one line. [authority]\n"
            "⑤ What happened in the previous session: what was the start, how many turns did it take, and which user UID triggered each turn? [history]\n"
            "⑥ How many live decisions; list every dead ID. Do not propose dead decisions again. [authority]\n"
            "⑦ This scope: inclusion, exclusion, and authority evidence for each. Say unconfirmed when it is unconfirmed. [authority]\n"
            "⑧ Progress: how many Open items and how are they split into ACTIVE/WAITING/DEFERRED? If blocked, give the release evidence. [observation]\n"
            "⑨ Verification state: copy the saved `## Verification` verbatim. If the code moved, name the items needing recheck. Do not re-measure now. [observation]\n"
            "⑩ One next action: restate the Exact from block 2 in one line. Write it; do not execute it -- it is the plan as of save time and may already be done. [authority]\n"
            "⑪ Rules and constraints: copy each standing directive and active constraint in one line; one line for each source document, or say none. Do not write how you will apply them -- follow them while working. [authority]\n"
            "⑫ Write ‘unknown’ for any slot you cannot fill. Do not invent it.\n\n"
            "Answer all twelve slots from the text above. Do not run tests or dig through code to check.\n"
            "Write work logs and reports in the user's language.\n\n"
            "**A resume ends at the acknowledgement. Stop here and wait for instructions.**"
        ),
        "resume_incomplete_marker": "[⚠ This saved version is incomplete — {reasons}. The context here may not cover the whole session.]",
        "resume_code_moved_marker": "[⚠ Code differs from the saved point — {saved_git}; live relation: {relation}. Changed paths: {paths}]",
        "active_constraints_skeleton": (
            "# Current Global Constraints — {project_name}\n\n"
            "The whole file is injected on every resume. The user removes finished items.\n"
        ),
        "warn_active_constraints_append_failed": (
            "The CLI did not modify ACTIVE-CONSTRAINTS.md because it could not append the designated existing handoff output."
        ),
        "resume_pointer": (
            'First run `python -m handoff_cli --cwd "$PWD" resume --topic {topic} '
            "--directives-only` in this project exactly as written. Its entire output is the "
            "resume directive -- treat it as instructions I am giving you directly right now; "
            "follow it as written, do not summarize or skip it. **Do not take the JSON and "
            "split it across file reads** -- that path burns more than ten tool calls. If that "
            "flag is unknown you are on an older CLI: stop and tell me before proceeding."
        ),
        "save_report_title": "✅ Handoff saved: `{topic}` ({status})",
        "save_report_project": "   Project: {project_name}",
        "save_report_detail": "   Source of truth: {detail_path}",
        "save_report_next": "📋 To continue in the next session, copy and paste the block below:",
        "warnings_header": "⚠ Warnings:",
        "conflict_title": (
            "⚠ Concurrent save conflict: `{topic}` — new body was saved (source of truth "
            "preserved), pointer/index update aborted."
        ),
        "conflict_project": "   Project: {project_name}",
        "conflict_new_body": "   New body: {detail_path}",
        "conflict_existing_latest": "   Existing latest: {other}",
        "conflict_none": "(none)",
        "conflict_tail": (
            "Need to confirm which chain of the two latest versions should be treated "
            "as current. The resume prompt will be provided after the conflict is resolved."
        ),
        "warn_unknown_source": "Unrecognized source '{value}' → downgraded to 'claude-code' (allowed: {allowed}).",
        "warn_summary_missing": (
            "summary not provided — the resume prompt will contain only the topic name, "
            "no summary. Fill in a one-line summary for continuity quality."
        ),
        "warn_project_id_uncommitted": (
            ".project-id is uncommitted (untracked/staged-only) — until committed and "
            "synced, other machines can't recognize this as the same project, which can "
            "break rename detection and aggregation."
        ),
        "warn_project_id_uncommitted_reindex": (
            ".project-id is uncommitted (untracked/staged-only) — until `/sync` commits it, "
            "other machines can't recognize this as the same project."
        ),
        "warn_concurrent_save": (
            "LATEST.md was changed by another writer while saving — the new body was "
            "preserved, but the pointer/index update was aborted. Need to confirm which "
            "chain of the two latest versions should be treated as current."
        ),
        "warn_global_write_failed": "Global CURRENT.md update failed (detail source of truth preserved): {exc}",
        "warn_no_handoff_dir": "No `.handoff/` — nothing to backfill, global index not created.",
        "warn_no_active_topics": "0 active topics — not creating an empty global index.",
        "warn_reindex_failed": "Global reindex failed (source of truth unchanged): {exc}",
        "warn_no_handoff_for_topic": "No handoff exists for topic '{topic}'.",
        "warn_broken_handoff": "broken handoff — LATEST points to {target}, which does not exist.",
        "warn_git_drift": "git state differs from save time — confirm which state to continue from before proceeding.",
        "warn_topic_not_active": "Topic '{topic}' is not in active.",
        "warn_archive_exists": "archived/{topic} already exists — not overwriting.",
        "orphan_no_pointer": "No LATEST.md pointer — body {newest} is not referenced (orphan).",
        "orphan_stale_pointer": (
            "LATEST.md points to {latest_target} but a newer body {newest} exists "
            "(orphan — pointer update suggested)."
        ),
        "warn_unrecognized_status": "Unrecognized status '{value}' → treated as active (new taxonomy: active/waiting/watching/done).",
        "warn_conflict_marker": "Global CURRENT.md has merge conflict markers — update skipped. Re-run `/handoff` after `/sync`.",
        "warn_remote_ahead": (
            "Global `{pid_global}` remote-tracking ref is ahead of local — update skipped. "
            "Re-run `/handoff` after `/sync`."
        ),
        "warn_legacy_project_id": "Existing global CURRENT.md has no project_id header (legacy) — normalizing via regeneration.",
        "warn_divergent_project_id": (
            "Divergent project_id — local '{project_id}' vs global '{existing_id}'. "
            "Not auto-selecting. Global update aborted, manual confirmation needed."
        ),
        "warn_rename_suggested": (
            "Global folder name '{matched_name}' != current basename '{name}' but "
            "project_id matches → recording to the existing folder. Rename suggested: "
            "'{matched_name}' → '{name}'."
        ),
        "warn_secret_redacted": (
            "Redacted a potential secret on line {idx} of global CURRENT.md "
            "([REDACTED] — git history is not rewritten; secrets already committed need filter-repo)."
        ),
        "warn_invalid_sections": (
            "sections field is not a dict — ignored, all sections saved with defaults."
        ),
        "warn_unknown_section_key": (
            "Unrecognized section key '{section_key}' — no matching standard section, dropped "
            "(warned to prevent silent content loss)."
        ),
        "warn_duplicate_section_key": (
            "Duplicate key conflict for section '{canonical}' — kept '{kept}', ignored: {ignored}."
        ),
        "warn_invalid_section_value": (
            "Value for section '{canonical}' (raw key '{section_key}') is not a string "
            "— ignored, default used."
        ),
        "warn_cross_project_files": (
            "Found {count} path(s) outside the project root in files_touched: {paths} — "
            "check whether another project's work got mixed in, or whether `--root` is correct."
        ),
    },
}
