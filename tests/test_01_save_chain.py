"""저장 · 체인 · 비-git · 교차 resume · 최근변경 롤링."""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path[:0] = [str(_Path(__file__).resolve().parent.parent / "core"),
                 str(_Path(__file__).resolve().parent)]

import re
from pathlib import Path

from handoff_cli import cli, detail, repo
from helpers import HandoffTestCase


class SaveChainTests(HandoffTestCase):

    def test_01_git_first_save(self):
        root = self.make_git_project()
        result = self.save(self.payload("sync-infra"), root)
        self.assertTrue(result["ok"])
        tdir = detail.topic_dir(root, "sync-infra")
        self.assertTrue((tdir / "LATEST.md").exists())
        self.assertTrue((Path(root) / ".handoff" / "INDEX.md").exists())
        text = self.read_current("demo-proj")
        m = re.search(r"written_at_commit: ([0-9a-f]{40})", text)
        self.assertIsNotNone(m, "full 40-char SHA 가 헤더에 있어야 함")
        self.assertNotIn("written_at_commit: none", text)

    def test_02_same_topic_prev_chain(self):
        root = self.make_git_project()
        self.save(self.payload("sync-infra", summary="첫째"), root)
        first = detail.read_latest_target(detail.topic_dir(root, "sync-infra"))
        self.save(self.payload("sync-infra", summary="둘째"), root)
        second = detail.read_latest_target(detail.topic_dir(root, "sync-infra"))
        self.assertNotEqual(first, second)
        body = (detail.topic_dir(root, "sync-infra") / second).read_text(encoding="utf-8")
        front, _ = detail.parse_frontmatter(body)
        self.assertEqual(front["prev"], first)

    def test_03_nongit_save_mtime_anchor(self):
        root = self.make_nongit_project()
        self.save(self.payload("local-only"), root)
        text = self.read_current("nongit-proj")
        self.assertIn("written_at_commit: none", text)
        m = re.search(r"written_at_mtime: (\S+)", text)
        self.assertIsNotNone(m)
        self.assertNotEqual(m.group(1), "none", "비-git 은 mtime 앵커가 있어야 함")

    def test_04_claude_save_codex_resume(self):
        root = self.make_git_project()
        self.save(self.payload("topic-a", source="claude-code",
                               done="- CLAUDE_SENTINEL_DONE"), root)
        # 저장 본문의 source 가 claude-code 로 기록됐는지 + 내용 보존.
        tdir = detail.topic_dir(root, "topic-a")
        front, _ = detail.parse_frontmatter(
            (tdir / detail.read_latest_target(tdir)).read_text(encoding="utf-8"))
        self.assertEqual(front["source"], "claude-code")
        # 타 writer(코어 동일) resume 으로 본문·다음행동을 읽는다.
        res = cli.cmd_resume(root, "topic-a")
        self.assertTrue(res["found"])
        self.assertFalse(res.get("broken"))
        self.assertIn("CLAUDE_SENTINEL_DONE", res["body"], "교차 resume 이 본문 내용을 복원")
        self.assertEqual(res["status"], "active")

    def test_05_codex_save_claude_resume(self):
        root = self.make_git_project()
        self.save(self.payload("topic-b", source="codex", done="- B"), root)
        # resume 는 어댑터 무관(코어). source 가 codex 여도 읽힌다.
        body = detail.topic_dir(root, "topic-b")
        target = detail.read_latest_target(body)
        front, _ = detail.parse_frontmatter((body / target).read_text(encoding="utf-8"))
        self.assertEqual(front["source"], "codex")
        res = cli.cmd_resume(root, "topic-b")
        self.assertTrue(res["found"])

    def test_06_two_writers_recent_changes(self):
        root = self.make_git_project()
        self.save(self.payload("topic-a", source="claude-code"), root)
        self.save(self.payload("topic-b", source="codex"), root)
        text = self.read_current("demo-proj")
        recent = [ln for ln in text.splitlines() if ln.startswith("- ") and "·" in ln]
        self.assertLessEqual(len(recent), 5)
        self.assertTrue(any("codex" in ln for ln in recent))
        self.assertTrue(any("claude-code" in ln for ln in recent))
        # writers set 머지 (양쪽 모두).
        self.assertIn("  - claude-code", text)
        self.assertIn("  - codex", text)

    def test_07_rolling_five_newest_first(self):
        root = self.make_git_project()
        for i in range(7):
            self.save(self.payload(f"topic-{i}", summary=f"요약{i}", lang="ko"), root)
        text = self.read_current("demo-proj")
        block = text.split("## 최근 변경", 1)[1]
        recent = [ln for ln in block.splitlines() if ln.startswith("- ") and "·" in ln]
        self.assertEqual(len(recent), 5, "최근 변경은 정확히 5개")
        self.assertIn("topic-6", recent[0], "newest-first")
        self.assertIn("topic-2", recent[-1])
