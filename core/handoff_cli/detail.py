"""토픽 상세 본문 · LATEST.md · INDEX.md (프로젝트 정본).

핵심 동작: Body Template 10섹션, detail frontmatter 9키(status 는 4-value),
동시저장 보존, orphan 감지, INDEX 재생성 우선순위(status·summary·date precedence,
archive suggested 무자동이동), LATEST 포인터 표준/레거시 변형 독해.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from pathlib import Path

from . import atomicio, messages, status as status_mod

__all__ = [
    "topic_dir",
    "parse_frontmatter",
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


def read_latest_target(tdir: Path) -> str | None:
    """LATEST.md 가 가리키는 본문 파일명을 돌려준다.

    표준(`# LATEST -> file.md`), 화살표(`→`), 레거시 링크(`→ [f.md](f.md)`) 모두
    독해. 포인터가 없으면(레거시 본문형) None.
    """
    latest = tdir / "LATEST.md"
    if not latest.exists():
        return None
    text = latest.read_text(encoding="utf-8")
    arrow = _LATEST_ARROW_RE.search(text)
    if arrow:
        cand = arrow.group(1).strip()
        if cand.endswith(".md") and not cand.startswith("["):
            return Path(cand).name
    link = _LATEST_LINK_RE.search(text)
    if link:
        return Path(link.group(2)).name
    return None


def detail_filename(now: datetime, existing: set[str]) -> str:
    """`YYYY-MM-DD-HHMMSS-<uuid8>.md`. 충돌 시 새 UUID 로 재시도."""
    stamp = now.strftime("%Y-%m-%d-%H%M%S")
    while True:
        suffix = uuid.uuid4().hex[:8]
        name = f"{stamp}-{suffix}.md"
        if name not in existing:
            return name


def _section(value: str | None, empty: str) -> str:
    value = (value or "").strip()
    return value if value else empty


def assemble_body(meta: dict, sections: dict, files_touched: list, created_human: str,
                  lang: str = "ko") -> str:
    """Body Template 10섹션을 바이트 결정적으로 조립한다.

    섹션 헤딩(`## Done` 등)·frontmatter 키·파일명 규칙은 언어 무관 불변 — 번역
    대상은 각 섹션의 기본값(placeholder)·Git State 라인·Files Touched 빈 값
    문구뿐이다.
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

    front = (
        "---\n"
        f"topic: {meta['topic']}\n"
        f"created: {meta['created']}\n"
        f"project_root: {meta['project_root']}\n"
        f"status: {meta['status']}\n"
        f"prev: {meta['prev'] if meta['prev'] else 'null'}\n"
        f"source: {meta['source']}\n"
        f"git_branch: {git['branch'] if git['branch'] else 'null'}\n"
        f"git_commit: {git['commit'] if git['commit'] else 'null'}\n"
        f"git_dirty: {('true' if git['dirty'] else 'false') if git['is_git'] else 'null'}\n"
        "---\n"
    )

    summary = meta.get("summary") or meta["topic"]
    body = (
        f"\n# Handoff: {meta['topic']} - {created_human}\n\n"
        f"> {summary}\n\n"
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
        f"{_section(sections.get('decisions'), messages.msg('decisions_default', lang))}\n\n"
        "## Exact Next Step\n\n"
        f"{_section(sections.get('exact_next_step'), messages.msg('exact_next_step_default', lang))}\n\n"
        "## Verification\n\n"
        f"{_section(sections.get('verification'), messages.msg('verification_default', lang))}\n"
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
    """LATEST 가 가리키는 것보다 새 본문파일이 있으면 경고 문자열 (orphan)."""
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


def _extract_section(body: str, heading: str) -> str:
    """`## <heading>` 섹션의 첫 의미 있는 줄을 한 줄로 돌려준다(마커·체크박스 제거).

    없거나 괄호형 placeholder(`(...)`)뿐이면 빈 문자열. CURRENT.md 집계 보조줄용
    (다음 행동·블로커 추출). 인덱스 역할 유지를 위해 한 줄만 뽑는다.
    """
    in_section = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            in_section = stripped[3:].strip() == heading
            continue
        if in_section and stripped:
            text = stripped.lstrip("-*> ").strip()
            text = re.sub(r"^\[[ xX]\]\s*", "", text)  # checkbox 마커 제거
            if not text:
                continue
            if text.startswith("(") and text.endswith(")"):
                return ""  # 괄호형 placeholder = 내용 없음
            return text
    return ""


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
    target = read_latest_target(tdir)
    detail_text = ""
    raw_status = None
    summary = ""
    date = ""
    next_step = ""
    blocker = ""
    if target and (tdir / target).exists():
        detail_text = (tdir / target).read_text(encoding="utf-8")
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
        next_step = _extract_section(body, "Exact Next Step")
        blocker = _extract_section(body, "Blockers And Questions")
        # 언어중립 기본값 판정: ko/en 어느 언어로 저장된 본문이든 "블로커 없음" 계열
        # placeholder 는 상수집합(messages.BLOCKER_DEFAULTS) 비교로 빈 값 처리한다 —
        # ko 문자열 substring 의존을 제거.
        if blocker in messages.BLOCKER_DEFAULTS:
            blocker = ""
        if not date and target:
            date = target[:10]
    elif target:
        date = target[:10]

    # frontmatter status 부재 시(레거시 본문) 포인터/요약 텍스트의 종료신호
    # (CLOSED/closed/done) 를 fallback 으로 읽는다.
    if raw_status is None:
        latest_text = ""
        latest_file = tdir / "LATEST.md"
        if latest_file.exists():
            latest_text = latest_file.read_text(encoding="utf-8")
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
