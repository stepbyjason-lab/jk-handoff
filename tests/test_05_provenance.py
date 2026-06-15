"""updated_at 시스템 생성 · git 메타 실측 일치."""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path[:0] = [str(_Path(__file__).resolve().parent.parent / "core"),
                 str(_Path(__file__).resolve().parent)]

from datetime import datetime, timedelta, timezone

from handoff_cli import repo
from helpers import HandoffTestCase


class ProvenanceTests(HandoffTestCase):

    def test_15_updated_at_from_clock(self):
        root = self.make_git_project()
        frozen = datetime(2026, 6, 5, 22, 51, 0, tzinfo=timezone(timedelta(hours=9)))
        original = repo.now_local
        repo.now_local = lambda: frozen
        try:
            self.save(self.payload("topic-a"), root)
        finally:
            repo.now_local = original
        text = self.read_current("demo-proj")
        self.assertIn("updated_at: 2026-06-05T22:51:00+09:00", text)

    def test_16_git_meta_matches_rev_parse(self):
        root = self.make_git_project()
        self.save(self.payload("topic-a"), root)
        real = self.git(root, "rev-parse", "HEAD").stdout.strip()
        branch = self.git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        text = self.read_current("demo-proj")
        self.assertIn(f"written_at_commit: {real}", text)
        self.assertIn(f"branch: {branch}", text)
