"""traversal/경로이탈 거부 · secret 차단."""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path[:0] = [str(_Path(__file__).resolve().parent.parent / "core"),
                 str(_Path(__file__).resolve().parent)]

from handoff_cli import repo, topics
from helpers import HandoffTestCase


class SecurityTests(HandoffTestCase):

    def test_13_traversal_rejected(self):
        bad = [
            "../../etc",
            "..\\..\\win",
            "a/b",
            "a\\b",
            ".hidden",
            "topic:stream",
            "\\\\server\\share",   # UNC
            "C:evil",
            "x\x00y",              # null byte
        ]
        for value in bad:
            with self.assertRaises(topics.TopicError, msg=f"{value!r} 는 거부되어야 함"):
                topics.normalize_topic(value)

        # 정상 토픽은 통과 + 정규화.
        self.assertEqual(topics.normalize_topic("Sync Infra"), "sync-infra")
        self.assertEqual(topics.normalize_topic("한글-토픽_1"), "한글-토픽_1")

        # --root 상위탈출 거부.
        with self.assertRaises(ValueError):
            repo.resolve_root(self.tmp, "../../escape")
        with self.assertRaises(ValueError):
            repo.resolve_root(self.tmp, "C:\\ok\\..\\..\\escape")
        with self.assertRaises(ValueError):
            repo.resolve_root(self.tmp, "ok\x00null")
        # 정상 명시 루트(`..` 없음)는 허용.
        self.assertTrue(repo.resolve_root(self.tmp, self.tmp))

    def test_14_secret_blocked_from_current(self):
        root = self.make_git_project()
        secret = "sk-ABCDEFGH1234567890abcdef"
        result = self.save(self.payload("topic-a", summary=f"키 유출 {secret}"), root)
        text = self.read_current("demo-proj")
        self.assertNotIn(secret, text, "secret 은 CURRENT.md 에 나오면 안 됨")
        self.assertIn("[REDACTED", text)
        self.assertTrue(any("secret" in w.lower() or "REDACTED" in w for w in result["warnings"]))

    def test_14b_common_words_not_redacted(self):
        # false-positive 회귀: 일반 업무 단어는 redact 하지 않는다.
        root = self.make_git_project()
        self.save(self.payload("jwt-work", summary="JWT token 구현과 password 화면 작업"), root)
        text = self.read_current("demo-proj")
        self.assertIn("token 구현", text, "일반 단어 'token'은 redact 되면 안 됨")
        self.assertNotIn("[REDACTED", text)
