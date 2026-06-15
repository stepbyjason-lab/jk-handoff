"""회귀: find --global 트리 확장 · source 검증 · git_meta 무커밋."""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path[:0] = [str(_Path(__file__).resolve().parent.parent / "core"),
                 str(_Path(__file__).resolve().parent)]

import json
import os
import subprocess
import sys
from unittest import mock

from handoff_cli import cli, detail, repo
from helpers import HandoffTestCase

_REPO = _Path(__file__).resolve().parent.parent


class ReviewFollowupTests(HandoffTestCase):

    def test_codex_default_global_root_is_codex_local(self):
        root = self.make_git_project()
        fake_home = _Path(self.tmp) / "home"

        def fake_expanduser(path: str) -> str:
            if path == "~/.codex":
                return str(fake_home / ".codex")
            if path == "~/.claude":
                return str(fake_home / ".claude")
            return path

        with mock.patch("handoff_cli.cli.os.path.expanduser", side_effect=fake_expanduser):
            result = cli.cmd_save(self.payload("topic-c", source="codex"), root)

        codex_current = fake_home / ".codex" / "handoffs" / "demo-proj" / "CURRENT.md"
        claude_current = fake_home / ".claude" / "handoffs" / "demo-proj" / "CURRENT.md"
        self.assertTrue(codex_current.exists())
        self.assertFalse(claude_current.exists())
        self.assertEqual(_Path(result["global"]["path"]), codex_current)

    def test_claude_default_global_root_stays_claude_local(self):
        root = self.make_git_project()
        fake_home = _Path(self.tmp) / "home"

        def fake_expanduser(path: str) -> str:
            if path == "~/.codex":
                return str(fake_home / ".codex")
            if path == "~/.claude":
                return str(fake_home / ".claude")
            return path

        with mock.patch("handoff_cli.cli.os.path.expanduser", side_effect=fake_expanduser):
            result = cli.cmd_save(self.payload("topic-a", source="claude-code"), root)

        claude_current = fake_home / ".claude" / "handoffs" / "demo-proj" / "CURRENT.md"
        codex_current = fake_home / ".codex" / "handoffs" / "demo-proj" / "CURRENT.md"
        self.assertTrue(claude_current.exists())
        self.assertFalse(codex_current.exists())
        self.assertEqual(_Path(result["global"]["path"]), claude_current)

    def test_find_global_expands_tree(self):
        # 스코프 루트(예: ~/projects)를 넘기면 하위 트리의 모든 .handoff/ 를 검색해야 한다.
        scope = os.path.join(self.tmp, "code")
        # 직접 두 프로젝트 생성.
        a = os.path.join(scope, "proj-a")
        b = os.path.join(scope, "nested", "proj-b")
        os.makedirs(a); os.makedirs(b)
        for root in (a, b):
            for c in [["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]]:
                self.git(root, *c)
            (_Path(root) / "x.txt").write_text("x\n", encoding="utf-8")
            self.git(root, "add", "-A"); self.git(root, "commit", "-qm", "init")
        self.save(self.payload("topic-a", summary="UNIQUEKEY_AAA"), a)
        self.save(self.payload("topic-b", summary="UNIQUEKEY_BBB"), b)

        res = cli.cmd_find(a, "UNIQUEKEY", global_roots=[scope])
        paths = " ".join(m["path"] for m in res["matches"])
        self.assertIn("proj-a", paths, "스코프 하위 proj-a 검색됨")
        self.assertIn("proj-b", paths, "중첩된 proj-b 도 트리 확장으로 검색됨")
        self.assertTrue(res["read_only"])

    def test_source_validation_rejects_injection(self):
        root = self.make_git_project()
        # 미인식/주입성 source 는 claude-code 로 강등 + 경고.
        result = self.save({"topic": "topic-a", "source": "evil\nsecret: x",
                            "summary": "s", "status": "active",
                            "sections": {"done": "- x"}}, root)
        self.assertTrue(any("source" in w for w in result["warnings"]))
        tdir = detail.topic_dir(root, "topic-a")
        front, _ = detail.parse_frontmatter(
            (tdir / detail.read_latest_target(tdir)).read_text(encoding="utf-8"))
        self.assertEqual(front["source"], "claude-code", "주입성 source 강등")
        # codex 는 정상 허용.
        r2 = self.save(self.payload("topic-b", source="codex"), root)
        self.assertFalse(any("source" in w for w in r2["warnings"]))

    def test_git_meta_empty_repo_no_literal_head(self):
        # 커밋 0개 레포: git_commit/branch 가 리터럴 'HEAD' 가 아니라 None.
        root = os.path.join(self.tmp, "empty-repo")
        os.makedirs(root)
        for c in [["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]]:
            self.git(root, *c)
        meta = repo.git_meta(root)
        self.assertNotEqual(meta["commit"], "HEAD")
        self.assertNotEqual(meta["branch"], "HEAD")
        self.assertIsNone(meta["commit"])

    def test_p3_uuid_basename_title_not_redacted(self):
        # 32-hex basename 프로젝트에서 CURRENT.md 제목이 secret 오탐 redaction 안 됨.
        name = "proj-97ba47bf2e0b4594b3a0198c154b912f"
        root = self.make_git_project(name=name)
        self.save(self.payload("topic-a", summary="정상 요약", lang="ko"), root)
        text = self.read_current(name)
        self.assertIn(f"# {name} — 진행상황 인덱스", text, "구조 제목은 redact 되면 안 됨")
        self.assertNotIn("[REDACTED", text)

    def test_p2_save_input_utf8_bom(self):
        # --input 이 BOM UTF-8 JSON 을 읽어야 한다(PowerShell Set-Content -Encoding UTF8).
        root = self.make_git_project()
        payload = {"topic": "bomtopic", "source": "claude-code", "summary": "bom",
                   "status": "active", "sections": {"done": "- bom"}}
        infile = os.path.join(self.tmp, "payload.json")
        # utf-8-sig 로 써서 BOM 부착.
        with open(infile, "w", encoding="utf-8-sig") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        env = dict(os.environ)
        env["PYTHONPATH"] = str(_REPO / "core")
        env["PYTHONUTF8"] = "1"
        proc = subprocess.run(
            [sys.executable, "-m", "handoff_cli", "--cwd", root,
             "--global-root", os.path.join(self.tmp, "g"), "save", "--input", infile],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
        )
        self.assertEqual(proc.returncode, 0, f"BOM 입력 실패: {proc.stderr}")
        out = json.loads(proc.stdout)
        self.assertTrue(out["ok"])
        self.assertEqual(out["command"], "save")
