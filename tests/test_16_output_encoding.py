"""케이스 24: CLI JSON 출력이 cp949 locale 에서도 truncate 되지 않는다.

근본 원인(__main__.py): json.dump(sys.stdout, ensure_ascii=False) 가 cp949 stdout 에
비-cp949 문자(이모지)를 쓰면 UnicodeEncodeError 로 출력이 중간에 잘린다. main() 진입부의
stdout/stderr UTF-8 reconfigure 가 이를 막는지 서브프로세스로 검증한다.

cp949 codec 은 전 OS 내장이라 PYTHONIOENCODING=cp949 로 어느 플랫폼에서든 재현 가능.
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path[:0] = [str(_Path(__file__).resolve().parent.parent / "core"),
                 str(_Path(__file__).resolve().parent)]

import json
import os
import subprocess
import sys

from helpers import HandoffTestCase

REPO_ROOT = _Path(__file__).resolve().parent.parent
CORE_DIR = REPO_ROOT / "core"

# cp949 로 인코딩 불가능한 문자들(이모지) — save.report·resume 본문에 흔히 등장.
NON_CP949 = "완료 ✅ 경고 ⚠ 목록 📋 보류 🔵"


class OutputEncodingTests(HandoffTestCase):

    def _run_cli(self, root: str, *args: str,
                 input_text: str | None = None) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        # 라이브 어댑터가 PYTHONUTF8 을 안 붙인 최악 조건을 강제 재현.
        env.pop("PYTHONUTF8", None)
        env["PYTHONIOENCODING"] = "cp949"
        env["PYTHONPATH"] = os.pathsep.join(
            [str(CORE_DIR), env.get("PYTHONPATH", "")]
        ).rstrip(os.pathsep)
        return subprocess.run(
            [sys.executable, "-m", "handoff_cli", "--cwd", root, *args],
            input=input_text,
            capture_output=True, encoding="utf-8", errors="replace", env=env,
        )

    def test_24_resume_emoji_body_not_truncated(self):
        root = self.make_git_project()
        # 본문에 비-cp949 이모지를 심는다(summary + done 섹션).
        self.save(self.payload("emoji-topic", summary=NON_CP949, done=f"- {NON_CP949}"), root)

        proc = self._run_cli(root, "resume", "--topic", "emoji-topic")

        self.assertEqual(
            proc.returncode, 0,
            f"CLI 비정상 종료(cp949 crash 의심). stderr=\n{proc.stderr}",
        )
        # 핵심: 잘리지 않은 유효한 JSON 이어야 한다.
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:  # pragma: no cover - 실패 경로
            self.fail(f"stdout 이 유효 JSON 이 아님(truncate 의심): {exc}\n"
                      f"--- stdout ---\n{proc.stdout!r}")
        self.assertTrue(data.get("found"), f"resume found != True: {data}")
        self.assertEqual(data.get("command"), "resume")

    def test_24_save_report_emoji_not_truncated(self):
        root = self.make_git_project()
        payload = self.payload("save-emoji", summary=NON_CP949, done="- 끝")
        # save 입력(stdin)에도 비-cp949 문자 → 입력·출력 양쪽 경로를 동시 검증.
        proc = self._run_cli(root, "save",
                             input_text=json.dumps(payload, ensure_ascii=False))
        self.assertEqual(proc.returncode, 0, f"save crash. stderr=\n{proc.stderr}")
        data = json.loads(proc.stdout)  # report 에 ✅📋⚠ 포함 → truncate 시 여기서 실패
        self.assertTrue(data.get("ok"))
        self.assertIn("✅", data.get("report", ""))
