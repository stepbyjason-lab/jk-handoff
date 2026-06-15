"""전 토픽 집계 · idempotence · no-duplication."""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path[:0] = [str(_Path(__file__).resolve().parent.parent / "core"),
                 str(_Path(__file__).resolve().parent)]

import re
from datetime import datetime, timedelta, timezone

from handoff_cli import repo
from helpers import HandoffTestCase


class AggregationTests(HandoffTestCase):

    def test_17_aggregate_no_cross_topic_loss(self):
        root = self.make_git_project()
        self.save(self.payload("topic-a", summary="A 진행"), root)
        self.save(self.payload("topic-b", summary="B 진행"), root)
        text = self.read_current("demo-proj")
        # topicA 인덱스가 topicB 저장 후에도 보존됨.
        self.assertIn("topic-a", text)
        self.assertIn("topic-b", text)
        self.assertIn(".handoff/topic-a/LATEST.md", text)
        self.assertIn(".handoff/topic-b/LATEST.md", text)

    def test_18_idempotent_except_updated_at(self):
        root = self.make_git_project()
        frozen = datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone(timedelta(hours=9)))
        original = repo.now_local
        repo.now_local = lambda: frozen
        try:
            self.save(self.payload("topic-a", summary="고정"), root)
            first = self.read_current("demo-proj")
            self.save(self.payload("topic-a", summary="고정"), root)
            second = self.read_current("demo-proj")
        finally:
            repo.now_local = original
        # 시계 freeze + 동일 입력 → 바이트 동일 (최근변경 중복 헤드 제거 + updated_at 동일).
        self.assertEqual(first, second)

    def test_19_detail_body_not_copied_into_current(self):
        root = self.make_git_project()
        sentinel = "SENTINEL_DEEP_DETAIL_XYZ_9981"
        self.save(self.payload("topic-a", summary="짧은 요약",
                               done=f"- 깊은 본문 {sentinel} 내용"), root)
        text = self.read_current("demo-proj")
        self.assertNotIn(sentinel, text, "CURRENT 는 상세 본문을 복제하지 않음")
        # 상세 본문에는 sentinel 이 있어야 함(인덱스가 아닌 정본).
        from handoff_cli import detail
        tdir = detail.topic_dir(root, "topic-a")
        target = detail.read_latest_target(tdir)
        self.assertIn(sentinel, (tdir / target).read_text(encoding="utf-8"))
