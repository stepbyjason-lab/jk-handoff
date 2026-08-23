"""토픽 상세 본문 · LATEST.md · INDEX.md (프로젝트 정본).

라이브 동작 보존(단계1 보존표): Body Template 14헤딩(절 11 + CLI 데이터 블록 3), detail frontmatter 15키
(status 만 4-value 로 확장), 동시저장 보존, orphan 감지, INDEX 재생성 우선순위
(status·summary·date precedence, archive suggested 무자동이동), LATEST 포인터
표준/레거시 변형 독해.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from pathlib import Path

from . import atomicio, messages, status as status_mod

__all__ = [
    "topic_dir",
    "parse_frontmatter",
    "parse_latest_target",
    "read_latest_target",
    "detail_filename",
    "assemble_body",
    "write_detail",
    "write_latest",
    "scan_topics",
    "regenerate_index",
    "detect_orphan",
    "TopicSummary",
]

_BODY_FILE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{6}-[0-9a-f]{8}\.md$")
_LATEST_ARROW_RE = re.compile(r"#\s*LATEST\s*(?:->|→)\s*(\S+)")
_LATEST_LINK_RE = re.compile(r"\[([^\]]+\.md)\]\(([^)]+\.md)\)")


def handoff_root(root: str) -> Path:
    return Path(root) / ".handoff"


def topic_dir(root: str, topic: str, archived: bool = False) -> Path:
    base = handoff_root(root)
    if archived:
        return base / "archived" / topic
    return base / topic


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """`--- ... ---` frontmatter 를 단순 `key: value` 로 파싱. (yaml 의존 없음.)"""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    block = text[3:end].strip("\n")
    body = text[end + 4 :].lstrip("\n")
    data: dict = {}
    for line in block.splitlines():
        if ":" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition(":")
            data[key.strip()] = value.strip()
    return data, body


def parse_latest_target(text: str) -> str | None:
    """이미 읽은 ``LATEST.md`` 본문에서 포인터 대상만 뽑는다.

    스냅샷 판독자가 같은 파일을 다시 읽지 않도록 파일 I/O와 기존 포인터 문법 판독을
    분리한다. 포인터가 없으면(레거시 본문형) ``None``이다.
    """
    arrow = _LATEST_ARROW_RE.search(text)
    if arrow:
        cand = arrow.group(1).strip()
        if cand.endswith(".md") and not cand.startswith("["):
            return Path(cand).name
    link = _LATEST_LINK_RE.search(text)
    if link:
        return Path(link.group(2)).name
    return None


def read_latest_target(tdir: Path) -> str | None:
    """LATEST.md 가 가리키는 본문 파일명을 돌려준다.

    표준(`# LATEST -> file.md`), 화살표(`→`), 레거시 링크(`→ [f.md](f.md)`) 모두
    독해. 포인터가 없으면(레거시 본문형) None.
    """
    latest = tdir / "LATEST.md"
    if not latest.exists():
        return None
    try:
        text = latest.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    return parse_latest_target(text)


def detail_filename(now: datetime, existing: set[str]) -> str:
    """`YYYY-MM-DD-HHMMSS-<uuid8>.md`. 충돌 시 새 UUID 로 재시도."""
    stamp = now.strftime("%Y-%m-%d-%H%M%S")
    while True:
        suffix = uuid.uuid4().hex[:8]
        name = f"{stamp}-{suffix}.md"
        if name not in existing:
            return name


def defuse_boundary_lines(text: str) -> str:
    """줄머리가 절·블록 경계로 오인될 줄을 인용(`> `)으로 감싼다. **글자는 지우지 않는다.**

    산문에 `## Decisions` 를 쓰면 **그 세션의 결정이 통째로 사라진다.** 실측(2026-08-21):
    가짜 헤딩이 생겨 `extract_section_block("Decisions")` 이 그 뒤를 읽었고, 진짜 결정 절은
    안 읽혔으며, 저장은 `ok: True` 로 통과했다. 이 프로젝트는 핸드오프 구조 자체를 다루므로
    그런 산문이 자주 나온다 — 우연히 안 났을 뿐이다.

    **들여쓰기로는 못 막는다** — 추출기가 `line.strip()` 으로 보기 때문이다. 인용부호를
    붙이면 추출기에 안 걸리고, 마크다운에서는 원문이 그대로 보인다. `Recent Dialogue` 가
    이미 같은 방식으로 안전했다(그래서 대화 꼬리에서는 이 사고가 한 번도 안 났다).

    `### ` 로 시작하는 교훈·사고 ID 헤딩은 건드리지 않는다 — 그건 절 경계가 아니다.
    """
    return "\n".join(
        f"> {line}" if line.lstrip().startswith(("## ", "━━━")) else line
        for line in (text or "").splitlines())


def _section(value: str | None, empty: str) -> str:
    value = (value or "").strip()
    # 어댑터 산문이 들어오는 유일한 관문이다 — 경계 방어를 여기 한 곳에 둔다.
    return defuse_boundary_lines(value) if value else empty


#: `## Utterance Ledger` 의 `담긴 곳` 으로 쓸 수 있는 값. 절 이름이거나 「없음」이다.
#: `Git State`·`Files Touched`·`Utterance Ledger` 는 CLI 가 만드는 데이터라 목적지가 아니다.
LEDGER_DESTINATIONS = (
    "Intent And Purpose", "Done", "Open", "Failed Attempts", "Not Tried Yet",
    "Blockers And Questions", "Decisions", "Unapproved Proposals",
    "Exact Next Step", "Verification", "Incidents", "Lessons",
    "Standing Directives",
)

#: 사고를 **무엇이 잡았나**. 닫힌 집합인 이유는 관계 토큰과 같다 — 자유 서술이면
#: 세지 못하고, 세지 못하면 「검증 절차가 실제로 무엇을 잡나」에 답할 수 없다.
#: 실측(2026-08-18 한 세션): 자체검증 0 · 외부리뷰 15 · 사용자지적 4.
INCIDENT_CATCHERS = ("자체검증", "테스트", "외부리뷰", "사용자지적", "운영중")

#: 목적지 없음 — 이 발화가 남길 내용이 없었다는 좁은 주장.
LEDGER_NONE = "없음"


def render_ledger(rows: list, lang: str = "ko") -> str:
    """처분 대장을 마크다운 표로 렌더한다. 밀도 줄은 CLI 가 따로 얹는다.

    **어댑터는 배열을 넘기고 표는 CLI 가 만든다.** 마크다운을 넘겨받아 정규식으로 파싱했더니
    형식 사고가 반복됐다(표만 검사하는 검사가 목록 형식에서 안 돌던 일, 앵커 수로 뭉침을
    잡으려다 헛짚은 일). 구조로 받으면 검사가 전부 dict 연산이 된다.
    """
    if not rows:
        return messages.msg("ledger_empty", lang)
    out = ["| UID | 지문 | 담긴 곳 | 무엇이 남았나 |", "|---|---|---|---|"]
    for row in rows:
        uid = str(row.get("uid", "")).strip()
        excerpt = " ".join(str(row.get("excerpt", "")).split()).replace("|", "\\|")
        section = str(row.get("section", "")).strip()
        note = " ".join(str(row.get("note", "")).split()).replace("|", "\\|")
        out.append(f"| {uid} | {excerpt} | {section} | {note or '—'} |")
    return "\n".join(out)


#: frontmatter·마크다운 구조를 만드는 문자들. 어댑터 값에 들어오면 제거한다.
_STRUCTURE_CHARS = str.maketrans({c: " " for c in "\r\n\t\v\f\x00"})


def sanitize_line(value) -> str:
    """어댑터가 준 문자열을 **한 줄로** 정규화한다 (기획 §8 경계 세탁).

    지금까지 나온 결함 다수가 뿌리 하나였다: 어댑터 문자열을 마크다운·frontmatter 로
    직렬화한 뒤 **다시 구조로 파싱**한다. 개행 하나가 그때마다 새 구조를 만들었다 —
    `note` 로 가짜 규율을 위조했고, 접었더니 `target` 으로 재발했고, `id` 개행은
    **frontmatter 에 새 줄을 만들어 `schema_demoted: true` 를 `false` 로 덮었다.**

    필드마다 접는 방식은 두더지잡기라 **경계에서 한 번만** 세탁한다. 값을 거부하지 않고
    무해화만 한다 — 거부는 게이트의 일이고, 여기는 렌더가 구조를 배신하지 않게 하는 자리다.
    """
    return " ".join(str(value if value is not None else "").translate(
        _STRUCTURE_CHARS).split())


def sanitize_frontmatter_value(value) -> str:
    """frontmatter 한 줄에 실릴 값. 구분자(`,`·`:`)까지 뺀다.

    `schema_problems` 는 `code:uid, code:uid` 로 직렬화되므로, uid 에 콤마·콜론이 있으면
    읽는 쪽이 **가짜 코드를 읽는다**(실측: ID 에 콤마를 심어 오염 코드를 주입해
    상시 규율 격리 판정을 뒤집었다).
    """
    return sanitize_line(value).replace(",", " ").replace(":", " ").strip()


def render_decisions(entries: list, quotes: dict, lang: str = "ko") -> str:
    """결정을 **인용(권위) + 해석(비권위)** 으로 렌더한다.

    **모델은 인용문도 주체도 쓰지 않는다.** UID 만 가리키고, 인용은 CLI 가 대장 원문에서
    넣고 주체는 `source` 유무에서 파생한다. 그래서 「사용자가 정한 것」을 chair 것으로,
    또는 그 반대로 적는 일이 **필드 자체가 없어서** 불가능해진다.

    거짓 해석을 막지는 못한다(의미는 기계가 못 본다). 대신 **바로 위 인용이 반박**하게 놓아
    읽는 순간 걸리게 한다 — 이 부류에서 그것이 정직한 최선이다.
    """
    if not entries:
        return ""
    out = []
    for e in entries:
        # 기획 §8 — **렌더에 들어가는 모든 어댑터 값을 한 줄로 세탁한다.** 필드별로
        # 접으면 접지 않은 필드로 같은 위조가 재발한다(note → target 실측).
        uids = [sanitize_line(u) for u in (e.get("source") or []) if sanitize_line(u)]
        actor = "user" if uids else "chair"
        head = f"- **{sanitize_line(e.get('id'))}**"
        if uids:
            head += f" — 출처: {' · '.join(uids)} · {actor}"
        else:
            head += f" — 출처: {actor}(미승인)"
        rels = e.get("relations") or []
        if rels:
            head += " (" + ", ".join(
                f"{sanitize_line(r.get('token'))}: {sanitize_line(r.get('target'))}"
                + (f", {sanitize_line(r['note'])}" if r.get("note") else "")
                for r in rels) + ")"
        out.append(head)
        for uid in uids:
            for line in (quotes.get(uid) or "").splitlines() or [""]:
                out.append(f"  > {line}".rstrip())
        interp = sanitize_line(e.get("interpretation"))
        out.append(f"  {messages.msg('decision_interp_prefix', lang)}{interp}")
    return "\n".join(out)


def render_dialogue(rows: list, lang: str = "ko") -> str:
    """`Recent Dialogue` — 대화 꼬리 원문. CLI 가 트랜스크립트에서 뽑아 그대로 넣는다.

    자동압축 분석(2026-08-18)에서 가져온 부품: 압축이 97% 를 버리고도 자연스러운 이유는
    요약 옆에 최근 대화 **원문**이 붙어 있어서다. 모델이 옮겨 적지 않으므로(D-8 과 같은
    원리) 생성 토큰 0, 변형 사고도 구조적으로 없다.
    """
    if not rows:
        return messages.msg("dialogue_empty", lang)
    out = []
    for row in rows:
        label = messages.msg("dialogue_user_label" if row.get("role") == "user"
                             else "dialogue_assistant_label", lang)
        out.append(f"**{label}**")
        for line in str(row.get("text", "")).splitlines() or [""]:
            out.append(f"> {line}".rstrip())
        out.append("")
    return "\n".join(out).rstrip()


def _tail_uid_range(ledger: list, dialogue: list) -> tuple[str, str] | None:
    """대화 꼬리가 덮는 발화 UID 구간 `(첫, 끝)`. 겹치는 게 없으면 None.

    꼬리는 사용자·assistant 를 섞어 담고 대장은 사용자 발화만 담으므로, **텍스트가 같은
    것끼리 맞춰야** 구간이 나온다. UID 를 못 맞추면 조용히 넘어가지 않고 None 을 돌려
    「전 구간을 요약하라」로 떨어진다 — 잘못된 구간을 빼라고 하면 그 구간이 통째로 사라진다.
    """
    tail_texts = {" ".join(str(r.get("text", "")).split())
                  for r in dialogue if r.get("role") == "user"}
    if not tail_texts:
        return None
    hits = [row["uid"] for row in ledger
            if " ".join(str(row.get("excerpt", "")).split()) and any(
                t.startswith(" ".join(str(row["excerpt"]).split()).rstrip("…"))
                for t in tail_texts)]
    return (hits[0], hits[-1]) if hits else None


def assemble_body(meta: dict, sections: dict, files_touched: list, created_human: str,
                  lang: str = "ko", ledger: list | None = None,
                  dialogue: list | None = None) -> str:
    """라이브 Body Template 18헤딩을 바이트 결정적으로 조립한다.

    **13개는 어댑터가 채우는 절**이고, 넷(`Git State`·`Files Touched`·`Utterance Ledger`·
    `Recent Dialogue`)은 **CLI 가 만드는 데이터 블록**이다. 헤딩·frontmatter 키·파일명 규칙은
    언어 무관 불변 — 번역 대상은 각 절의 기본값(placeholder)·고정 주석 줄·Git State 라인·
    빈 값 문구뿐이다.

    말미는 **맥락 전달 묶음**(R8, 사용자 제안 2026-08-18)이다:
    `Session Recap`(목표 1줄 + 대장 전체 왕복 요약) → `Standing Directives` →
    `Exact Next Step` → `Recent Dialogue`. 자동압축의 주입 구조(요약 → 원문 꼬리 → 이어서
    작업)를 같은 자리에 재현한다 — 모델의 문서 기억은 U자(양끝 강, 가운데 약)라 행동
    지시와 육성이 맨 끝에 있어야 최신 효과를 탄다.
    """
    git = meta["git"]
    if git["is_git"]:
        branch = git["branch"] or "null"
        commit = git["commit"] or "null"
        if git["dirty"]:
            tree = messages.msg("git_state_dirty", lang, count=git["dirty_count"])
        else:
            tree = messages.msg("git_state_clean", lang)
        git_line = messages.msg("git_state_line", lang, branch=branch, commit=commit, tree=tree)
    else:
        git_line = messages.msg("git_state_not_git", lang)

    if files_touched:
        rows = ["| File | State | Note |", "|---|---|---|"]
        for entry in files_touched:
            path = entry.get("path", "")
            state = entry.get("state", "")
            note = entry.get("note", "")
            rows.append(f"| `{path}` | {state} | {note} |")
        files_block = "\n".join(rows)
    else:
        files_block = messages.msg("files_touched_empty", lang)

    ledger_block = render_ledger(ledger or [], lang)
    dialogue_block = render_dialogue(dialogue or [], lang)

    # **요약이 덮을 구간을 CLI 가 계산해 못박는다.** 「고르게 쓰라」는 훈계로는 최신성
    # 편향을 못 막는다(외부 리뷰 지적, 2026-08-18): 꼬리가 이미 원문으로 있는데 요약까지
    # 후반으로 기울면 같은 구간이 **이중 가중**되고 전반은 두 번 밀린다. 그래서 꼬리가
    # 실제로 덮는 UID 구간을 여기서 재고, 그 구간을 요약에서 **빼라고** 지시한다.
    tail_uids = _tail_uid_range(ledger or [], dialogue or [])
    if tail_uids:
        recap_scope_line = messages.msg("recap_scope_bounded", lang,
                                        first=tail_uids[0], last=tail_uids[1])
    else:
        recap_scope_line = messages.msg("recap_scope_full", lang)

    front = (
        "---\n"
        f"topic: {meta['topic']}\n"
        f"created: {meta['created']}\n"
        f"project_root: {meta['project_root']}\n"
        f"status: {meta['status']}\n"
        f"prev: {meta['prev'] if meta['prev'] else 'null'}\n"
        f"source: {meta['source']}\n"
        # lang: 저장 언어. resume 이 지시문을 재개 시점에 만들므로 이게 있어야 저장 언어로
        # 복원된다. 옛 파일엔 없으므로 읽는 쪽은 부재를 허용하고 언어체인으로 폴백한다.
        f"lang: {meta.get('lang') or 'null'}\n"
        # R8: 저작 조건 기록. 「형식이 잘한 건가 모델이 잘한 건가」를 나중에 가르려면
        # 저장본 자체에 조건이 남아야 한다(실측: E1·E2·E3 세 표본이 전부 모델 미확인으로
        # 떠서 채점 해석이 추정에 머물렀다). writer_model 은 어댑터 자기 신고라 비어 있을
        # 수 있고, session_id 는 하네스가 준 값이라 결정적이다 — 그것으로 나중에 조회한다.
        f"writer_model: {meta.get('writer_model') or 'null'}\n"
        f"writer_effort: {meta.get('writer_effort') or 'null'}\n"
        f"writer_session: {meta.get('writer_session') or 'null'}\n"
        # 한 세션에서 두 번 이상 저장할 때, 이 본문이 **어느 시점 이후**를 덮는지.
        # null 이면 세션 처음부터다. 이게 없으면 두 번째 저장본만 읽은 사람이
        # 「세션이 여기서 시작됐다」고 오해한다 — 델타를 전체로 읽는 사고.
        f"covers_from: {meta.get('covers_from') or 'null'}\n"
        # 스키마 강등 표식. **본문 스탬프만으로는 관측 경로가 없다** — writer_model 을 안 남겨
        # 저작 모델이 추정뿐이 됐던 것과 같은 교훈이다. 여기 있어야 인덱스·집계가 읽는다.
        f"schema_demoted: {'true' if meta.get('schema_demoted') else 'false'}\n"
        f"schema_problems: {meta.get('schema_problems') or 'null'}\n"
        # R8 v6: 다음 행동의 근거와 대상은 본문 인용만으로 남기지 않는다. JSON 배열은
        # 기존 단순 frontmatter reader가 문자열로 보존할 수 있어 새 Markdown 파서가 필요 없다.
        f"body_contract: {meta.get('body_contract', 1)}\n"
        f"exact_source_uids: {json.dumps(meta.get('exact_source_uids') or [], ensure_ascii=False)}\n"
        f"exact_target_paths: {json.dumps(meta.get('exact_target_paths') or [], ensure_ascii=False)}\n"
        f"git_branch: {git['branch'] if git['branch'] else 'null'}\n"
        f"git_commit: {git['commit'] if git['commit'] else 'null'}\n"
        f"git_dirty: {('true' if git['dirty'] else 'false') if git['is_git'] else 'null'}\n"
        "---\n"
    )

    summary = meta.get("summary") or meta["topic"]
    body = (
        f"\n# Handoff: {meta['topic']} - {created_human}\n\n"
        f"> {summary}\n\n"
        # `Intent And Purpose` 가 상태 절(Done…)보다 앞이다 — 상태 축만 있으면 최신 것만으로도
        # 문법적으로 다 채워져 세션 전반이 증발한다. 전수 보증은 맨 끝 `Utterance Ledger` 가 진다.
        "## Intent And Purpose\n\n"
        f"{messages.msg('intent_note', lang)}\n\n"
        f"{_section(sections.get('intent'), messages.msg('intent_default', lang))}\n\n"
        "## Done\n\n"
        f"{_section(sections.get('done'), messages.msg('done_default', lang))}\n\n"
        "## Open\n\n"
        f"{_section(sections.get('open'), messages.msg('open_default', lang))}\n\n"
        "## Failed Attempts\n\n"
        f"{_section(sections.get('failed_attempts'), messages.msg('failed_attempts_default', lang))}\n\n"
        "## Not Tried Yet\n\n"
        f"{_section(sections.get('not_tried'), messages.msg('not_tried_default', lang))}\n\n"
        "## Blockers And Questions\n\n"
        f"{_section(sections.get('blockers'), messages.msg('blockers_default', lang))}\n\n"
        "## Git State\n\n"
        f"{git_line}\n\n"
        f"{messages.msg('git_state_note', lang)}\n\n"
        "## Files Touched\n\n"
        f"{files_block}\n\n"
        "## Decisions\n\n"
        f"{messages.msg('decisions_note', lang)}\n\n"
        f"{_section(sections.get('decisions'), messages.msg('decisions_default', lang))}\n\n"
        "## Unapproved Proposals\n\n"
        f"{messages.msg('unapproved_note', lang)}\n\n"
        f"{_section(sections.get('unapproved'), messages.msg('unapproved_default', lang))}\n\n"
        "## Verification\n\n"
        f"{_section(sections.get('verification'), messages.msg('verification_default', lang))}\n\n"
        # 사고는 교훈의 **원료**다. 증류 결과(Lessons)보다 먼저 온다.
        "## Incidents\n\n"
        f"{messages.msg('incidents_note', lang)}\n\n"
        f"{_section(sections.get('incidents'), messages.msg('incidents_default', lang))}\n\n"
        # 경험 축 — 프로젝트를 넘어 재사용되는 것만. 0건이 정상이다.
        "## Lessons\n\n"
        f"{messages.msg('lessons_note', lang)}\n\n"
        f"{_section(sections.get('lessons'), messages.msg('lessons_default', lang))}\n\n"
        # 대장은 **데이터**다(Git State·Files Touched 와 같은 급). 어댑터가 배열로 넘기고
        # CLI 가 렌더한다 — 마크다운을 넘겨받아 정규식으로 파싱하면 형식 사고가 되풀이된다.
        # 가운데쯤 두는 이유: U자 기억 곡선의 회수율 최저 구간에는 색인으로 다시 찾을 수
        # 있는 것을 둔다 — 대장은 UID 로 찾아 읽는 색인이지 흐름으로 읽는 절이 아니다.
        "## Utterance Ledger\n\n"
        f"{ledger_block}\n\n"
        # ── 맥락 전달 묶음 ── 여기서부터 문서 끝까지가 「다음 세션이 마지막에 읽는 것」이다.
        # 목표 1줄은 CLI 가 summary 를 재삽입한다 — 새 입력 없음.
        "## Session Recap\n\n"
        f"{messages.msg('recap_goal_line', lang, summary=summary)}\n\n"
        f"{messages.msg('session_recap_note', lang)}\n"
        f"{recap_scope_line}\n"
        f"{_section(sections.get('session_recap'), messages.msg('session_recap_default', lang))}\n\n"
        "## Standing Directives\n\n"
        f"{messages.msg('standing_note', lang)}\n\n"
        f"{_section(sections.get('standing'), messages.msg('standing_default', lang))}\n\n"
        "## Exact Next Step\n\n"
        f"{_section(sections.get('exact_next_step'), messages.msg('exact_next_step_default', lang))}\n\n"
        # 원문 꼬리가 문서의 **마지막**이다 — 다음 세션이 읽고 나서 마지막에 남는 것이
        # 직전 대화의 육성이 되게. 자동압축이 자연스러운 이유를 같은 자리에 재현한다.
        "## Recent Dialogue\n\n"
        f"{messages.msg('dialogue_note', lang)}\n\n"
        f"{dialogue_block}\n"
    )
    return front + body


def write_detail(tdir: Path, filename: str, body: str) -> Path:
    """신규 본문은 절대 기존 파일을 덮어쓰지 않는다."""
    tdir.mkdir(parents=True, exist_ok=True)
    path = tdir / filename
    if path.exists():
        raise FileExistsError(f"본문 파일이 이미 존재: {path}")
    atomicio.atomic_write_text(str(path), body)
    return path


def write_latest(tdir: Path, target_filename: str, summary: str) -> None:
    content = (
        f"# LATEST -> {target_filename}\n\n"
        f"[{target_filename}]({target_filename})\n\n"
        f"> {summary}\n"
    )
    atomicio.atomic_write_text(str(tdir / "LATEST.md"), content)


def detect_orphan(tdir: Path, latest_target: str | None, lang: str = "ko") -> str | None:
    """LATEST 가 가리키는 것보다 새 본문파일이 있으면 경고 문자열 (orphan, test 11)."""
    bodies = sorted(
        p.name for p in tdir.glob("*.md") if _BODY_FILE_RE.match(p.name)
    )
    if not bodies:
        return None
    newest = bodies[-1]
    if latest_target is None:
        return messages.msg("orphan_no_pointer", lang, newest=newest)
    if newest > latest_target:
        return messages.msg("orphan_stale_pointer", lang, latest_target=latest_target, newest=newest)
    return None


def _extract_section(body: str, heading: str, skip_quotes: bool = False) -> str:
    """`## <heading>` 섹션의 첫 의미 있는 줄을 한 줄로 돌려준다(마커·체크박스 제거).

    없거나 괄호형 placeholder(`(...)`)뿐이면 빈 문자열. CURRENT.md 집계 보조줄용
    (다음 행동·블로커 추출). 인덱스 역할 유지를 위해 한 줄만 뽑는다.

    `skip_quotes` 는 **CLI 가 인용을 만들어 넣는 절에만** 켠다. 지금은
    `Exact Next Step` 하나다 — 근거 발화 원문과 미승인 경고가 거기 `> ` 로 붙는다.
    다른 절의 인용은 사용자가 쓴 산문이므로 버리면 안 된다: 전 절에 켰더니
    `> CI 용량이 소진되어 배포가 막혔다` 같은 정당한 블로커가 인덱스에서 사라졌다
    (외부 리뷰 실측).
    """
    in_section = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            in_section = stripped[3:].strip() == heading
            continue
        if in_section and stripped:
            # **CLI 가 넣은 인용은 내용이 아니다** — 근거 발화 원문·미승인 경고가
            # `> ` 로 시작한다. 예전에는 `lstrip("-*> ")` 이 인용부호를 벗겨 그것들을
            # 본문처럼 뽑았고, Exact 절 맨 앞에 미승인 경고가 붙자 **인덱스의 「다음:」이
            # 실제 행동 대신 경고문을 싣게 됐다**(외부 리뷰 실측 · test_14 3건).
            if skip_quotes and stripped.startswith(">"):
                continue
            text = stripped.lstrip("-*> ").strip()
            text = re.sub(r"^\[[ xX]\]\s*", "", text)  # checkbox 마커 제거
            if not text:
                continue
            if text.startswith("(") and text.endswith(")"):
                return ""  # 괄호형 placeholder = 내용 없음
            return text
    return ""


def extract_section_block(body: str, heading: str) -> str:
    """`## <heading>` 섹션의 본문 전체를 돌려준다(다음 `## ` 헤딩 직전까지).

    `_extract_section` 은 한 줄만 뽑는 CURRENT.md 보조용이고, 이쪽은 변곡점 색인처럼
    절 전체가 필요한 소비자용이다. 없으면 빈 문자열.
    """
    lines: list[str] = []
    in_section = False
    for line in body.splitlines():
        if line.strip().startswith("## "):
            if in_section:
                break
            in_section = line.strip()[3:].strip() == heading
            continue
        if in_section:
            lines.append(line)
    return "\n".join(lines).strip()


#: 관계 토큰 — **닫힌 집합**. 3군으로 갈리고, 가르는 축은 「이전 것이 아직 살아 있나」다.
#: 목록을 키워서 갚지 않기 위해 예외는 `RELATES` 하나뿐이고, 같은 패턴이 3회 쌓이면
#: 토큰 후보로 올린다(감시 대장의 「회차를 세고 문턱에서 형태를 바꾼다」를 어휘에 적용).
RELATION_TOKENS = (
    # Ⓐ 이전 것이 죽는다
    "REVERSES", "SUPERSEDES", "ABANDONS",
    # Ⓑ 이전 것이 살아 있다
    "NARROWS", "DEFERS", "TRANSFERS", "CONFIRMS",
    # Ⓒ 이전 것이 안 끝났다
    "RETRIES", "CONTINUES", "SPLITS", "RESOLVES",
    # 예외 — 닫힌 집합에 안 맞을 때만. 산문 한 줄을 함께 적는다.
    "RELATES",
)

#: 이 토큰이 가리키면 대상 결정은 **무효**가 된다. 부정 색인이 이걸로 죽은 결정을 모은다.
#:
#: `RETRIES` 가 여기 있는 이유: 재시도당한 것은 **그 방법이 실패했다는 뜻**이므로 더 이상
#: 유효하지 않다. `SUPERSEDES` 와 죽는 결과는 같고 사유만 다르다(실패 vs 더 나은 길).
#: 이걸 빼두면 부정 색인이 실패한 시도를 못 잡아 「이거 해봤나?」에 답하지 못한다.
RELATION_KILLS = ("REVERSES", "SUPERSEDES", "ABANDONS", "RETRIES")

#: `**madi-r48f-D1**` 또는 `madi-r48f-D1` 로 시작하는 항목. `-L<n>` 은 교훈 ID.
# `\w` 는 Python 3 에서 유니코드다 — 한글 토픽(`topics.normalize_topic` 이 허용한다)이
# ASCII 문자군에 안 걸려 **관계가 조용히 사라지던** 자리다.
_DECISION_ID_RE = re.compile(
    r"^(?:#{1,6}\s+|[-*]?\s*)\*{0,2}([\w]+-[\w.\-]+-[DLSI]\d+)\*{0,2}")
_RELATION_RE = re.compile(
    r"\b(" + "|".join(RELATION_TOKENS) + r")\s*[:：]\s*([^\s,)\]]+)")


def parse_decisions(section_text: str) -> list[dict]:
    """`## Decisions` / `## Unapproved Proposals` 에서 **ID 가 붙은 항목만** 뽑는다.

    반환: `{"id", "text", "owner", "relations": [{"token", "target"}]}`.

    ID 가 없는 항목은 **건너뛴다.** 없는 ID 를 지어내면 색인이 거짓이 된다 — 옛 저장본은
    ID 가 없으므로 자연히 색인에 안 오르고, 그건 「소급 재작성 금지」의 결과다.

    **판단을 넣지 않는다.** 관계 토큰은 닫힌 집합과 문자 일치로만 잡고, 그 관계가 타당한지는
    보지 않는다.
    """
    items: list[dict] = []
    current: dict | None = None
    for raw in section_text.splitlines():
        match = _DECISION_ID_RE.match(raw)
        if match:
            if current is not None:
                items.append(current)
            rest = raw[match.end():].strip(" *:—-")
            current = {"id": match.group(1), "text": rest, "owner": "",
                       "relations": []}
        if current is None:
            continue
        for match in _RELATION_RE.finditer(raw):
            token, target = match.group(1), match.group(2)
            # `RELATES` 는 **산문 이유가 본체**다 — 그게 3회 쌓여 토큰 승격 후보가 된다.
            # 토큰과 대상만 뽑고 이유를 버리면 승격 심사할 재료가 안 남아 장치가 못 돈다.
            note = ""
            if token == "RELATES":
                tail = raw[match.end():]
                cut = min((tail.index(c) for c in ")]" if c in tail), default=len(tail))
                note = tail[:cut].strip(" ,—-:")
            rel = {"token": token, "target": target, "note": note}
            if rel not in current["relations"]:
                current["relations"].append(rel)
        if "누가:" in raw and not current["owner"]:
            # `chair(미승인)` 의 닫는 괄호를 자르면 안 된다 — 승인 여부가 그 안에 있다.
            current["owner"] = raw.split("누가:", 1)[1].strip(" *")
    if current is not None:
        items.append(current)
    return items


_INCIDENT_HEADING_RE = re.compile(
    r"^###(?!#)\s+\*{0,2}([\w]+-[\w.\-]+-I\d+)\*{0,2}(?=\s|$)")


def parse_incident_records(section_text: str) -> list[dict]:
    """유효한 ``### <ID>`` 사고 헤딩에서만 ID·관계 레코드를 만든다.

    사고의 다섯 칸은 보존해야 할 산문이다. 그 본문을 ``parse_decisions`` 로 훑으면 목록형
    ID나 ``RETRIES:`` 설명이 과거 ID 사전과 파생 색인에 섞인다. 이 함수가 사고 관계의
    유일한 구조 경계다: 정확한 3단계 헤딩, 완전형 ``-I<n>`` ID, 헤딩 한 줄의 관계만 읽는다.

    ``_unknown_relation_tokens`` 는 저장 게이트가 같은 레코드에서 닫힌 집합을 검사하도록
    함께 생산하는 내부 값이다. 공개 결정 색인은 이 내부 값은 내보내지 않는다.
    """
    records: list[dict] = []
    for heading in section_text.splitlines():
        if not _INCIDENT_HEADING_RE.match(heading):
            continue
        parsed = parse_decisions(heading)
        if not parsed:
            continue
        item = parsed[0]
        unknown: list[str] = []
        for group in re.findall(r"\(([^)]*)\)", heading):
            for token in re.findall(
                    r"(?:^|,)\s*([A-Za-z]+)\s*[:：]\s*[^\s,\)]+", group):
                if token not in RELATION_TOKENS:
                    unknown.append(token)
        item["_unknown_relation_tokens"] = unknown
        records.append(item)
    return records


def is_full_decision_id(value: str) -> bool:
    """`<프로젝트>-<토픽>-D<n>` 완전형인가."""
    return bool(_DECISION_ID_RE.match(value.strip()))


# 머리 형태 둘을 다 읽는다: 목록(`- **D1**`)과 헤딩(`### I1 — 제목`).
# 교훈·사고가 헤딩 형식을 쓰는데 목록만 읽던 탓에 **축약 ID 가 정규화되지 않고
# 색인·관계 토큰에서 통째로 빠졌다**(사고 대장 도입 때 실측).
_SHORT_ID_RE = re.compile(r"(?m)^(\s*(?:#{1,6}\s+|[-*]?\s*))\*{0,2}([DLSI]\d+)\*{0,2}(?=\s|$)")


def normalize_decision_ids_in_block(block: str, project: str, topic: str) -> str:
    """절 본문의 축약 ID(`**D1**`)를 완전형으로 바꾼다. 이미 완전형이면 그대로.

    어댑터가 같은 문서 안에서 `D1` 로 줄여 써도 되게 한 대신, **저장 시 CLI 가 정규화**한다.
    안 하면 색인이 `D1` 을 여러 토픽에서 같은 것으로 보게 된다.
    """
    if not block:
        return block

    def sub(match):
        return f"{match.group(1)}**{decision_id_prefix(project, topic)}-{match.group(2)}**"

    block = _SHORT_ID_RE.sub(sub, block)

    # 어댑터는 이전 판본을 보고 **옛 접두 완전형**을 그대로 옮겨 적는다. 축약만
    # 정규화하면 그 경로가 통째로 빠진다 — 관계 토큰의 target 도 같이 새는 자리다.
    aliases = decision_id_prefix_aliases(project, topic)
    for legacy in aliases[1:]:
        block = _legacy_id_re(legacy).sub(
            lambda m: f"{aliases[0]}-{m.group(1)}", block)
    return block


def decision_id_prefix(project: str, topic: str) -> str:
    """ID 앞머리. **토픽이 이미 프로젝트명으로 시작하면 겹쳐 쓰지 않는다.**

    실측: 프로젝트 `madi` + 토픽 `madi-operational-floor` → `madi-madi-operational-floor-D1`.
    기능엔 지장이 없으나 읽기 나쁘고, ID 는 사람이 인용하는 값이라 짧을수록 낫다.
    """
    if topic == project or topic.startswith(f"{project}-"):
        return topic
    return f"{project}-{topic}"


def decision_id_prefix_aliases(project: str, topic: str) -> tuple[str, ...]:
    """이 토픽을 가리켜 온 접두 전부. **첫 값이 정규형이다.**

    v0.4.0 이전에는 접두가 언제나 `{project}-{topic}` 이었다. 토픽이 프로젝트명으로
    시작하면 `madi-madi-operational-floor-D1` 처럼 겹쳐 읽혀서 그 규칙을 바꿨는데,
    **이미 저장된 판본에는 옛 접두가 그대로 남는다.**

    두 접두를 서로 다른 결정으로 보면 재개가 같은 결정을 두 벌 싣는다 — 실측으로
    madi 재개가 44건을 실었고 고유한 것은 25건이었다(`D1`~`D19` 가 전부 두 벌).

    그래서 판독기에 「두 이름을 같게 보라」를 넣지 않는다. 그건 조항을 하나 더 얹는
    것이고, 판독 쪽 두 함수는 이미 P-C 중단 신호가 난 자리다. 대신 **저장 입구에서
    한 형식으로 접는다.** 정본이 하나면 읽는 쪽은 아무것도 몰라도 된다.
    """
    canonical = decision_id_prefix(project, topic)
    legacy = f"{project}-{topic}"
    return (canonical,) if legacy == canonical else (canonical, legacy)


#: ID 를 이루는 글자. 앞뒤에 이게 붙어 있으면 **더 긴 ID 의 일부**이지 이 ID 가 아니다.
#: `\w` 는 Python 3 에서 유니코드다 — 한글 토픽이 여기 걸린다(이 코드베이스는 허용한다).
_ID_CHAR = r"[\w.-]"


def _legacy_id_re(prefix: str) -> "re.Pattern[str]":
    """옛 접두가 붙은 완전형 ID 를 잡는 정규식. 번호는 그룹 1 로 돌려준다.

    `\\b` 로는 못 막는다 — `-` 와 `.` 이 단어 경계라 **다른 프로젝트 ID 안에서 매치가
    시작한다.** 실측(외부 리뷰): 이 토픽이 `demo`/`demo-work` 일 때 남의 관계 대상
    `other-demo-demo-work-D7` 이 `other-demo-work-D7` 로 바뀌어, 관계가 조용히 엉뚱한
    결정을 가리켰다.

    그래서 경계를 「단어」가 아니라 **「ID 를 이루는 글자」**로 잡는다. 앞뒤에 ID 글자가
    붙어 있으면 더 긴 ID 의 일부이므로 건드리지 않는다.
    """
    return re.compile(
        r"(?<!" + _ID_CHAR + r")" + re.escape(prefix)
        + r"-([DLSI]\d+)(?!" + _ID_CHAR + r")")


def fold_legacy_decision_id(value: str, project: str, topic: str) -> str:
    """완전형 ID 의 접두가 옛 별칭이면 정규형으로 바꾼다. 아니면 그대로."""
    aliases = decision_id_prefix_aliases(project, topic)
    for legacy in aliases[1:]:
        if value.startswith(f"{legacy}-"):
            return f"{aliases[0]}-{value[len(legacy) + 1:]}"
    return value


def normalize_decision_id(raw: str, project: str, topic: str) -> str:
    """`D1` 같은 축약을 완전형으로. 완전형이면 접두만 정규형으로 접는다."""
    value = raw.strip().strip("*")
    if _DECISION_ID_RE.match(value):
        return fold_legacy_decision_id(value, project, topic)
    if re.fullmatch(r"[DLSI]\d+", value):
        return f"{decision_id_prefix(project, topic)}-{value}"
    return value


def iter_topic_dirs(root: str, include_archived: bool = False):
    """(tdir, topic, archived) 를 토픽명 순으로 낸다. 읽기 전용."""
    base = Path(root) / ".handoff"
    if not base.is_dir():
        return
    scopes = [(base, False)]
    if include_archived and (base / "archived").is_dir():
        scopes.append((base / "archived", True))
    for scope, archived in scopes:
        for tdir in sorted(p for p in scope.iterdir() if p.is_dir()):
            if not archived and tdir.name == "archived":
                continue
            yield tdir, tdir.name, archived


def iter_body_files(root: str, include_archived: bool = False):
    """(topic, archived, path) 를 토픽명·파일명 순으로 낸다. 읽기 전용.

    **체인에 없는 고아 본문도 낸다** — 동시 저장에서 진 저장본이 여기 섞인다. 결정 색인은
    그래서 이걸 쓰지 않고 `_chain_bodies` 를 쓴다(같은 ID 오염 방지).
    """
    for tdir, topic, archived in iter_topic_dirs(root, include_archived):
        for path in sorted(p for p in tdir.glob("*.md") if _BODY_FILE_RE.match(p.name)):
            yield topic, archived, path


class TopicSummary:
    """INDEX/CURRENT 집계용 토픽 1건."""

    def __init__(self, topic: str, group: str, summary: str, date: str,
                 latest_target: str | None, archived: bool, warning: str | None,
                 next_step: str = "", blocker: str = ""):
        self.topic = topic
        self.group = group  # active|waiting|watching|done
        self.summary = summary
        self.date = date
        self.latest_target = latest_target
        self.archived = archived
        self.warning = warning
        self.next_step = next_step  # CURRENT.md 보조줄 (## Exact Next Step)
        self.blocker = blocker      # CURRENT.md 보조줄 (## Blockers And Questions)


def _read_topic_summary(root: str, tdir: Path, archived: bool, lang: str = "ko") -> TopicSummary | None:
    if not tdir.is_dir():
        return None
    # `.handoff/` 아래에는 토픽 외에도 round evidence·brief·artifact bundle 디렉터리가
    # 함께 존재할 수 있다. 토픽의 정식 진입점은 LATEST.md 이므로, 그것이 없는 디렉터리를
    # status=active·요약 없음인 가짜 토픽으로 INDEX/CURRENT에 올리지 않는다.
    if not (tdir / "LATEST.md").is_file():
        return None
    target = read_latest_target(tdir)
    detail_text = ""
    raw_status = None
    summary = ""
    date = ""
    next_step = ""
    blocker = ""
    if target and (tdir / target).exists():
        # 손상된 본문 하나가 목록·색인 전체를 죽이면 안 된다 — 읽기 실패는
        # 「내용 없음」으로 낮춰 읽고, 그 사실은 저장 게이트가 따로 소리 낸다.
        try:
            detail_text = (tdir / target).read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            detail_text = ""
        front, body = parse_frontmatter(detail_text)
        raw_status = front.get("status")
        summary = front.get("summary", "")
        created = front.get("created", "")
        date = created[:16].replace("T", " ") if created else ""
        if not summary:
            for line in body.splitlines():
                if line.strip().startswith(">"):
                    summary = line.strip().lstrip("> ").strip()
                    break
        next_step = _extract_section(body, "Exact Next Step", skip_quotes=True)
        blocker = _extract_section(body, "Blockers And Questions")
        # 언어중립 기본값 판정: ko/en 어느 언어로 저장된 본문이든
        # "블로커 없음" 계열 placeholder 는 상수집합(messages.BLOCKER_DEFAULTS) 비교로
        # 빈 값 처리한다 — ko 문자열 substring 의존을 제거.
        if blocker in messages.BLOCKER_DEFAULTS:
            blocker = ""
        if not date and target:
            date = target[:10]
    elif target:
        date = target[:10]

    # 라이브 INDEX 호환: frontmatter status 부재 시 포인터/요약 텍스트의 종료신호
    # (CLOSED/closed/done) 를 fallback 으로 읽는다.
    if raw_status is None:
        latest_text = ""
        latest_file = tdir / "LATEST.md"
        if latest_file.exists():
            try:
                latest_text = latest_file.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                latest_text = ""
        haystack = f"{latest_text}\n{summary}"
        if re.search(r"\b(CLOSED|closed|done)\b", haystack):
            raw_status = "done"

    group, warning = status_mod.normalize_status(raw_status, lang)
    return TopicSummary(tdir.name, group, summary or messages.msg("index_no_summary", lang),
                        date or "", target, archived, warning,
                        next_step=next_step, blocker=blocker)


def scan_topics(root: str, include_archived: bool = False, lang: str = "ko") -> list[TopicSummary]:
    """active(+선택적 archived) 토픽 요약을 LATEST 스캔으로 수집."""
    base = handoff_root(root)
    out: list[TopicSummary] = []
    if base.is_dir():
        for child in sorted(base.iterdir()):
            if not child.is_dir() or child.name == "archived" or child.name.startswith("."):
                continue
            summary = _read_topic_summary(root, child, archived=False, lang=lang)
            if summary:
                out.append(summary)
    if include_archived:
        arch = base / "archived"
        if arch.is_dir():
            for child in sorted(arch.iterdir()):
                if not child.is_dir():
                    continue
                summary = _read_topic_summary(root, child, archived=True, lang=lang)
                if summary:
                    out.append(summary)
    return out


def regenerate_index(root: str, lang: str = "ko") -> None:
    """INDEX.md 를 LATEST 스캔으로 재생성 (active + archived). 자동 archive 이동 없음."""
    topics = scan_topics(root, include_archived=True, lang=lang)
    lines = [messages.msg("index_title", lang), "", "| Topic | Status | Date | Summary |", "|---|---|---|---|"]
    for summary in topics:
        status_label = summary.group
        if not summary.archived and summary.group == "done":
            status_label = messages.msg("index_status_archive_suggested", lang)
        elif summary.archived:
            status_label = messages.msg("index_status_archived", lang, group=summary.group)
        lines.append(
            f"| {summary.topic} | {status_label} | {summary.date} | {summary.summary} |"
        )
    text = "\n".join(lines) + "\n"
    atomicio.atomic_write_text(str(handoff_root(root) / "INDEX.md"), text)
