"""프로젝트 식별 · git/시간 실측.

모든 git 호출은 `run_git` 단일 경로를 거친다. `run_git` 은 네트워크 서브커맨드
(fetch/pull/push 등)를 거부한다 — `/handoff` 는 네트워크를 일절 쓰지 않는다.
시각은 `now_local()` 단일 경로로 실측하며, 테스트는 이 함수를 monkeypatch 해
provenance 를 검증한다. 타임존은 시스템 로컬 — env·설정 불필요, `datetime.now().astimezone()`
이 OS 로컬 오프셋을 자동으로 붙인다.
"""

from __future__ import annotations

import os
import re
import subprocess
import uuid
from datetime import datetime
from pathlib import Path

from . import atomicio

__all__ = [
    "now_local",
    "iso8601",
    "run_git",
    "GitNetworkError",
    "resolve_root",
    "project_name",
    "ensure_project_id",
    "read_project_id",
    "git_meta",
    "project_id_uncommitted",
    "latest_project_mtime",
]

# git 네트워크 서브커맨드 차단 목록 (producer 는 네트워크 없음).
# 원격 전송을 유발할 수 있는 저수준/우회 커맨드까지 포함.
_NETWORK_GIT = {
    "fetch", "pull", "push", "clone", "remote", "ls-remote",
    "submodule", "archive", "bundle", "send-pack", "receive-pack", "credential",
}

PROJECT_MARKERS = (".git", "package.json", "pyproject.toml", "Cargo.toml", "AGENTS.md", "CLAUDE.md")


class GitNetworkError(RuntimeError):
    """네트워크 git 서브커맨드 호출 시도 — handoff producer 에서 금지."""


def now_local() -> datetime:
    """현재 시각을 시스템 로컬 타임존으로 실측한다.

    (테스트가 monkeypatch 하는 단일 시계 경로.) env·설정 불필요 — OS 가 보고하는
    로컬 오프셋을 그대로 쓴다(예: KST +09:00, PST -08:00 등). 하드코딩된 고정
    타임존 상수는 없다.
    """
    return datetime.now().astimezone()


def iso8601(dt: datetime) -> str:
    """full ISO 8601 + 로컬 오프셋 (`2026-06-05T22:51:00+09:00`). 초까지, 마이크로초 절삭.

    `dt` 가 naive 면 로컬 타임존을 붙이고, aware 면 그 타임존 표현을 그대로 유지한다.
    """
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.replace(microsecond=0).isoformat()


