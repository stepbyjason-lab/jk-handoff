"""글로벌 갱신 실패가 상세 저장을 롤백하지 않음 (분리 실패 경계)."""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path[:0] = [str(_Path(__file__).resolve().parent.parent / "core"),
                 str(_Path(__file__).resolve().parent)]

from pathlib import Path

from handoff_cli import cli, current, detail
from helpers import HandoffTestCase


class GlobalFailureTests(HandoffTestCase):

    def test_25_global_failure_no_rollback(self):
        root = self.make_git_project()
        original = current.regenerate_current

        def boom(*args, **kwargs):
            raise RuntimeError("injected global failure")

        current.regenerate_current = boom
        try:
            result = self.save(self.payload("topic-a", lang="ko", done="- 상세 본문 보존되어야 함"), root)
        finally:
            current.regenerate_current = original

        self.assertTrue(result["ok"])
        # 상세 정본 3종은 그대로 존재.
        tdir = detail.topic_dir(root, "topic-a")
        target = detail.read_latest_target(tdir)
        self.assertIsNotNone(target)
        self.assertTrue((tdir / target).exists())
        self.assertTrue((tdir / "LATEST.md").exists())
        self.assertTrue((Path(root) / ".handoff" / "INDEX.md").exists())
        self.assertTrue(any("글로벌" in w and "실패" in w for w in result["warnings"]))
