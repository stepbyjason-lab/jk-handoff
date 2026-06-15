"""reindex 명령 — 기존 정본만 스캔해 글로벌 CURRENT.md 백필.

(a) 기존 토픽 2개 → 집계 생성 / (b) 멱등 / (c) .project-id 신규 생성 /
(d) active 0개·`.handoff` 없음 → 미생성+사유 / (e) legacy `status: open` → 진행 중.
reindex 는 새 detail·LATEST·INDEX 를 쓰지 않는다(정본 read-only).
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path[:0] = [str(_Path(__file__).resolve().parent.parent / "core"),
                 str(_Path(__file__).resolve().parent)]

import os
from datetime import datetime, timedelta, timezone
from unittest import mock

from handoff_cli import cli, detail, repo
from helpers import HandoffTestCase


def _craft_topic(root: str, topic: str, status: str, summary: str,
                 source: str = "claude-code") -> None:
    """save 경유 없이 detail+LATEST 만 만든다(글로벌 부수효과 없는 백필 fixture)."""
    tdir = detail.topic_dir(root, topic)
    tdir.mkdir(parents=True, exist_ok=True)
    fname = "2026-06-01-100000-aaaaaaaa.md"
    body = (
        f"---\ntopic: {topic}\ncreated: 2026-06-01T10:00:00+09:00\n"
        f"status: {status}\nsource: {source}\n---\n\n# {topic}\n> {summary}\n"
    )
    (tdir / fname).write_text(body, encoding="utf-8")
    (tdir / "LATEST.md").write_text(
        f"# LATEST -> {fname}\n\n[{fname}]({fname})\n\n> {summary}\n", encoding="utf-8")


class ReindexTests(HandoffTestCase):

    def reindex(self, root):
        return cli.cmd_reindex(root, None, self.global_root)

    def test_a_aggregate_existing_topics(self):
        root = self.make_git_project()
        _craft_topic(root, "topic-a", "active", "A 진행", source="claude-code")
        _craft_topic(root, "topic-b", "waiting", "B 대기", source="codex")
        result = self.reindex(root)
        self.assertTrue(result["reindexed"])
        self.assertEqual(result["active_topics"], 2)
        text = self.read_current("demo-proj")
        self.assertIn("topic-a", text)
        self.assertIn("topic-b", text)
        self.assertIn(".handoff/topic-a/LATEST.md", text)
        self.assertIn(".handoff/topic-b/LATEST.md", text)
        # writers 가 토픽 source 에서 도출됨.
        self.assertIn("  - claude-code", text)
        self.assertIn("  - codex", text)

    def test_a_read_only_no_new_detail(self):
        # reindex 는 새 detail·LATEST·INDEX 를 쓰지 않는다(정본 read-only).
        root = self.make_git_project()
        _craft_topic(root, "topic-a", "active", "A")
        tdir = detail.topic_dir(root, "topic-a")
        before = sorted(p.name for p in tdir.iterdir())
        self.reindex(root)
        after = sorted(p.name for p in tdir.iterdir())
        self.assertEqual(before, after, "토픽 디렉토리에 새 파일이 생기면 안 됨")
        self.assertFalse((_Path(root) / ".handoff" / "INDEX.md").exists(),
                         "reindex 는 INDEX.md 를 만들지 않음")

    def test_b_idempotent(self):
        root = self.make_git_project()
        _craft_topic(root, "topic-a", "active", "고정")
        frozen = datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone(timedelta(hours=9)))
        original = repo.now_local
        repo.now_local = lambda: frozen
        try:
            self.reindex(root)
            first = self.read_current("demo-proj")
            self.reindex(root)
            second = self.read_current("demo-proj")
        finally:
            repo.now_local = original
        self.assertEqual(first, second, "같은 입력 2회 → 바이트 동일")

    def test_c_generates_project_id(self):
        root = self.make_git_project()
        _craft_topic(root, "topic-a", "active", "A")
        self.assertIsNone(repo.read_project_id(root), "reindex 전엔 .project-id 없음")
        result = self.reindex(root)
        self.assertTrue(result["reindexed"])
        self.assertIsNotNone(repo.read_project_id(root), "reindex 가 .project-id 생성")
        self.assertEqual(result["project_id"], repo.read_project_id(root))

    def test_d_no_active_no_index(self):
        root = self.make_git_project()
        _craft_topic(root, "topic-closed", "CLOSED", "종료됨")
        result = self.reindex(root)
        self.assertFalse(result["reindexed"])
        self.assertEqual(result["reason"], "no active topics")
        self.assertFalse(self.current_path("demo-proj").exists(), "빈 인덱스 안 만듦")

    def test_d_no_handoff_dir(self):
        root = self.make_git_project()
        result = self.reindex(root)
        self.assertFalse(result["reindexed"])
        self.assertEqual(result["reason"], "no .handoff")

    def test_e_legacy_open_in_progress(self):
        # reindex 는 payload 가 없어 lang 을 env HANDOFF_LANG 체인으로 해석한다(cli.cmd_reindex).
        # "## 진행 중" 단언은 ko 렌더링 전제이므로 로케일 오염을 막기 위해 명시 고정한다.
        root = self.make_git_project()
        _craft_topic(root, "legacy-open", "open", "레거시 진행 중")
        _craft_topic(root, "legacy-plan", "open_planning", "레거시 계획")
        original_env = os.environ.get("HANDOFF_LANG")
        os.environ["HANDOFF_LANG"] = "ko"
        try:
            self.reindex(root)
        finally:
            if original_env is None:
                os.environ.pop("HANDOFF_LANG", None)
            else:
                os.environ["HANDOFF_LANG"] = original_env
        text = self.read_current("demo-proj")
        progress = text.split("## 진행 중", 1)[1].split("\n## ", 1)[0]
        self.assertIn("legacy-open", progress)
        self.assertIn("legacy-plan", progress)

    # --- writer-local 라우팅 (v0.2.1): reindex 도 source 에 따라 기본 루트가 갈린다 ---

    def _patched_reindex(self, root, source):
        """global_root 를 명시하지 않고(=기본 라우팅) source 만 주어 reindex.

        ~/.codex·~/.claude expanduser 를 tmp 안 fake_home 으로 돌려 실제 HOME 오염 없이
        기본 루트 라우팅을 검증한다 (cmd_save 라우팅 테스트와 동일 패턴).
        """
        fake_home = _Path(self.tmp) / "home"

        def fake_expanduser(path: str) -> str:
            if path == "~/.codex":
                return str(fake_home / ".codex")
            if path == "~/.claude":
                return str(fake_home / ".claude")
            return path

        with mock.patch("handoff_cli.cli.os.path.expanduser", side_effect=fake_expanduser):
            result = cli.cmd_reindex(root, None, None, source=source)
        codex_cur = fake_home / ".codex" / "handoffs" / "demo-proj" / "CURRENT.md"
        claude_cur = fake_home / ".claude" / "handoffs" / "demo-proj" / "CURRENT.md"
        return result, codex_cur, claude_cur

    def test_f_reindex_source_codex_routes_to_codex_local(self):
        root = self.make_git_project()
        _craft_topic(root, "topic-a", "active", "A 진행", source="codex")
        result, codex_cur, claude_cur = self._patched_reindex(root, source="codex")
        self.assertTrue(result["reindexed"])
        self.assertTrue(codex_cur.exists(), "codex reindex 는 ~/.codex 로 가야 함")
        self.assertFalse(claude_cur.exists(), "codex reindex 가 ~/.claude 를 건드리면 안 됨")
        self.assertEqual(_Path(result["global"]["path"]), codex_cur)

    def test_f_reindex_source_none_defaults_to_claude_local(self):
        # source 미지정(None) → claude-code 기본 → ~/.claude (cmd_save 와 동일 경로).
        root = self.make_git_project()
        _craft_topic(root, "topic-a", "active", "A 진행", source="claude-code")
        result, codex_cur, claude_cur = self._patched_reindex(root, source=None)
        self.assertTrue(result["reindexed"])
        self.assertTrue(claude_cur.exists(), "기본 reindex 는 ~/.claude 로 가야 함")
        self.assertFalse(codex_cur.exists(), "기본 reindex 가 ~/.codex 를 건드리면 안 됨")
        self.assertEqual(_Path(result["global"]["path"]), claude_cur)
