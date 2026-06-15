"""테스트 공용 fixture 빌더."""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_CORE = str(_Path(__file__).resolve().parent.parent / "core")
if _CORE not in _sys.path:
    _sys.path.insert(0, _CORE)

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from handoff_cli import cli, current, detail


def _git(root: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", root, *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


class HandoffTestCase(unittest.TestCase):
    def setUp(self) -> None:
        # macOS 심링크(/var → /private/var) 정규화 — CLI 의 resolve 결과와 기대 경로 일치.
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="handoff-test-"))
        self.addCleanup(self._cleanup)
        self.global_root = os.path.join(self.tmp, "global-claude")

    def _cleanup(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- fixture builders ---

    def make_git_project(self, name: str = "demo-proj", commit_project_id: bool = False) -> str:
        root = os.path.join(self.tmp, name)
        os.makedirs(root, exist_ok=True)
        _git(root, "init", "-q")
        _git(root, "config", "user.email", "t@example.com")
        _git(root, "config", "user.name", "Tester")
        (Path(root) / "README.md").write_text("seed\n", encoding="utf-8")
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "init")
        if commit_project_id:
            # save 가 .project-id 를 만들도록 한 번 호출 후 커밋.
            self.save({"topic": "seed", "source": "claude-code", "summary": "seed",
                       "status": "active", "sections": {"done": "- seed"}}, root)
            _git(root, "add", ".handoff/.project-id")
            _git(root, "commit", "-qm", "commit project-id")
        return root

    def make_nongit_project(self, name: str = "nongit-proj") -> str:
        root = os.path.join(self.tmp, name)
        os.makedirs(root, exist_ok=True)
        (Path(root) / "CLAUDE.md").write_text("marker\n", encoding="utf-8")  # 프로젝트 마커
        return root

    def git(self, root: str, *args: str) -> subprocess.CompletedProcess:
        return _git(root, *args)

    # --- thin wrappers ---

    def save(self, payload: dict, cwd: str) -> dict:
        return cli.cmd_save(payload, cwd, self.global_root)

    def current_path(self, name: str) -> Path:
        return Path(self.global_root) / "handoffs" / name / "CURRENT.md"

    def read_current(self, name: str) -> str:
        return self.current_path(name).read_text(encoding="utf-8")

    def payload(self, topic: str, source: str = "claude-code", summary: str | None = None,
                status: str = "active", lang: str | None = None, **sections) -> dict:
        """`lang` 은 기본을 두지 않는다 — 명시 전달 시에만 payload 에 포함, 생략하면
        개별 테스트가 env/locale 체인을 그대로 탄다. 언어 의존 단언이 있는 테스트는
        호출부에서 `lang="ko"`(또는 "en")를 명시해 머신 로케일 오염을 막는다.
        """
        out = {
            "topic": topic,
            "source": source,
            "summary": summary or f"{topic} 요약",
            "status": status,
            "sections": sections or {"done": "- 작업"},
        }
        if lang is not None:
            out["lang"] = lang
        return out
