"""글로벌 CURRENT.md = 프로젝트당 1개 전 토픽 집계 인덱스.

파생물. 상세 정본에서 언제든 재생성 가능. 단순 덮어쓰기 금지 — 전 active 토픽
재스캔 후 재생성. writers set 머지, 최근변경 5개 롤링, secret 차단, 크로스호스트
가드(네트워크 없음), 원자교체.
"""

from __future__ import annotations

import re
import socket
from pathlib import Path

from . import atomicio, detail, guard, messages, repo, status as status_mod

__all__ = ["resolve_global_dir", "regenerate_current", "GlobalResult"]

_MAX_RECENT = 5
_VALID_WRITERS = ("claude-code", "codex")
_MAX_AGG_LINE = 200  # 보조줄 길이 상한 — 인덱스 역할 유지(본문 통째 복제 금지)


def _clip(text: str) -> str:
    """집계 보조줄을 한 줄·길이 상한으로 자른다."""
    text = " ".join(text.split())
    return text if len(text) <= _MAX_AGG_LINE else text[: _MAX_AGG_LINE - 1].rstrip() + "…"


class GlobalResult:
    def __init__(self, written: bool, path: str | None, mode: str,
                 warnings: list[str], skipped_reason: str | None):
        self.written = written
        self.path = path
        self.mode = mode  # new|normal|rename|divergent|skipped
        self.warnings = warnings
        self.skipped_reason = skipped_reason


def handoffs_base(global_root: str) -> Path:
    return Path(global_root) / "handoffs"


def _read_header_project_id(current_path: Path) -> str | None:
    if not current_path.exists():
        return None
    text = current_path.read_text(encoding="utf-8")
    front, _ = detail.parse_frontmatter(text)
    return front.get("project_id")


def resolve_global_dir(global_root: str, name: str, project_id: str,
                       lang: str = "ko") -> tuple[Path, str, str | None]:
    """글로벌 폴더를 결정한다. `(dir, mode, warning)`.

    mode: new(신규) | normal(이름일치) | rename(id일치·이름다름) | divergent(id충돌→abort).
    project_id 로 기존 폴더를 매칭해 rename 을 감지하고, basename 형제폴더를 새로
    만들지 않는다.
    """
    base = handoffs_base(global_root)
    preferred = base / name

    # 모든 글로벌 폴더에서 project_id 매칭 탐색.
    matched: Path | None = None
    if base.is_dir():
        for child in sorted(base.iterdir()):
            if not child.is_dir():
                continue
            cid = _read_header_project_id(child / "CURRENT.md")
            if cid and cid == project_id:
                matched = child
                break

    if matched is not None:
        if matched.name == name:
            return matched, "normal", None
        return matched, "rename", messages.msg(
            "warn_rename_suggested", lang, matched_name=matched.name, name=name)

    # id 매칭 없음. preferred 가 이미 있고 **실제 다른 id** 면 divergent.
    # project_id 헤더가 없는 레거시 CURRENT.md(None)는 divergent 가 아니라
    # 덮어쓰기 허용(legacy 마이그레이션)으로 처리한다.
    if preferred.exists():
        existing_id = _read_header_project_id(preferred / "CURRENT.md")
        if existing_id and existing_id != project_id:
            return preferred, "divergent", messages.msg(
                "warn_divergent_project_id", lang, project_id=project_id, existing_id=existing_id)
        if existing_id is None:
            return preferred, "normal", messages.msg("warn_legacy_project_id", lang)
    return preferred, "new", None


def _parse_recent(text: str) -> list[str]:
    """`## 최근 변경`/`## Recent Changes` 아래 항목을 읽는다.

    프로젝트가 lang 을 바꿔 재생성했을 수 있으므로 두 언어 헤딩 모두 인식한다.
    """
    heading = None
    for candidate in (h["current_recent_heading"] for h in messages._MESSAGES.values()):
        if candidate in text:
            heading = candidate
            break
    if heading is None:
        return []
    after = text.split(heading, 1)[1]
    out: list[str] = []
    for line in after.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            break
        if stripped.startswith("- "):
            out.append(stripped)
    return out


def _parse_writers(text: str) -> list[str]:
    # writers 는 멀티라인 리스트 — frontmatter 단순파서가 못 잡으므로 직접 스캔.
    writers: list[str] = []
    in_block = False
    for line in text.splitlines():
        if re.match(r"^writers:\s*$", line):
            in_block = True
            continue
        if in_block:
            m = re.match(r"^\s*-\s*(\S+)\s*$", line)
            if m:
                writers.append(m.group(1))
            else:
                break
    return [w for w in writers if w in _VALID_WRITERS]


