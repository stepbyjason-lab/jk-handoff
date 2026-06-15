"""3단계 충실화: CURRENT.md 가 다음 행동·블로커 보조줄을 뽑되 인덱스 역할 유지."""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path[:0] = [str(_Path(__file__).resolve().parent.parent / "core"),
                 str(_Path(__file__).resolve().parent)]

from helpers import HandoffTestCase


class EnrichmentTests(HandoffTestCase):

    def test_next_step_and_blocker_rendered(self):
        root = self.make_git_project()
        self.save(self.payload(
            "topic-a", summary="A 진행",
            exact_next_step="reindex 명령에 --all 플래그 추가",
            blockers="- gdrive 업로드 권한 막힘", lang="ko"), root)
        text = self.read_current("demo-proj")
        self.assertIn("다음: reindex 명령에 --all 플래그 추가", text)
        self.assertIn("⚠ 블로커: gdrive 업로드 권한 막힘", text)

    def test_blocker_placeholder_omitted(self):
        root = self.make_git_project()
        # blockers/exact_next_step 미지정 → CLI 가 placeholder 채움 → 보조줄 안 떠야 함.
        # lang 고정 — "⚠ 블로커:"/"다음:" 라벨은 ko 렌더링 전제.
        self.save(self.payload("topic-b", summary="B 진행", done="- 끝", lang="ko"), root)
        text = self.read_current("demo-proj")
        self.assertIn("topic-b", text)
        self.assertNotIn("⚠ 블로커:", text)
        self.assertNotIn("다음:", text)

    def test_real_blocker_with_eopseum_kept(self):
        # "블로커 없음" placeholder 가 아닌 실제 블로커(어미에 '없음') 는 보존돼야 함.
        root = self.make_git_project()
        self.save(self.payload("topic-c", summary="C 진행",
                               blockers="- DB 접근 권한 없음", lang="ko"), root)
        text = self.read_current("demo-proj")
        self.assertIn("⚠ 블로커: DB 접근 권한 없음", text)

    def test_long_next_step_clipped(self):
        root = self.make_git_project()
        # 비-secret 긴 문장(공백·한글 포함 → secret 휴리스틱 미적중)으로 truncation 검증.
        long_step = "다음 단계 상세 설명 " * 30  # 300자
        self.save(self.payload("topic-d", summary="D 진행",
                               exact_next_step=long_step, lang="ko"), root)
        text = self.read_current("demo-proj")
        self.assertIn("다음: ", text)
        self.assertIn("…", text)              # 잘림 표식
        self.assertNotIn(long_step, text)     # 본문 통째 복제 안 함(상한 truncate)

    def test_enrichment_does_not_leak_full_body(self):
        root = self.make_git_project()
        sentinel = "SENTINEL_BODY_4412"
        self.save(self.payload(
            "topic-e", summary="E 진행",
            done=f"- 깊은 본문 {sentinel}",
            exact_next_step="다음 단계 실행",
            blockers="- 블로커 X", lang="ko"), root)
        text = self.read_current("demo-proj")
        self.assertNotIn(sentinel, text)             # 인덱스 역할 유지
        self.assertIn("다음: 다음 단계 실행", text)
        self.assertIn("⚠ 블로커: 블로커 X", text)
