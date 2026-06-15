"""폴더 rename · divergent project_id · .project-id 미커밋 경고."""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path[:0] = [str(_Path(__file__).resolve().parent.parent / "core"),
                 str(_Path(__file__).resolve().parent)]

import os
from pathlib import Path

from handoff_cli import detail, repo
from helpers import HandoffTestCase


class IdentityTests(HandoffTestCase):

    def test_08_rename_same_project_id(self):
        root = self.make_git_project(name="alpha")
        self.save(self.payload("topic-a"), root)
        pid = repo.read_project_id(root)
        self.assertTrue(self.current_path("alpha").exists())

        # 프로젝트 폴더 rename: alpha → beta (.project-id 는 따라 이동, 값 동일).
        new_root = os.path.join(self.tmp, "beta")
        os.rename(root, new_root)
        self.assertEqual(repo.read_project_id(new_root), pid)

        result = self.save(self.payload("topic-a", summary="rename 후"), new_root)
        # 기존 글로벌 폴더(alpha)에 기록, beta 형제폴더 생성 안 함.
        self.assertTrue(self.current_path("alpha").exists())
        self.assertFalse(self.current_path("beta").exists())
        self.assertEqual(result["global"]["mode"], "rename")
        self.assertTrue(any("rename" in w for w in result["warnings"]))

    def test_09_divergent_project_id_aborts_global(self):
        root = self.make_git_project(name="alpha")
        self.save(self.payload("topic-a"), root)
        before = self.read_current("alpha")
        original_id = repo.read_project_id(root)

        # 로컬 .project-id 를 다른 값으로 교체 → divergent.
        (Path(root) / ".handoff" / ".project-id").write_text("00000000-0000-0000-0000-000000000000\n",
                                                             encoding="utf-8")
        result = self.save(self.payload("topic-b", summary="divergent", lang="ko"), root)

        # 상세는 저장됨, 글로벌은 미갱신(원본 그대로).
        self.assertTrue(result["ok"])
        self.assertIn("detail_path", result)
        self.assertEqual(result["global"]["mode"], "divergent")
        self.assertFalse(result["global"]["written"])
        self.assertEqual(self.read_current("alpha"), before, "글로벌 CURRENT 는 미변경")
        self.assertTrue(any("divergent" in w for w in result["warnings"]))
        # 상세 정본은 디스크에 실제 존재해야 한다(분리 실패 경계).
        tdir = detail.topic_dir(root, "topic-b")
        target = detail.read_latest_target(tdir)
        self.assertIsNotNone(target)
        self.assertTrue((tdir / target).exists())

    def test_26_project_id_uncommitted_warning(self):
        root = self.make_git_project(name="demo-proj")
        # 첫 save: .project-id 가 untracked → 경고.
        result = self.save(self.payload("topic-a", lang="ko"), root)
        self.assertTrue(any(".project-id" in w and "미커밋" in w for w in result["warnings"]))
        self.assertTrue(result["ok"])

        # staged-only(add 했지만 commit 안 함)도 미커밋으로 경고해야 한다.
        self.git(root, "add", ".handoff/.project-id")
        result_staged = self.save(self.payload("topic-a", summary="staged", lang="ko"), root)
        self.assertTrue(
            any(".project-id" in w and "미커밋" in w for w in result_staged["warnings"]),
            "staged-only 도 미커밋 경고",
        )

        # 커밋 후 재저장: 무경고.
        self.git(root, "commit", "-qm", "commit project-id")
        result2 = self.save(self.payload("topic-a", summary="2회차", lang="ko"), root)
        self.assertFalse(any(".project-id" in w and "미커밋" in w for w in result2["warnings"]))
