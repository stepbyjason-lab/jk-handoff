"""Windows 원자교체 실패주입 — 원본 무손상 + temp 잔존 금지."""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path[:0] = [str(_Path(__file__).resolve().parent.parent / "core"),
                 str(_Path(__file__).resolve().parent)]

import os
from pathlib import Path

from handoff_cli import atomicio
from helpers import HandoffTestCase


class AtomicTests(HandoffTestCase):

    def test_20_replace_fault_keeps_original(self):
        target = Path(self.tmp) / "CURRENT.md"
        original_bytes = "원본 내용 — 손상 금지\n".encode("utf-8")
        target.write_bytes(original_bytes)

        real_replace = os.replace

        def boom(src, dst):
            raise OSError("injected replace failure")

        os.replace = boom
        try:
            with self.assertRaises(OSError):
                atomicio.atomic_write_text(str(target), "새 내용 — 절대 반영되면 안 됨\n")
        finally:
            os.replace = real_replace

        # 원본 바이트 무손상.
        self.assertEqual(target.read_bytes(), original_bytes)
        # temp 파일 잔존 없음.
        leftovers = [p for p in Path(self.tmp).iterdir() if p.name.startswith(".handoff-tmp-")]
        self.assertEqual(leftovers, [], f"임시파일이 남았다: {leftovers}")
