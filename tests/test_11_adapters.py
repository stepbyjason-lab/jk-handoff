"""quick_validate 통과 · Body Template 동작 회귀."""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path[:0] = [str(_Path(__file__).resolve().parent.parent / "core"),
                 str(_Path(__file__).resolve().parent)]

import os
import subprocess
import sys
from pathlib import Path

from handoff_cli import detail
from helpers import HandoffTestCase

REPO_ROOT = Path(__file__).resolve().parent.parent
# skill-creator 의 quick_validate.py 경로는 머신마다 다르다.
# 환경변수 HANDOFF_QUICK_VALIDATE 로 지정하지 않으면 이 테스트는 skip 된다.
_VALIDATOR_ENV = os.environ.get("HANDOFF_QUICK_VALIDATE", "").strip()
VALIDATOR = Path(_VALIDATOR_ENV) if _VALIDATOR_ENV else None

# 10섹션 Body Template.
BODY_SECTIONS = [
    "## Done", "## Open", "## Failed Attempts", "## Not Tried Yet",
    "## Blockers And Questions",
    "## Git State", "## Files Touched", "## Decisions", "## Exact Next Step",
    "## Verification",
]
# detail frontmatter 9키 (status 는 4-value 로 확장).
FRONT_KEYS = [
    "topic:", "created:", "project_root:", "status:", "prev:", "source:",
    "git_branch:", "git_commit:", "git_dirty:",
]
# Claude 어댑터가 보존해야 할 모드·가드 표면.
ADAPTER_MARKERS = [
    "list --all", "find --global", "--root", "resume", "archive",
    "Legacy Migration", "Durable Memory", "project root:", "handoff store:",
    "동시", "git drift", "Not Tried Yet", "확인 증거",
]


class AdapterTests(HandoffTestCase):

    def test_22_quick_validate_passes(self):
        if VALIDATOR is None or not VALIDATOR.exists():
            self.skipTest("HANDOFF_QUICK_VALIDATE 미설정/미존재 — skip")
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"  # Windows 기본 인코딩(cp949) 우회 — validator read_text.
        proc = subprocess.run(
            [sys.executable, str(VALIDATOR), str(REPO_ROOT / "codex" / "handoff")],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
        )
        self.assertEqual(proc.returncode, 0, f"validator 실패: {proc.stdout}\n{proc.stderr}")

    def test_23_body_template_preserved(self):
        root = self.make_git_project()
        self.save(self.payload("topic-a"), root)
        tdir = detail.topic_dir(root, "topic-a")
        target = detail.read_latest_target(tdir)
        body = (tdir / target).read_text(encoding="utf-8")
        for section in BODY_SECTIONS:
            self.assertIn(section, body, f"필수 항목 누락: {section} 누락")
        front_block = body.split("---", 2)[1]
        for key in FRONT_KEYS:
            self.assertIn(key, front_block, f"필수 항목 누락: frontmatter {key} 누락")

    def test_23_adapter_markers_preserved(self):
        adapter = (REPO_ROOT / "claude" / "handoff.md").read_text(encoding="utf-8")
        for marker in ADAPTER_MARKERS:
            self.assertIn(marker, adapter, f"필수 항목 누락: Claude 어댑터에 '{marker}' 누락")
