"""크로스호스트 가드 (네트워크 없음).

(a) 충돌마커 → 상세 저장 + 글로벌 skip+경고
(b) 로컬 remote-tracking ref ahead → 글로벌 skip+경고
(c) 정상 → 재생성
(d) save 동안 fetch/pull/push 등 네트워크 git 호출 0건
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path[:0] = [str(_Path(__file__).resolve().parent.parent / "core"),
                 str(_Path(__file__).resolve().parent)]

import os
from pathlib import Path

from handoff_cli import guard, repo
from helpers import HandoffTestCase

_NETWORK = {"fetch", "pull", "push", "clone", "remote", "ls-remote"}


class CrossHostTests(HandoffTestCase):

    def test_21a_conflict_markers_skip_global(self):
        root = self.make_git_project()
        self.save(self.payload("topic-a"), root)
        cur = self.current_path("demo-proj")
        text = cur.read_text(encoding="utf-8")
        # 본문에 머지충돌 마커 주입 (frontmatter 는 보존해 project_id 파싱 유지).
        text = text + "\n<<<<<<< HEAD\nlocal\n=======\nremote\n>>>>>>> other\n"
        cur.write_text(text, encoding="utf-8")

        result = self.save(self.payload("topic-b", summary="충돌 중"), root)
        self.assertEqual(result["global"]["mode"], "skipped")
        self.assertEqual(result["global"]["skipped_reason"], "conflict markers")
        self.assertIn("<<<<<<<", cur.read_text(encoding="utf-8"), "글로벌은 미변경(마커 유지)")
        self.assertTrue(any("충돌" in w or "skip" in w for w in result["warnings"]))
        # 상세는 저장됨.
        from handoff_cli import detail
        self.assertTrue((detail.topic_dir(root, "topic-b") / "LATEST.md").exists())

    def test_21b_remote_ahead_skip_global(self):
        gr = self.global_root
        os.makedirs(gr, exist_ok=True)
        self.git(gr, "init", "-q")
        self.git(gr, "config", "user.email", "t@example.com")
        self.git(gr, "config", "user.name", "Tester")
        (Path(gr) / "f.txt").write_text("a\n", encoding="utf-8")
        self.git(gr, "add", "-A")
        self.git(gr, "commit", "-qm", "A")
        a_sha = self.git(gr, "rev-parse", "HEAD").stdout.strip()
        branch = self.git(gr, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        self.git(gr, "commit", "--allow-empty", "-qm", "B")
        b_sha = self.git(gr, "rev-parse", "HEAD").stdout.strip()
        self.git(gr, "update-ref", f"refs/remotes/origin/{branch}", b_sha)
        self.git(gr, "reset", "--hard", "-q", a_sha)
        self.git(gr, "config", f"branch.{branch}.remote", "origin")
        self.git(gr, "config", f"branch.{branch}.merge", f"refs/heads/{branch}")
        self.git(gr, "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
        self.assertTrue(guard.remote_ahead(gr), "fixture: 원격 ref 가 앞서야 함")

        root = self.make_git_project()
        result = self.save(self.payload("topic-a", lang="ko"), root)
        self.assertEqual(result["global"]["mode"], "skipped")
        self.assertEqual(result["global"]["skipped_reason"], "remote ahead")
        self.assertFalse(self.current_path("demo-proj").exists(), "글로벌 미작성")
        self.assertTrue(any("원격" in w or "앞" in w for w in result["warnings"]))

    def test_21c_normal_regenerates(self):
        root = self.make_git_project()
        result = self.save(self.payload("topic-a"), root)
        self.assertTrue(result["global"]["written"])
        self.assertIn(result["global"]["mode"], ("new", "normal"))

    def test_21d_no_network_git_calls(self):
        root = self.make_git_project()
        calls = []
        real = repo.run_git

        def spy(r, *args):
            calls.append(args)
            return real(r, *args)

        repo.run_git = spy
        try:
            self.save(self.payload("topic-a"), root)
        finally:
            repo.run_git = real
        offending = [a for a in calls if a and a[0] in _NETWORK]
        self.assertEqual(offending, [], f"네트워크 git 호출 발생: {offending}")
        self.assertTrue(calls, "git 이 실제로 호출되긴 해야 함")
