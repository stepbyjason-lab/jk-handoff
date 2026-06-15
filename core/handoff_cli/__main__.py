"""CLI 진입점: `python -m handoff_cli <command> ...` (인터op 경계).

어댑터는 구조화 입력을 JSON 으로 넘기고(`save` 는 stdin/`--input`), 결과를 JSON 으로
받는다. 모든 파일쓰기는 코어가 수행한다.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import cli


def _read_payload(args) -> dict:
    if args.input:
        # utf-8-sig: Windows PowerShell `Set-Content -Encoding UTF8` 이 붙이는 BOM 을
        # 투명하게 벗긴다. BOM 없는 UTF-8 도 그대로 읽힌다.
        with open(args.input, encoding="utf-8-sig") as handle:
            return json.load(handle)
    return json.load(sys.stdin)


def _force_utf8_streams() -> None:
    """표준 스트림을 UTF-8 로 고정한다.

    Windows 기본 locale(cp949)에서 sys.stdin/stdout/stderr 는 cp949 라, 입력·출력의
    비-cp949 문자(이모지 ✅⚠📋 등)에서 json.load/json.dump 가 UnicodeError 로 죽거나
    출력이 중간에 잘린다. 호출자(어댑터)의 PYTHONUTF8 설정에 의존하지 않고 여기서 고정한다.
    """
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except (ValueError, OSError) as exc:
                # 재구성을 시도했으나 실패(detach/broken fd) — 다운스트림 json.dump/load 가
                # 같은 원인으로 다시 죽을 수 있으니, 조용히 넘기지 않고 원본 stderr 에 단서를
                # 남긴다(ASCII 메시지라 cp949 에서도 안전). hasattr=False(예: StringIO)는
                # 인코딩 개념이 없어 무해하므로 경고하지 않는다.
                try:
                    sys.__stderr__.write(
                        f"[handoff_cli] WARN: {stream!r} reconfigure(utf-8) 실패: {exc!r}\n"
                    )
                except Exception:
                    pass


def main(argv=None) -> int:
    _force_utf8_streams()
    parser = argparse.ArgumentParser(prog="handoff_cli")
    parser.add_argument("--cwd", default=None, help="작업 디렉토리 (기본: 프로세스 cwd)")
    parser.add_argument(
        "--global-root",
        default=None,
        help="글로벌 루트 (기본: source=codex 이면 ~/.codex, 그 외 ~/.claude)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_save = sub.add_parser("save")
    p_save.add_argument("--input", default=None, help="JSON 페이로드 파일 (없으면 stdin)")

    p_list = sub.add_parser("list")
    p_list.add_argument("--root", default=None)
    p_list.add_argument("--all", action="store_true")

    p_find = sub.add_parser("find")
    p_find.add_argument("--root", default=None)
    p_find.add_argument("--keyword", required=True)
    p_find.add_argument("--global-scope", nargs="*", default=None,
                        help="read-only 로 검색할 추가 루트들")

    p_resume = sub.add_parser("resume")
    p_resume.add_argument("--root", default=None)
    p_resume.add_argument("--topic", required=True)

    p_arch = sub.add_parser("archive")
    p_arch.add_argument("--root", default=None)
    p_arch.add_argument("--topic", required=True)

    p_reindex = sub.add_parser("reindex")
    p_reindex.add_argument("--root", default=None)
    p_reindex.add_argument("--source", choices=("claude-code", "codex"), default="claude-code")

    args = parser.parse_args(argv)
    import os
    cwd = args.cwd or os.getcwd()

    if args.command == "save":
        result = cli.cmd_save(_read_payload(args), cwd, args.global_root)
    elif args.command == "list":
        result = cli.cmd_list(cwd, args.root, include_archived=args.all)
    elif args.command == "find":
        result = cli.cmd_find(cwd, args.keyword, args.root, args.global_scope)
    elif args.command == "resume":
        result = cli.cmd_resume(cwd, args.topic, args.root)
    elif args.command == "archive":
        result = cli.cmd_archive(cwd, args.topic, args.root)
    elif args.command == "reindex":
        result = cli.cmd_reindex(cwd, args.root, args.global_root, args.source)
    else:  # pragma: no cover
        parser.error(f"알 수 없는 명령: {args.command}")

    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