def _build_text(name: str, project_id: str, meta: dict,
                topics: list[detail.TopicSummary], recent: list[str],
                writers: list[str], lang: str = "ko") -> tuple[str, str]:
    """`(header, body)`. 헤더는 CLI 생성 구조 메타라 secret 스캔 제외, body 만 스캔한다.

    (헤더의 `written_at_commit` 40자 SHA 가 hex>32 secret 패턴에 오검출되어 헤더가
    파괴되는 것을 막기 위해 분리한다.)
    """
    active = [t for t in topics if t.group in status_mod.ACTIVE_GROUPS]
    detail_docs = [f".handoff/{t.topic}/LATEST.md" for t in active]
    headings = messages.GROUP_HEADINGS.get(lang, messages.GROUP_HEADINGS["en"])

    header_lines = [
        "---",
        f"project: {name}",
        f"project_id: {project_id}",
        f"updated_at: {meta['updated_at']}",
        f"host: {meta['host']}",
        f"written_at_commit: {meta['written_at_commit']}",
        f"written_at_mtime: {meta['written_at_mtime']}",
        f"branch: {meta['branch']}",
        "writers:",
    ]
    for writer in writers:
        header_lines.append(f"  - {writer}")
    header_lines.append("detail_docs:")
    if detail_docs:
        for doc in detail_docs:
            header_lines.append(f"  - {doc}")
    header_lines.append("---")

    body_lines = [
        "",
        messages.msg("current_title", lang, name=name),
        "",
        messages.msg("current_notice", lang),
        "",
    ]
    for group in status_mod.ACTIVE_GROUPS:
        members = [t for t in active if t.group == group]
        if not members:
            continue
        body_lines.append(headings[group])
        for t in members:
            body_lines.append(f"- **{t.topic}** — {t.summary}  ·  `.handoff/{t.topic}/LATEST.md`")
            if getattr(t, "next_step", ""):
                body_lines.append(f"    {messages.msg('current_next_prefix', lang)}{_clip(t.next_step)}")
            if getattr(t, "blocker", ""):
                body_lines.append(f"    {messages.msg('current_blocker_prefix', lang)}{_clip(t.blocker)}")
        body_lines.append("")

    body_lines.append(messages.msg("current_recent_heading", lang))
    if recent:
        body_lines.extend(recent)
    else:
        body_lines.append(messages.msg("current_recent_empty", lang))
    body_lines.append("")

    return "\n".join(header_lines), "\n".join(body_lines)


def regenerate_current(global_root: str, name: str, project_id: str, root: str,
                       meta: dict, *, recent_entry: str | None,
                       add_writers: list[str], lang: str = "ko") -> GlobalResult:
    """전 active 토픽을 집계해 글로벌 CURRENT.md 를 재생성한다.

    호출 시점엔 상세 정본은 이미 저장됨(분리 실패 경계). 여기서 무엇이
    실패/skip 해도 상세는 롤백되지 않는다.

    - `recent_entry`: `## 최근 변경`/`## Recent Changes` 에 prepend 할 1줄(save 경로).
      `None` 이면 추가 안 함(reindex 백필 — 새 변경이 아니므로 멱등 유지).
    - `add_writers`: 이번 갱신에 합쳐 넣을 writer 들(⊆ {claude-code, codex}).
    - `lang`: 이번 갱신에 쓸 장식 텍스트 언어(그룹 헤딩·안내문·보조줄 prefix 등).
    """
    warnings: list[str] = []
    gdir, mode, gwarn = resolve_global_dir(global_root, name, project_id, lang)
    if gwarn:
        warnings.append(gwarn)
    current_path = gdir / "CURRENT.md"

    if mode == "divergent":
        return GlobalResult(False, str(current_path), "divergent", warnings,
                            "divergent project_id")

    # 크로스호스트 가드 (읽기 전용, 네트워크 없음).
    existing_text = current_path.read_text(encoding="utf-8") if current_path.exists() else ""
    if existing_text and guard.has_conflict_markers(existing_text):
        warnings.append(messages.msg("warn_conflict_marker", lang))
        return GlobalResult(False, str(current_path), "skipped", warnings, "conflict markers")
    pid_global = handoffs_base(global_root).parent  # writer-local root, e.g. ~/.claude or ~/.codex
    if guard.remote_ahead(str(pid_global)):
        warnings.append(messages.msg("warn_remote_ahead", lang, pid_global=pid_global))
        return GlobalResult(False, str(current_path), "skipped", warnings, "remote ahead")

    # 집계 재생성.
    topics = detail.scan_topics(root, include_archived=False, lang=lang)
    recent = _parse_recent(existing_text)
    # recent_entry 가 있고 직전 항목과 다르면 prepend — 무변경 재저장/백필 idempotence 유지.
    if recent_entry and (not recent or recent[0] != recent_entry):
        recent = [recent_entry] + recent
    recent = recent[:_MAX_RECENT]

    writers = _parse_writers(existing_text)
    for writer in add_writers:
        if writer in _VALID_WRITERS and writer not in writers:
            writers.append(writer)
    writers = sorted(set(writers))[:2]

    header, body = _build_text(name, project_id, meta, topics, recent, writers, lang)
    # 구조 라인(제목 `# `, 고정 안내문 `> `, 그룹 헤딩 `## `)은 CLI 생성물이라 스캔 제외.
    # 사용자 content(토픽 요약 `- **`, 최근변경 `- YYYY`)는 계속 스캔된다.
    redacted_body, sec_warnings = guard.redact_secrets(body, skip_prefixes=("# ", "> ", "## "), lang=lang)
    warnings.extend(sec_warnings)

    atomicio.atomic_write_text(str(current_path), header + "\n" + redacted_body)
    return GlobalResult(True, str(current_path), mode, warnings, None)


def build_meta(root: str, git: dict, updated_at: str) -> dict:
    return {
        "updated_at": updated_at,
        "host": socket.gethostname(),
        "written_at_commit": git["commit"] if git["commit"] else "none",
        "written_at_mtime": (repo.latest_project_mtime(root) or "none") if not git["is_git"] else "none",
        "branch": git["branch"] if git["branch"] else "none",
    }
