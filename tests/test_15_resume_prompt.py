"""resume_prompt / report 결정적 생성 (CLI 가 보고 전체를 만든다)."""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path[:0] = [str(_Path(__file__).resolve().parent.parent / "core"),
                 str(_Path(__file__).resolve().parent)]

from handoff_cli import cli, detail
from helpers import HandoffTestCase


class ResumePromptTests(HandoffTestCase):

    def test_t1_result_has_fields(self):
        root = self.make_git_project()
        res = self.save(self.payload("topic-a", summary="테스트 요약", lang="ko"), root)
        for key in ("resume_prompt", "report", "topic", "status", "summary"):
            self.assertIn(key, res, f"결과에 {key} 없음")
        self.assertEqual(res["topic"], "topic-a")
        self.assertEqual(res["status"], "active")
        self.assertEqual(res["summary"], "테스트 요약")
        self.assertIn("새 세션", res["resume_prompt"])

    def test_t2_resume_prompt_byte_exact(self):
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

    def test_t3_report_contains_skeleton_and_prompt(self):
        root = self.make_git_project()
        res = self.save(self.payload("topic-a", summary="테스트 요약", lang="ko"), root)
        report = res["report"]
        self.assertIn("✅ 핸드오프 저장: `topic-a` (active)", report)
        self.assertIn("📋 다음 세션에서 이어가려면", report)
        self.assertIn("```text", report)                       # 코드블럭 펜스 포함
        self.assertIn("`/handoff resume topic-a`", report)     # 명령 형태 보존
        self.assertIn(res["resume_prompt"], report)            # resume_prompt 전문 포함

    def test_t4_deterministic(self):
        root = self.make_git_project()
        r1 = self.save(self.payload("topic-a", summary="요약 X", lang="ko"), root)["resume_prompt"]
        r2 = self.save(self.payload("topic-a", summary="요약 X", lang="ko"), root)["resume_prompt"]
        self.assertEqual(r1, r2, "같은 입력이면 resume_prompt 바이트 동일")
        # report 빌더는 순수함수 — 같은 인자 2회 동일.
        args = ("topic-a", "active", "demo-proj", ".handoff/topic-a/x.md", r1, [], "ko")
        self.assertEqual(cli._save_report(*args), cli._save_report(*args))

    def test_t5_summary_hygiene(self):
        root = self.make_git_project()
        # 멀티라인 summary → 1줄로 접힘.
        res = self.save(self.payload("topic-a", summary="첫 줄\n둘째 줄", lang="ko"), root)
        self.assertIn("- 직전 요약: 첫 줄 둘째 줄", res["resume_prompt"])
        self.assertNotIn("첫 줄\n둘째 줄", res["resume_prompt"])
        # summary 미입력(=topic 폴백) → 요약 줄 생략.
        res2 = self.save({"topic": "topic-b", "source": "claude-code", "summary": "",
                          "status": "active", "sections": {"done": "- x"}, "lang": "ko"}, root)
        self.assertNotIn("직전 요약", res2["resume_prompt"])

    def test_t7_warnings_rendered_in_report(self):
        # 미인식 source → _validate_source 가 강등 경고를 append → report 에 "⚠ 경고:" 블록.
        root = self.make_git_project()
        res = self.save({"topic": "topic-a", "source": "bogus", "summary": "요약",
                         "status": "active", "sections": {"done": "- x"}, "lang": "ko"}, root)
        self.assertTrue(res["warnings"], "경고가 있어야 분기 검증 가능")
        self.assertIn("⚠ 경고:", res["report"])
        for w in res["warnings"]:
            self.assertIn(f"- {w}", res["report"])

    def test_t8_resume_prompt_enclosed_in_fence(self):
        # resume_prompt 가 여는/닫는 ```text 펜스 '안에' 통째로 들어가야 한다(닫는 펜스 포함).
        root = self.make_git_project()
        res = self.save(self.payload("topic-a", summary="요약", lang="ko"), root)
        self.assertIn("```text\n" + res["resume_prompt"] + "\n```", res["report"])

    def test_t9_backticks_in_summary_neutralized(self):
        # summary 의 ``` 가 펜스를 깨면 안 된다 → resume_prompt 에 ``` 없음 + 펜스 enclosure 유지.
        root = self.make_git_project()
        res = self.save(self.payload("topic-a", summary="보고 ```python``` 블록 수정", lang="ko"), root)
        self.assertNotIn("```", res["resume_prompt"])
        self.assertIn("'''python'''", res["resume_prompt"])
        self.assertIn("```text\n" + res["resume_prompt"] + "\n```", res["report"])

    def test_t10_empty_summary_warns(self):
        # summary 미입력 → 조용히 thin 프롬프트가 아니라 경고로 신호.
        root = self.make_git_project()
        res = self.save({"topic": "topic-a", "source": "claude-code", "summary": "",
                         "status": "active", "sections": {"done": "- x"}, "lang": "ko"}, root)
        self.assertTrue(any("summary 미입력" in w for w in res["warnings"]))
        self.assertIn("⚠ 경고:", res["report"])
        self.assertIn("summary 미입력", res["report"])

    def test_t11_conflict_report_no_duplicate_warning(self):
        root = self.make_git_project()
        self.save(self.payload("topic-a", summary="첫째", lang="ko"), root)

        original = detail.write_detail

        def racing(td, filename, body):
            path = original(td, filename, body)
            (td / "LATEST.md").write_text("# LATEST -> someone-else.md\n", encoding="utf-8")
            return path

        detail.write_detail = racing
        try:
            res = self.save(self.payload("topic-a", summary="둘째", lang="ko"), root)
        finally:
            detail.write_detail = original

        # 충돌 안내는 lead 문구로 한 번만. 같은 메시지가 ⚠경고 블록에 중복 안 됨.
        self.assertIn("동시 저장 충돌", res["report"])
        self.assertNotIn("저장 도중 LATEST.md", res["report"])
        # 단, result.warnings 에는 그대로 보존(소실 아님).
        self.assertTrue(any("저장 도중 LATEST.md" in w for w in res["warnings"]))

    def test_t6_conflict_has_no_resume(self):
        root = self.make_git_project()
        self.save(self.payload("topic-a", summary="첫째", lang="ko"), root)

        original = detail.write_detail

        def racing(td, filename, body):
            path = original(td, filename, body)
            (td / "LATEST.md").write_text("# LATEST -> someone-else.md\n", encoding="utf-8")
            return path

        detail.write_detail = racing
        try:
            res = self.save(self.payload("topic-a", summary="둘째", lang="ko"), root)
        finally:
            detail.write_detail = original

        self.assertTrue(res.get("concurrent_conflict"), "충돌 경로 실제 진입")
        self.assertNotIn("resume_prompt", res, "충돌 시 resume_prompt 키 부재")
        self.assertIn("충돌", res["report"])
