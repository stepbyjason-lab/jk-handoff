"""기존 status 호환 (open/open_planning/done/CLOSED 정규화 + 미인식 경고)."""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path[:0] = [str(_Path(__file__).resolve().parent.parent / "core"),
                 str(_Path(__file__).resolve().parent)]

from pathlib import Path

from handoff_cli import detail, status as status_mod
from helpers import HandoffTestCase


def _craft_legacy_topic(root: str, topic: str, status_value: str, summary: str) -> None:
    tdir = detail.topic_dir(root, topic)
    tdir.mkdir(parents=True, exist_ok=True)
    body = (
        f"---\ntopic: {topic}\ncreated: 2026-06-01T10:00:00+09:00\n"
        f"status: {status_value}\nsource: claude-code\n---\n\n# {topic}\n> {summary}\n"
    )
    fname = "2026-06-01-100000-aaaaaaaa.md"
    (tdir / fname).write_text(body, encoding="utf-8")
    (tdir / "LATEST.md").write_text(f"# LATEST -> {fname}\n\n[{fname}]({fname})\n\n> {summary}\n",
                                    encoding="utf-8")


class StatusCompatTests(HandoffTestCase):

    def test_24_normalize_mapping(self):
        self.assertEqual(status_mod.normalize_status("open")[0], "active")
        self.assertEqual(status_mod.normalize_status("open_planning")[0], "active")
        self.assertEqual(status_mod.normalize_status("in_progress")[0], "active")
        self.assertEqual(status_mod.normalize_status("in-progress")[0], "active")
        self.assertEqual(status_mod.normalize_status("paused")[0], "waiting")
        self.assertEqual(status_mod.normalize_status("done")[0], "done")
        self.assertEqual(status_mod.normalize_status("closed")[0], "done")
        self.assertEqual(status_mod.normalize_status("CLOSED")[0], "done")
        self.assertEqual(status_mod.normalize_status("waiting")[0], "waiting")
        self.assertEqual(status_mod.normalize_status("watching")[0], "watching")
        group, warn = status_mod.normalize_status("frobnicate")
        self.assertEqual(group, "active")
        self.assertIsNotNone(warn)

    def test_24_legacy_status_aggregation(self):
        root = self.make_git_project()
        _craft_legacy_topic(root, "legacy-open", "open", "레거시 진행 중")
        _craft_legacy_topic(root, "legacy-plan", "open_planning", "레거시 계획")
        _craft_legacy_topic(root, "legacy-closed", "CLOSED", "레거시 종료")
        # 정상 저장으로 CURRENT 재생성 트리거. lang 고정 — "## 진행 중" 단언은
        # ko 렌더링 전제이므로 로케일 무관하게 재현되도록 명시 고정한다.
        self.save(self.payload("trigger", lang="ko"), root)
        text = self.read_current("demo-proj")
        # open / open_planning 은 `## 진행 중` 섹션에 위치해야 한다.
        progress = text.split("## 진행 중", 1)[1].split("\n## ", 1)[0]
        self.assertIn("legacy-open", progress, "open → 진행 중 섹션")
        self.assertIn("legacy-plan", progress, "open_planning → 진행 중 섹션")
        self.assertNotIn("legacy-closed", text, "CLOSED → done → 인덱스 제외")

    def test_24_legacy_status_text_fallback(self):
        # frontmatter status 부재 + 텍스트에 CLOSED → done 으로 fallback (레거시 INDEX 호환).
        root = self.make_git_project()
        tdir = detail.topic_dir(root, "legacy-textclosed")
        tdir.mkdir(parents=True, exist_ok=True)
        fname = "2026-06-01-100000-bbbbbbbb.md"
        (tdir / fname).write_text("---\ntopic: legacy-textclosed\n---\n# 본문\n> 종료됨\n",
                                  encoding="utf-8")
        (tdir / "LATEST.md").write_text(
            f"# LATEST -> {fname}\n\n[{fname}]({fname})\n\n> CLOSED 종료됨\n", encoding="utf-8")
        self.save(self.payload("trigger", lang="ko"), root)
        text = self.read_current("demo-proj")
        self.assertNotIn("legacy-textclosed", text, "텍스트 CLOSED → done → 제외")
