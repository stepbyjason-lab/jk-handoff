"""케이스 17: #2 Not Tried Yet 섹션 흡수 회귀."""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path[:0] = [str(_Path(__file__).resolve().parent.parent / "core"),
                 str(_Path(__file__).resolve().parent)]

from handoff_cli import detail
from helpers import HandoffTestCase


class AbsorbTests(HandoffTestCase):

    def test_not_tried_section_renders_content(self):
        root = self.make_git_project()
        self.save(
            self.payload("topic-a", not_tried="- SSR 쿠키 방식 아직 안 해봄"),
            root,
        )
        tdir = detail.topic_dir(root, "topic-a")
        target = detail.read_latest_target(tdir)
        body = (tdir / target).read_text(encoding="utf-8")
        self.assertIn("## Not Tried Yet", body)
        self.assertIn("SSR 쿠키 방식", body)

    def test_not_tried_section_default_when_absent(self):
        # lang 고정 — 기본값 리터럴 단언은 ko 렌더링 전제, 로케일 오염 방지.
        root = self.make_git_project()
        self.save(self.payload("topic-b", lang="ko"), root)
        tdir = detail.topic_dir(root, "topic-b")
        target = detail.read_latest_target(tdir)
        body = (tdir / target).read_text(encoding="utf-8")
        self.assertIn("특별히 미시도 후보 없음.", body)

    def test_not_tried_section_order(self):
        root = self.make_git_project()
        self.save(
            self.payload("topic-c", not_tried="- 아직 안 해본 접근"),
            root,
        )
        tdir = detail.topic_dir(root, "topic-c")
        target = detail.read_latest_target(tdir)
        body = (tdir / target).read_text(encoding="utf-8")
        failed_idx = body.index("## Failed Attempts")
        not_tried_idx = body.index("## Not Tried Yet")
        blockers_idx = body.index("## Blockers And Questions")
        self.assertLess(failed_idx, not_tried_idx)
        self.assertLess(not_tried_idx, blockers_idx)
