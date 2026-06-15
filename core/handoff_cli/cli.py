"""명령 디스패치 — 어댑터가 호출하는 공용 진입점.

모든 파일쓰기는 여기(및 하위 모듈)에서만 일어난다. 어댑터는 구조화 입력(dict/JSON)을
넘기고 결과 dict 를 받아 사용자에게 보고만 한다.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import current, detail, messages, repo, status as status_mod, topics

__all__ = ["cmd_save", "cmd_list", "cmd_find", "cmd_resume", "cmd_archive", "cmd_reindex"]


_VALID_SOURCES = ("claude-code", "codex")


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


def _resume_prompt(project_name: str, root: str, topic: str, summary: str, lang: str) -> str:
    """새 세션에 그대로 붙여넣어 이어가는 프롬프트. 결정적(같은 입력→바이트 동일).

    프로젝트명 우선 + 절대경로는 힌트(크로스머신). summary 는 공백·개행을 1줄로
    접고, 사용자 미입력(=topic 폴백)이면 요약 줄을 생략한다. lang 에 따라 ko/en
    스켈레톤을 고른다.
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
        messages.msg("resume_tail1", lang, topic=topic),
        messages.msg("resume_tail2", lang),
        messages.msg("resume_tail3", lang),
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

    meta = {
        "topic": topic,
        "created": created_iso,
        "project_root": root,
        "status": status_val,
        "prev": prev,
        "source": source,
        "summary": summary,
        "git": git,
    }
    body = detail.assemble_body(meta, payload.get("sections", {}),
                               payload.get("files_touched", []), created_human, lang)

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

    # 동시성: 본문 저장 사이 LATEST 가 바뀌었으면 포인터 갱신 중단.
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

    # 글로벌 CURRENT.md — 분리 실패 경계: 실패해도 상세는 보존.
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
    생성한다. 멱등(같은 입력 2회 → updated_at 외 바이트 동일). active 토픽이
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
            except OSError:
                continue
            if needle in text.lower():
                matches.append({"root": str(base.parent), "path": str(path)})
    return _result("find", resolved, name, repo.read_project_id(resolved), [],
                   {"keyword": keyword, "matches": matches, "read_only": True})


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

    # git drift 비교 (resume 게이트 보존).
    drift = None
    git = repo.git_meta(resolved)
    if git["is_git"] and front.get("git_commit") and front["git_commit"] != "null":
        saved_commit = front.get("git_commit") or ""
        cur_commit = git["commit"] or ""
        # 레거시 detail 은 short SHA 를 기록했다 — prefix 일치면 같은 커밋으로 본다
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

    prev_chain = []
    prev = front.get("prev")
    hops = 0
    while prev and prev != "null" and hops < 2 and (tdir / prev).exists():
        prev_chain.append(prev)
        ptext = (tdir / prev).read_text(encoding="utf-8")
        pfront, _ = detail.parse_frontmatter(ptext)
        prev = pfront.get("prev")
        hops += 1

    return _result("resume", resolved, name, repo.read_project_id(resolved), warnings, {
        "found": True,
        "broken": False,
        "detail_path": _rel(resolved, body_path),
        "status": front.get("status"),
        "git_drift": drift,
        "prev_chain": prev_chain,
        "body": body,
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
