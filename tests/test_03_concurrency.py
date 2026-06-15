"""동시저장 LATEST 충돌 · orphan 감지 · 손상/없는 포인터."""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path[:0] = [str(_Path(__file__).resolve().parent.parent / "core"),
                 str(_Path(__file__).resolve().parent)]

from pathlib import Path

from handoff_cli import cli, detail
from helpers import HandoffTestCase


class ConcurrencyTests(HandoffTestCase):

    def test_10_concurrent_latest_change(self):
        root = self.make_git_project()
        self.save(self.payload("topic-a", summary="첫째"), root)
        tdir = detail.topic_dir(root, "topic-a")

        original_write_detail = detail.write_detail

        def racing_write_detail(td, filename, body):
            path = original_write_detail(td, filename, body)
            # 다른 writer 가 LATEST 를 바꾼 상황을 모사.
            (td / "LATEST.md").write_text("# LATEST -> someone-else.md\n", encoding="utf-8")
            return path

        detail.write_detail = racing_write_detail
        try:
            result = self.save(self.payload("topic-a", summary="둘째"), root)
        finally:
            detail.write_detail = original_write_detail

        self.assertTrue(result.get("concurrent_conflict"))
        self.assertTrue(any("동시" in w or "LATEST" in w for w in result["warnings"]))
        # 신규 본문은 보존(파일 존재), 포인터는 racing 값 그대로(덮어쓰지 않음).
        self.assertIn("someone-else.md", (tdir / "LATEST.md").read_text(encoding="utf-8"))

    def test_11_orphan_detection(self):
        root = self.make_git_project()
        self.save(self.payload("topic-a"), root)
        tdir = detail.topic_dir(root, "topic-a")
        # LATEST 가 가리키는 것보다 새 본문파일을 만든다(포인터 갱신 전 크래시 모사).
        orphan = tdir / "2099-12-31-235959-deadbeef.md"
        orphan.write_text("---\ntopic: topic-a\n---\n# orphan\n", encoding="utf-8")
        warn = detail.detect_orphan(tdir, detail.read_latest_target(tdir))
        self.assertIsNotNone(warn)
        self.assertIn("orphan", warn)
        res = cli.cmd_resume(root, "topic-a")
        self.assertTrue(any("orphan" in w for w in res["warnings"]))

    def test_12_broken_and_missing_pointer(self):
        root = self.make_git_project()
        self.save(self.payload("topic-a"), root)
        tdir = detail.topic_dir(root, "topic-a")
        # 손상: LATEST 가 없는 파일을 가리킴.
        (tdir / "LATEST.md").write_text("# LATEST -> 2099-01-01-000000-00000000.md\n", encoding="utf-8")
        res = cli.cmd_resume(root, "topic-a")
        self.assertTrue(res["found"])
        self.assertTrue(res["broken"], "broken 포인터는 임의 파일을 최신으로 고르지 않음")

        # 레거시 본문형 LATEST(포인터 없음) → 그 파일 자체를 본문으로 읽음.
        tdir2 = detail.topic_dir(root, "legacy")
        tdir2.mkdir(parents=True, exist_ok=True)
        (tdir2 / "LATEST.md").write_text("---\ntopic: legacy\nstatus: open\n---\n# 레거시 본문\n> 요약\n",
                                         encoding="utf-8")
        res2 = cli.cmd_resume(root, "legacy")
        self.assertTrue(res2["found"])
        self.assertFalse(res2.get("broken"))
