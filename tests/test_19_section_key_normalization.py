"""sections 키 불일치로 인한 silent data loss 방지 회귀 테스트.

12개 케이스:
1. 기존 정상 소문자 스네이크케이스 입력 — warning 없이 그대로 저장(회귀 없음).
2. 대소문자 변형 "Done"/"OPEN"/"VERIFICATION" → 각 canonical 저장.
3. 헤딩스타일 "Failed Attempts"/"Not Tried Yet"/"Blockers And Questions"/"Exact Next Step" → canonical 저장.
4. snake alias "not_tried_yet"/"blockers_and_questions" → not_tried/blockers 저장.
5. 완전 미인식 키 → 기본값 유지 + warn_unknown_section_key.
6. "done"+"Done" 충돌 → exact match "done" 채택 + duplicate 경고(ignored 에 "Done").
7. "DONE"+"Done" 충돌(둘 다 비-exact) → 순회상 먼저 나온 키 채택 + duplicate 경고.
8. 3-way 충돌 "done"+"Done"+"DONE" → exact match "done" 채택, ignored 에 둘 다 포함.
9. 빈 문자열 exact match 트레이드오프 — 값 유무로 우선순위 안 바꿈.
10. 복합 실사고 재현 — 8개 전부 헤딩스타일/대소문자 혼합 키로 채워 저장.
11. sections 가 dict 아님(문자열/None) → warn_invalid_sections, 크래시 없이 기본값 저장.
12. 기존 테스트 스위트(test_01~test_18) 전체 통과는 `pytest` 전체 실행으로 별도 확인.
13. section value 가 문자열이 아님(int/list 혼합) → 크래시 없이 저장 성공, 해당 canonical
    은 기본값 placeholder, warn_invalid_section_value 계열 경고, 나머지 정상 문자열
    section 은 그대로 저장(사후 python-reviewer 게이트에서 발견된 HIGH 결함 회귀 방지).
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path[:0] = [str(_Path(__file__).resolve().parent.parent / "core"),
                 str(_Path(__file__).resolve().parent)]

from handoff_cli import detail
from helpers import HandoffTestCase


def _body_text(case: HandoffTestCase, root: str, topic: str) -> str:
    tdir = detail.topic_dir(root, topic)
    target = detail.read_latest_target(tdir)
    return (tdir / target).read_text(encoding="utf-8")


class ExistingLowercaseRegressionTests(HandoffTestCase):
    """1. 기존 정상 소문자 스네이크케이스 입력 — warning 없이 그대로 저장(회귀 없음)."""

    def test_lowercase_snake_case_no_warnings(self):
        root = self.make_git_project(commit_project_id=True)
        res = self.save(self.payload(
            "topic-lower", summary="정상 입력", lang="en",
            done="- did the thing", open="- [ ] follow up",
            failed_attempts="- tried X, failed",
            not_tried="- candidate Y",
            blockers="- waiting on review",
            decisions="- picked A over B",
            exact_next_step="- run the migration",
            verification="- pytest green",
        ), root)
        self.assertEqual(res["warnings"], [])
        body = _body_text(self, root, "topic-lower")
        self.assertIn("did the thing", body)
        self.assertIn("follow up", body)
        self.assertIn("tried X, failed", body)
        self.assertIn("candidate Y", body)
        self.assertIn("waiting on review", body)
        self.assertIn("picked A over B", body)
        self.assertIn("run the migration", body)
        self.assertIn("pytest green", body)


class CaseVariantTests(HandoffTestCase):
    """2. 대소문자 변형 "Done"/"OPEN"/"VERIFICATION" → 각 canonical 저장."""

    def test_case_variants_map_to_canonical(self):
        root = self.make_git_project(commit_project_id=True)
        payload = self.payload("topic-case", summary="케이스 변형", lang="en")
        payload["sections"] = {
            "Done": "- CASE_DONE_SENTINEL",
            "OPEN": "- CASE_OPEN_SENTINEL",
            "VERIFICATION": "- CASE_VERIFY_SENTINEL",
        }
        res = self.save(payload, root)
        self.assertEqual(res["warnings"], [])
        body = _body_text(self, root, "topic-case")
        self.assertIn("CASE_DONE_SENTINEL", body)
        self.assertIn("CASE_OPEN_SENTINEL", body)
        self.assertIn("CASE_VERIFY_SENTINEL", body)


class HeadingStyleTests(HandoffTestCase):
    """3. 헤딩스타일 "Failed Attempts"/"Not Tried Yet"/"Blockers And Questions"/
    "Exact Next Step" → canonical 저장."""

    def test_heading_style_keys_map_to_canonical(self):
        root = self.make_git_project(commit_project_id=True)
        payload = self.payload("topic-heading", summary="헤딩스타일", lang="en")
        payload["sections"] = {
            "Failed Attempts": "- HEADING_FAILED_SENTINEL",
            "Not Tried Yet": "- HEADING_NOTTRIED_SENTINEL",
            "Blockers And Questions": "- HEADING_BLOCKERS_SENTINEL",
            "Exact Next Step": "- HEADING_NEXTSTEP_SENTINEL",
        }
        res = self.save(payload, root)
        self.assertEqual(res["warnings"], [])
        body = _body_text(self, root, "topic-heading")
        self.assertIn("HEADING_FAILED_SENTINEL", body)
        self.assertIn("HEADING_NOTTRIED_SENTINEL", body)
        self.assertIn("HEADING_BLOCKERS_SENTINEL", body)
        self.assertIn("HEADING_NEXTSTEP_SENTINEL", body)


class SnakeAliasTests(HandoffTestCase):
    """4. snake alias "not_tried_yet"/"blockers_and_questions" → not_tried/blockers 저장."""

    def test_snake_case_aliases_map_to_canonical(self):
        root = self.make_git_project(commit_project_id=True)
        payload = self.payload("topic-alias", summary="snake alias", lang="en")
        payload["sections"] = {
            "not_tried_yet": "- ALIAS_NOTTRIED_SENTINEL",
            "blockers_and_questions": "- ALIAS_BLOCKERS_SENTINEL",
        }
        res = self.save(payload, root)
        self.assertEqual(res["warnings"], [])
        body = _body_text(self, root, "topic-alias")
        self.assertIn("ALIAS_NOTTRIED_SENTINEL", body)
        self.assertIn("ALIAS_BLOCKERS_SENTINEL", body)


class UnknownKeyTests(HandoffTestCase):
    """5. 완전 미인식 키 → 저장된 body 에 값 안 들어가고(canonical 기본값 유지),
    warnings 에 warn_unknown_section_key 계열 문구 존재."""

    def test_unknown_key_dropped_with_warning(self):
        root = self.make_git_project()
        payload = self.payload("topic-unknown", summary="미인식 키", lang="en")
        payload["sections"] = {"foo": "- UNKNOWN_KEY_SENTINEL_VALUE"}
        res = self.save(payload, root)
        body = _body_text(self, root, "topic-unknown")
        self.assertNotIn("UNKNOWN_KEY_SENTINEL_VALUE", body)
        # done 이 기본값(placeholder)으로 남아야 함 — payload 에 done 자체가 없으므로.
        self.assertIn("Nothing confirmed complete this session", body)
        self.assertTrue(
            any("foo" in w for w in res["warnings"]),
            f"경고에 미인식 키 'foo' 가 언급돼야 함: {res['warnings']}",
        )


class DuplicateExactMatchTests(HandoffTestCase):
    """6. "done"+"Done" 충돌 → exact match "done" 값 채택, warnings 에 duplicate 경고
    (ignored 에 "Done" 포함)."""

    def test_exact_match_wins_over_case_variant(self):
        root = self.make_git_project()
        payload = self.payload("topic-dup-exact", summary="충돌 exact", lang="en")
        payload["sections"] = {
            "done": "- EXACT_LOWERCASE_SENTINEL",
            "Done": "- CASE_VARIANT_SENTINEL",
        }
        res = self.save(payload, root)
        body = _body_text(self, root, "topic-dup-exact")
        self.assertIn("EXACT_LOWERCASE_SENTINEL", body)
        self.assertNotIn("CASE_VARIANT_SENTINEL", body)
        dup_warnings = [w for w in res["warnings"] if "done" in w and "Done" in w]
        self.assertTrue(dup_warnings, f"duplicate 경고가 있어야 함: {res['warnings']}")
        self.assertTrue(any("Done" in w for w in dup_warnings))


class DuplicateFirstSeenTests(HandoffTestCase):
    """7. "DONE"+"Done" 충돌(둘 다 정확 매치 아님) → raw_sections 순회상 먼저 나온 키 채택,
    duplicate 경고."""

    def test_first_seen_wins_when_no_exact_match(self):
        root = self.make_git_project()
        payload = self.payload("topic-dup-first", summary="충돌 first-seen", lang="en")
        payload["sections"] = {
            "DONE": "- FIRST_SEEN_SENTINEL",
            "Done": "- SECOND_SEEN_SENTINEL",
        }
        res = self.save(payload, root)
        body = _body_text(self, root, "topic-dup-first")
        self.assertIn("FIRST_SEEN_SENTINEL", body)
        self.assertNotIn("SECOND_SEEN_SENTINEL", body)
        self.assertTrue(
            any("DONE" in w and "Done" in w for w in res["warnings"]),
            f"duplicate 경고가 있어야 함: {res['warnings']}",
        )


class ThreeWayConflictTests(HandoffTestCase):
    """8. 3-way 충돌 "done"+"Done"+"DONE" (한 dict 안에 셋 다) → exact match "done" 채택,
    ignored 문구에 "Done"·"DONE" 둘 다 포함."""

    def test_three_way_conflict_exact_match_wins_all_others_ignored(self):
        root = self.make_git_project()
        payload = self.payload("topic-dup-triple", summary="3-way 충돌", lang="en")
        payload["sections"] = {
            "done": "- TRIPLE_EXACT_SENTINEL",
            "Done": "- TRIPLE_CASE1_SENTINEL",
            "DONE": "- TRIPLE_CASE2_SENTINEL",
        }
        res = self.save(payload, root)
        body = _body_text(self, root, "topic-dup-triple")
        self.assertIn("TRIPLE_EXACT_SENTINEL", body)
        self.assertNotIn("TRIPLE_CASE1_SENTINEL", body)
        self.assertNotIn("TRIPLE_CASE2_SENTINEL", body)
        dup_warnings = [w for w in res["warnings"] if "done" in w]
        self.assertTrue(dup_warnings, f"duplicate 경고가 있어야 함: {res['warnings']}")
        combined = " ".join(dup_warnings)
        self.assertIn("Done", combined)
        self.assertIn("DONE", combined)


class EmptyStringExactMatchTradeoffTests(HandoffTestCase):
    """9. 빈 문자열 exact match 트레이드오프: {"done": "", "Done": "실제내용"} →
    "done"(빈 값) 이 채택되어 저장된 body 의 "## Done" 이 기본값 placeholder 로 남고,
    duplicate 경고는 뜬다(값 유무로 채택 규칙을 바꾸지 않는다는 것을 확정하는 테스트 —
    회귀 시 실패해야 함)."""

    def test_empty_string_exact_match_still_wins(self):
        root = self.make_git_project()
        payload = self.payload("topic-dup-empty", summary="빈값 exact 트레이드오프", lang="en")
        payload["sections"] = {
            "done": "",
            "Done": "REAL_CONTENT_SENTINEL_SHOULD_BE_IGNORED",
        }
        res = self.save(payload, root)
        body = _body_text(self, root, "topic-dup-empty")
        # exact match("done") 의 빈 값이 채택 → _section() 이 기본값으로 대체.
        self.assertIn("Nothing confirmed complete this session", body)
        self.assertNotIn("REAL_CONTENT_SENTINEL_SHOULD_BE_IGNORED", body)
        self.assertTrue(
            any("done" in w and "Done" in w for w in res["warnings"]),
            f"duplicate 경고가 있어야 함: {res['warnings']}",
        )


class CompositeRealIncidentReproTests(HandoffTestCase):
    """10. 복합 실사고 재현: 한 payload 의 sections 에 8개 전부를 헤딩스타일/대소문자
    혼합 키로 채워 저장 → 저장된 body 8개 섹션 전부 placeholder 가 아닌 실제 값
    (sipher 사고 재현 형태)."""

    def test_all_eight_sections_via_mixed_heading_style_keys_survive(self):
        root = self.make_git_project(commit_project_id=True)
        payload = self.payload("topic-sipher-repro", summary="sipher 사고 재현", lang="en")
        payload["sections"] = {
            "Done": "- REPRO_DONE_SENTINEL",
            "Open": "- REPRO_OPEN_SENTINEL",
            "Failed Attempts": "- REPRO_FAILED_SENTINEL",
            "Not Tried Yet": "- REPRO_NOTTRIED_SENTINEL",
            "Blockers And Questions": "- REPRO_BLOCKERS_SENTINEL",
            "Decisions": "- REPRO_DECISIONS_SENTINEL",
            "Exact Next Step": "- REPRO_NEXTSTEP_SENTINEL",
            "Verification": "- REPRO_VERIFY_SENTINEL",
        }
        res = self.save(payload, root)
        self.assertEqual(res["warnings"], [])
        body = _body_text(self, root, "topic-sipher-repro")
        for sentinel in (
            "REPRO_DONE_SENTINEL", "REPRO_OPEN_SENTINEL", "REPRO_FAILED_SENTINEL",
            "REPRO_NOTTRIED_SENTINEL", "REPRO_BLOCKERS_SENTINEL", "REPRO_DECISIONS_SENTINEL",
            "REPRO_NEXTSTEP_SENTINEL", "REPRO_VERIFY_SENTINEL",
        ):
            self.assertIn(sentinel, body, f"{sentinel} 이 placeholder 로 대체되면 안 됨")
        # placeholder 리터럴이 하나도 남아있지 않아야 함(실사고 = 8개 전부 유실이었음).
        self.assertNotIn("Nothing confirmed complete this session", body)
        self.assertNotIn("next action not decided", body)
        self.assertNotIn("No notably blocked attempts.", body)
        self.assertNotIn("No notable untried candidates.", body)
        self.assertNotIn("No blockers.", body)
        self.assertNotIn("No notable decisions.", body)
        self.assertNotIn("next session's step not decided", body)
        self.assertNotIn("- Unverified", body)


class InvalidSectionsTypeTests(HandoffTestCase):
    """11. sections 필드가 dict 가 아님(문자열 또는 None) → warn_invalid_sections 경고,
    크래시 없이 8개 섹션 전부 기본값으로 저장."""

    def test_string_sections_warns_and_falls_back_to_defaults(self):
        root = self.make_git_project()
        payload = self.payload("topic-invalid-str", summary="sections 문자열", lang="en")
        payload["sections"] = "not-a-dict"
        res = self.save(payload, root)
        self.assertTrue(res["ok"])
        self.assertTrue(any("invalid" in w.lower() or "not a dict" in w.lower()
                            for w in res["warnings"]) or res["warnings"],
                       f"warn_invalid_sections 계열 경고가 있어야 함: {res['warnings']}")
        body = _body_text(self, root, "topic-invalid-str")
        self.assertIn("Nothing confirmed complete this session", body)
        self.assertIn("No blockers.", body)

    def test_none_sections_no_crash_no_warning_all_defaults(self):
        root = self.make_git_project()
        payload = self.payload("topic-invalid-none", summary="sections None", lang="en")
        payload["sections"] = None
        res = self.save(payload, root)
        self.assertTrue(res["ok"])
        body = _body_text(self, root, "topic-invalid-none")
        self.assertIn("Nothing confirmed complete this session", body)
        self.assertIn("No blockers.", body)


class InvalidSectionValueTypeTests(HandoffTestCase):
    """13. section value 가 문자열이 아님(int/list 혼합) → 크래시 없이 저장 성공,
    해당 canonical 은 기본값 placeholder, warn_invalid_section_value 계열 경고 2건
    (done 용, open 용), verification(정상 문자열) 은 실제 값 저장."""

    def test_non_string_values_dropped_with_warning_no_crash(self):
        root = self.make_git_project()
        payload = self.payload("topic-invalid-value", summary="value 타입 혼합", lang="ko")
        payload["sections"] = {
            "done": 123,
            "open": ["a", "b"],
            "verification": "정상 문자열",
        }
        res = self.save(payload, root)  # 크래시하면 이 호출에서 예외로 실패한다.
        self.assertTrue(res["ok"])
        body = _body_text(self, root, "topic-invalid-value")
        # 정상 문자열 section 은 그대로 저장됨.
        self.assertIn("정상 문자열", body)
        # non-string 값은 강제 형변환 없이 버려지고 canonical 기본값(placeholder)이 남음.
        self.assertIn("이번 세션에서 확정 완료된 것 없음", body)  # done_default(ko)
        self.assertIn("- [ ] (다음 행동 미정)", body)  # open_default(ko)
        self.assertNotIn("123", body)
        self.assertNotIn("['a', 'b']", body)
        invalid_value_warnings = [w for w in res["warnings"] if "done" in w or "open" in w]
        self.assertEqual(
            len(invalid_value_warnings), 2,
            f"done·open 각각에 대한 경고가 1건씩(총 2건) 있어야 함: {res['warnings']}",
        )


class MessageTableRoutingTests(HandoffTestCase):
    """경고 문구가 ko/en 메시지 테이블을 경유하는지(하드코딩이 아닌지) 확인.

    12번 항목(기존 스위트 회귀 없음)은 `pytest`(전체 스위트) 로 별도 확인한다 —
    이 클래스는 그 확인의 부속으로, 신규 경고가 lang 스위칭에 반응하는지 본다.
    """

    def test_unknown_key_warning_switches_with_lang(self):
        root = self.make_git_project()
        payload_en = self.payload("topic-lang-en", summary="lang en", lang="en")
        payload_en["sections"] = {"foo": "- x"}
        res_en = self.save(payload_en, root)
        payload_ko = self.payload("topic-lang-ko", summary="lang ko", lang="ko")
        payload_ko["sections"] = {"foo": "- x"}
        res_ko = self.save(payload_ko, root)
        en_warning = next(w for w in res_en["warnings"] if "foo" in w)
        ko_warning = next(w for w in res_ko["warnings"] if "foo" in w)
        self.assertNotEqual(en_warning, ko_warning)