def run_git(root: str, *args: str) -> subprocess.CompletedProcess:
    """`git -C <root> <args>` 실행. 네트워크 서브커맨드는 거부한다."""
    if args and args[0] in _NETWORK_GIT:
        raise GitNetworkError(f"네트워크 git 호출 금지: {args[0]}")
    return subprocess.run(
        ["git", "-C", root, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _is_git_repo(root: str) -> bool:
    proc = run_git(root, "rev-parse", "--is-inside-work-tree")
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def resolve_root(cwd: str, explicit_root: str | None) -> str:
    """저장/탐색 루트를 결정한다 (Project Root Resolution 규칙).

    1. `--root` 가 있으면 그 절대경로.
    2. cwd 에 프로젝트 마커가 있으면 cwd.
    3. git toplevel 이 성공하면 그 루트.
    4. 둘 다 없으면 cwd.
    상위 폴더로의 자동 승격은 하지 않는다 (Rule: 하위 프로젝트에서 상위 폴더로 승격 금지).
    """
    if explicit_root:
        if "\x00" in explicit_root:
            raise ValueError("--root 에 null byte 가 있다.")
        # 상위탈출 거부: 원시 입력에 `..` 경로 컴포넌트가 있으면 거부.
        # 정상 명시 루트(절대경로)는 `..` 가 없다.
        parts = re.split(r"[\\/]+", explicit_root)
        if ".." in parts:
            raise ValueError("--root 에 상위탈출(`..`) 컴포넌트가 있다.")
        return str(Path(explicit_root).resolve())
    cwd_path = Path(cwd).resolve()
    for marker in PROJECT_MARKERS:
        if (cwd_path / marker).exists():
            return str(cwd_path)
    proc = run_git(str(cwd_path), "rev-parse", "--show-toplevel")
    if proc.returncode == 0 and proc.stdout.strip():
        return str(Path(proc.stdout.strip()).resolve())
    return str(cwd_path)


def project_name(root: str) -> str:
    """글로벌 폴더명 = 프로젝트 폴더 basename (사람이 읽음)."""
    return Path(root).name


def _project_id_path(root: str) -> Path:
    return Path(root) / ".handoff" / ".project-id"


def read_project_id(root: str) -> str | None:
    """`.handoff/.project-id` 를 읽는다. 없으면 None. **생성하지 않는다(read-only).**"""
    path = _project_id_path(root)
    if path.exists():
        value = path.read_text(encoding="utf-8").strip()
        return value or None
    return None


def ensure_project_id(root: str) -> str:
    """save 경로 전용: `.project-id` 가 없으면 최초 1회 UUID 생성·고정. 있으면 읽는다."""
    existing = read_project_id(root)
    if existing:
        return existing
    new_id = str(uuid.uuid4())
    atomicio.atomic_write_text(str(_project_id_path(root)), new_id + "\n")
    return new_id


def project_id_uncommitted(root: str) -> bool:
    """git 저장소에서 `.project-id` 가 아직 커밋 안 됨(untracked/staged-only)인지 여부.

    비-git 이면 False. **HEAD 에 커밋돼 있을 때만** False, 그 외(untracked·staged-only·
    아직 커밋 0개)는 True. `ls-files` 는 staged 도 추적으로 보므로 `ls-tree HEAD` 로
    실제 커밋 여부를 본다.
    """
    if not _is_git_repo(root):
        return False
    proc = run_git(root, "ls-tree", "HEAD", "--", ".handoff/.project-id")
    if proc.returncode != 0:
        return True  # HEAD 없음(커밋 0개) 등 → 미커밋.
    return proc.stdout.strip() == ""


def git_meta(root: str) -> dict:
    """branch / full commit SHA / dirty 를 실측한다 (비-git 이면 None 들)."""
    if not _is_git_repo(root):
        return {"is_git": False, "branch": None, "commit": None, "dirty": None, "dirty_count": None}
    # 커밋이 0개인 갓 init 한 레포: rev-parse 가 실패하며 stdout 에 리터럴 'HEAD' 가
    # 남을 수 있으므로 returncode 를 확인해 None 처리한다.
    commit_proc = run_git(root, "rev-parse", "HEAD")
    commit = commit_proc.stdout.strip() if commit_proc.returncode == 0 else None
    branch_proc = run_git(root, "rev-parse", "--abbrev-ref", "HEAD")
    branch = branch_proc.stdout.strip() if branch_proc.returncode == 0 else None
    if branch == "HEAD":  # detached/무커밋 — 의미있는 브랜치명 아님.
        branch = None
    porcelain = run_git(root, "status", "--porcelain").stdout
    changed = [ln for ln in porcelain.splitlines() if ln.strip()]
    dirty = len(changed) > 0
    return {
        "is_git": True,
        "branch": branch,
        "commit": commit,
        "dirty": dirty,
        "dirty_count": len(changed) if dirty else 0,
    }


def latest_project_mtime(root: str) -> str | None:
    """비-git staleness 앵커: `.handoff/` 밖 최신 소스 mtime 의 ISO 8601 (없으면 None)."""
    root_path = Path(root)
    newest = 0.0
    for path in root_path.rglob("*"):
        parts = set(path.parts)
        if ".handoff" in parts or ".git" in parts:
            continue
        if path.is_file():
            try:
                newest = max(newest, path.stat().st_mtime)
            except OSError:
                continue
    if newest <= 0:
        return None
    return iso8601(datetime.fromtimestamp(newest).astimezone())
