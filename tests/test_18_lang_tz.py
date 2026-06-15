"""로컬 타임존 + 언어 체인(i18n) 게이트.

(a) en 저장 → report/resume_prompt 영문 스켈레톤
(b) 체인 우선순위: payload > env HANDOFF_LANG > OS locale > en
(c) en 본문 "No blockers." 기본값 → CURRENT.md 집계에서 빈 값 처리(블로커 보조줄 안 뜸)
(d) 저장된 frontmatter `created:` 오프셋 == 로컬 시스템 오프셋
(e) ko 고정 저장 시 test_15 의 byte-exact 기대치 그대로 유지(회귀 없음)
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path[:0] = [str(_Path(__file__).resolve().parent.parent / "core"),
                 str(_Path(__file__).resolve().parent)]

import os
from datetime import datetime
from unittest import mock

from handoff_cli import cli, detail, messages, repo
from helpers import HandoffTestCase


class LangResolutionChainTests(HandoffTestCase):
    """(b) 체인 우선순위: payload > env > locale > en."""

    def test_payload_beats_everything(self):
        with mock.patch.dict(os.environ, {"HANDOFF_LANG": "en"}):
            with mock.patch("locale.getlocale", return_value=("Korean_Korea", "949")):
                self.assertEqual(messages.resolve_lang("ko"), "ko")

    def test_env_beats_locale(self):
        with mock.patch.dict(os.environ, {"HANDOFF_LANG": "en"}):
            with mock.patch("locale.getlocale", return_value=("Korean_Korea", "949")):
                self.assertEqual(messages.resolve_lang(None), "en")

    def test_locale_ko_variant_windows_form(self):
        # Windows locale 은 `Korean_Korea.949` 형태 — `ko` prefix 매칭만으론 놓친다.
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HANDOFF_LANG", None)
            with mock.patch("locale.getlocale", return_value=("Korean_Korea", "949")):
                self.assertEqual(messages.resolve_lang(None), "ko")

    def test_locale_ko_kr_form(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HANDOFF_LANG", None)
            with mock.patch("locale.getlocale", return_value=("ko_KR", "UTF-8")):
                self.assertEqual(messages.resolve_lang(None), "ko")

    def test_default_en_when_locale_is_neither(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HANDOFF_LANG", None)
            with mock.patch("locale.getlocale", return_value=("en_US", "UTF-8")):
                self.assertEqual(messages.resolve_lang(None), "en")

    def test_default_en_when_locale_unavailable(self):
        # CI ubuntu C locale 등 — getlocale 이 (None, None) 을 돌려줄 수 있음.
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HANDOFF_LANG", None)
            os.environ.pop("LANG", None)
            os.environ.pop("LC_ALL", None)
            os.environ.pop("LC_MESSAGES", None)
            with mock.patch("locale.getlocale", return_value=(None, None)):
                self.assertEqual(messages.resolve_lang(None), "en")

    def test_unknown_payload_lang_falls_back_to_en(self):
        self.assertEqual(messages.resolve_lang("fr"), "en")


class EnglishSaveTests(HandoffTestCase):
    """(a) en 저장 → report/resume_prompt 영문 스켈레톤."""

    def test_en_resume_prompt_skeleton(self):
        root = self.make_git_project()
        res = self.save(self.payload("topic-a", summary="short summary", lang="en"), root)
        prompt = res["resume_prompt"]
        self.assertIn("New session. Continuing the previous session's work.", prompt)
        self.assertIn(f"- Project: demo-proj  (saved-machine path: {root})", prompt)
        self.assertIn("- Topic: topic-a", prompt)
        self.assertIn("- Previous summary: short summary", prompt)
        self.assertIn("`/handoff resume topic-a`", prompt)
        self.assertIn('continue from "Exact Next Step"', prompt)
        # ko 스켈레톤 리터럴이 섞이지 않아야 함.
        self.assertNotIn("새 세션", prompt)
        self.assertNotIn("한국어", prompt)

    def test_en_save_report_skeleton(self):
        root = self.make_git_project()
        res = self.save(self.payload("topic-a", summary="short summary", lang="en"), root)
        report = res["report"]
        self.assertIn("✅ Handoff saved: `topic-a` (active)", report)
        self.assertIn("📋 To continue in the next session", report)
        self.assertIn("```text", report)
        self.assertIn(res["resume_prompt"], report)

    def test_en_warnings_header(self):
        root = self.make_git_project()
        res = self.save({"topic": "topic-a", "source": "bogus", "summary": "x",
                         "status": "active", "sections": {"done": "- x"}, "lang": "en"}, root)
        self.assertIn("⚠ Warnings:", res["report"])
        self.assertTrue(any("Unrecognized source" in w for w in res["warnings"]))


class BlockerDefaultBlankingTests(HandoffTestCase):
    """(c) en 본문 기본값("No blockers.") → CURRENT.md 집계에서 빈 값 처리."""

    def test_en_default_blocker_omitted_from_current(self):
        root = self.make_git_project()
        # blockers 미지정 → en 기본값 "No blockers." 채움 → CURRENT.md 보조줄에 안 떠야 함.
        self.save(self.payload("topic-en", summary="in progress", done="- done", lang="en"), root)
        text = self.read_current("demo-proj")
        self.assertIn("topic-en", text)
        self.assertNotIn("⚠ Blocker:", text)
        self.assertNotIn("No blockers.", text)

    def test_en_body_has_default_placeholder(self):
        root = self.make_git_project()
        # payload() 의 sections 기본값은 {"done": "- 작업"} 뿐 — 그 외 섹션(예: Failed
        # Attempts)은 실제로 CLI 기본값(placeholder)이 채워지므로 en 기본값 리터럴을 검증.
        self.save(self.payload("topic-en2", summary="in progress", lang="en"), root)
        tdir = detail.topic_dir(root, "topic-en2")
        target = detail.read_latest_target(tdir)
        body = (tdir / target).read_text(encoding="utf-8")
        self.assertIn("No blockers.", body)
        self.assertIn("No notably blocked attempts.", body)

    def test_en_real_blocker_kept(self):
        root = self.make_git_project()
        self.save(self.payload("topic-en3", summary="in progress",
                               blockers="- No access to prod DB", lang="en"), root)
        text = self.read_current("demo-proj")
        self.assertIn("⚠ Blocker: No access to prod DB", text)


class TimezoneOffsetTests(HandoffTestCase):
    """(d) 저장된 created: 오프셋 == 로컬 시스템 오프셋."""

    def test_created_offset_matches_local_astimezone(self):
        root = self.make_git_project()
        res = self.save(self.payload("topic-a", summary="tz check", lang="en"), root)
        tdir = detail.topic_dir(root, "topic-a")
        target = detail.read_latest_target(tdir)
        body = (tdir / target).read_text(encoding="utf-8")
        front, _ = detail.parse_frontmatter(body)
        created = front["created"]
        saved_dt = datetime.fromisoformat(created)
        expected_offset = datetime.now().astimezone().utcoffset()
        self.assertEqual(saved_dt.utcoffset(), expected_offset)

    def test_now_local_is_aware_with_local_offset(self):
        now = repo.now_local()
        self.assertIsNotNone(now.tzinfo)
        self.assertEqual(now.utcoffset(), datetime.now().astimezone().utcoffset())

    def test_no_hardcoded_kst_constant(self):
        # KST 상수가 repo 모듈에서 제거됐는지 확인 — 회귀 방지.
        self.assertFalse(hasattr(repo, "KST"), "KST 하드코딩 상수가 남아있으면 안 됨")
        self.assertFalse(hasattr(repo, "now_kst"), "now_kst 는 now_local 로 이름 변경돼야 함")


class KoreanByteExactRegressionTests(HandoffTestCase):
    """(e) ko 고정 저장 시 test_15 의 byte-exact 기대치 그대로 유지."""

    def test_ko_resume_prompt_byte_exact(self):
        root = self.make_git_project()
        res = self.save(self.payload("topic-a", summary="테스트 요약", lang="ko"), root)
        expected = "\n".join([
            "새 세션이다. 직전 세션의 작업을 이어간다.",
            "",
            f"- 프로젝트: demo-proj  (저장 머신 경로: {root})",
            "- 토픽: topic-a",
            "- 직전 요약: 테스트 요약",
            "",
            '먼저 이 프로젝트에서 `/handoff resume topic-a` 를 실행해(또는 "핸드오프 topic-a 이어받아줘")',
            "최신 핸드오프를 로드하고, Done/Open/Decisions/Git State 와 git drift 를 확인한 뒤",
            '"Exact Next Step" 부터 이어서 진행해줘. 작업 로그·보고는 사용자의 언어로.',
        ])
        self.assertEqual(res["resume_prompt"], expected)

    def test_ko_save_report_byte_exact_skeleton(self):
        root = self.make_git_project()
        res = self.save(self.payload("topic-a", summary="테스트 요약", lang="ko"), root)
        report = res["report"]
        self.assertIn("✅ 핸드오프 저장: `topic-a` (active)", report)
        self.assertIn("📋 다음 세션에서 이어가려면 아래를 복사해 붙여넣으세요:", report)


class DeterminismTests(HandoffTestCase):
    """같은 (payload, lang) → 같은 바이트."""

    def test_same_payload_same_lang_deterministic(self):
        root = self.make_git_project()
        r1 = self.save(self.payload("topic-a", summary="fixed", lang="en"), root)["resume_prompt"]
        r2 = self.save(self.payload("topic-a", summary="fixed", lang="en"), root)["resume_prompt"]
        self.assertEqual(r1, r2)

    def test_lang_switch_changes_output_language_only(self):
        root = self.make_git_project()
        res_ko = self.save(self.payload("topic-ko", summary="요약", lang="ko"), root)
        res_en = self.save(self.payload("topic-en", summary="summary", lang="en"), root)
        self.assertIn("새 세션", res_ko["resume_prompt"])
        self.assertIn("New session", res_en["resume_prompt"])
