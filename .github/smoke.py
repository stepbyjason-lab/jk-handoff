"""공개본 스모크 — 내부 회귀 스위트(tests/)는 공개하지 않으므로 이것이 공개 검증이다.

`unittest discover -s tests` 를 그대로 쓰면 tests 가 없는 트리에서 **0개를 찾고도 OK** 로
통과해 거짓 초록불이 된다. 그래서 이 스크립트는 산출물을 직접 단언한다 — 실패하면 반드시
비-0 으로 죽는다.

검증 범위: 설치된 CLI 가 save → 본문 조립 → resume → list 왕복을 해내는가.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PY = sys.executable
SECTIONS = [
    "## Done",
    "## Open",
    "## Failed Attempts",
    "## Not Tried Yet",
    "## Blockers And Questions",
    "## Git State",
    "## Files Touched",
    "## Decisions",
    "## Unapproved Proposals",
    "## Exact Next Step",
    "## Verification",
]


def fail(msg: str) -> None:
    print(f"SMOKE FAIL: {msg}", file=sys.stderr)
    raise SystemExit(1)


def run(root: str, *args: str) -> dict:
    """CLI 를 서브프로세스로 호출하고 JSON 을 돌려받는다(실제 인터op 경계 그대로)."""
    proc = subprocess.run(
        [PY, "-m", "handoff_cli", "--cwd", root, *args],
        capture_output=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        fail(f"`{' '.join(args)}` exit={proc.returncode}\nstderr:\n{proc.stderr}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        fail(f"`{' '.join(args)}` 출력이 JSON 이 아니다: {exc}\nstdout:\n{proc.stdout!r}")
        raise  # pragma: no cover


def git(root: str, *args: str) -> None:
    subprocess.run(["git", "-C", root, *args], check=True,
                   capture_output=True, encoding="utf-8", errors="replace")


def main() -> int:
    lang = os.environ.get("HANDOFF_LANG", "(unset)")
    print(f"smoke: python={sys.version.split()[0]} HANDOFF_LANG={lang}")

    with tempfile.TemporaryDirectory(prefix="handoff-smoke-") as tmp:
        root = os.path.realpath(tmp)
        git(root, "init", "-q")
        git(root, "config", "user.email", "smoke@example.com")
        git(root, "config", "user.name", "smoke")
        Path(root, "f.txt").write_text("x\n", encoding="utf-8")
        git(root, "add", "f.txt")
        git(root, "commit", "-qm", "init")

        # 1) save — 페이로드는 파일로 넘긴다(파이프 인코딩에 의존하지 않는다).
        payload = {
            "topic": "smoke-topic",
            "source": "claude-code",
            "status": "active",
            "summary": "smoke roundtrip",
            "sections": {"done": "- built", "exact_next_step": "- verify resume"},
            "files_touched": [{"path": "f.txt", "state": "complete"}],
        }
        pfile = Path(root) / "payload.json"
        pfile.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        saved = run(root, "save", "--input", str(pfile))
        if not saved.get("ok"):
            fail(f"save ok != True: {saved}")
        if saved.get("topic") != "smoke-topic":
            fail(f"topic 불일치: {saved.get('topic')}")
        for key in ("report", "resume_prompt", "detail_path"):
            if not saved.get(key):
                fail(f"save 결과에 {key} 없음")

        # 2) 저장된 본문에 11 섹션이 모두 조립됐는가
        detail = Path(root) / saved["detail_path"]
        if not detail.exists():
            fail(f"정본 파일 없음: {detail}")
        body = detail.read_text(encoding="utf-8")
        missing = [h for h in SECTIONS if h not in body]
        if missing:
            fail(f"본문에 누락된 섹션: {missing}")

        # 3) resume — 스코프 가드와 본문이 돌아오는가
        resumed = run(root, "resume", "--topic", "smoke-topic")
        if not resumed.get("found"):
            fail(f"resume found != True: {resumed}")
        if not resumed.get("scope_guard"):
            fail("resume 결과에 scope_guard 없음")
        if "smoke-topic" not in resumed["scope_guard"]:
            fail(f"scope_guard 에 토픽명이 없다: {resumed['scope_guard']!r}")
        if "## Done" not in (resumed.get("body") or ""):
            fail("resume body 가 비었거나 섹션이 없다")

        # 4) list — 방금 만든 토픽이 잡히는가
        listed = run(root, "list")
        topics = [t.get("topic") for t in listed.get("topics", [])]
        if "smoke-topic" not in topics:
            fail(f"list 에 smoke-topic 없음: {topics}")

    print("smoke: OK (save · 11 sections · resume · list)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
