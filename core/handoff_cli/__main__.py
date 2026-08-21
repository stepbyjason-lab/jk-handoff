"""CLI 진입점: `python -m handoff_cli <command> ...` (인터op 경계).

어댑터는 구조화 입력을 JSON 으로 넘기고(`save` 는 stdin/`--input`), 결과를 JSON 으로
받는다. 모든 파일쓰기는 코어가 수행한다.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import cli

# UTF-8 BOM(U+FEFF). 소스에 리터럴 문자를 박지 않는다 — 보이지 않아서 에디터·인코딩
# 왕복에서 조용히 사라지거나 이중 이스케이프되기 쉽다.
_BOM = chr(0xFEFF)


def _read_payload(args) -> dict:
    if args.input:
        # utf-8-sig: Windows PowerShell `Set-Content -Encoding UTF8` 이 붙이는 BOM 을
        # 투명하게 벗긴다. BOM 없는 UTF-8 도 그대로 읽힌다.
        with open(args.input, encoding="utf-8-sig") as handle:
            return json.load(handle)
    # stdin 도 같은 방어가 필요하다(비대칭이면 절반만 막힌다). PowerShell 5.1 에서
    # `$OutputEncoding = [System.Text.Encoding]::UTF8` 은 BOM 을 붙이는 인코딩이라,
    # 그 상태로 JSON 을 파이프하면 선두에 BOM 이 실려 오고 json.load 가
    # "Unexpected UTF-8 BOM" 으로 죽는다. 호출자의 인코딩 설정에 의존하지 않고 여기서 벗긴다.
    return json.loads(sys.stdin.read().lstrip(_BOM))


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
    # JSON 안에 3만 자 문자열이 들어 있으면 어댑터가 파일로 빼서 나눠 읽느라
    # 도구 호출이 5~7회로 불어난다(실측). 평문으로 내면 1회다.
    p_resume.add_argument("--directives-only", action="store_true",
                          help="resume_directives 만 평문으로 출력")

    p_arch = sub.add_parser("archive")
    p_arch.add_argument("--root", default=None)
    p_arch.add_argument("--topic", required=True)

    # 결정 색인 — `<id>` 하나로 그 결정의 일생(생사·관계·체인)을 낸다.
    p_dec = sub.add_parser("decisions")
    p_dec.add_argument("--root", default=None)
    p_dec.add_argument("--id", default=None, help="이 결정의 일생만 (생략하면 전부)")
    p_dec.add_argument("--all", action="store_true", help="archived 토픽도 포함")

    # 부정 색인 — 실패·폐기·죽은 결정만. 「이거 해봤나?」 에 답한다.
    p_neg = sub.add_parser("negative")
    p_neg.add_argument("--root", default=None)
    p_neg.add_argument("--all", action="store_true", help="archived 토픽도 포함")

    p_utt = sub.add_parser("utterances")
    p_utt.add_argument("--root", default=None)
    p_utt.add_argument("--session", required=True, help="저작 세션 id")
    p_utt.add_argument("--transcript", default=None, help="트랜스크립트 경로(정본). 없으면 유도")
    p_utt.add_argument("--topic", default=None,
                       help="주면 그 토픽의 직전 저장본 이후만 (한 세션 두 번째 저장)")
    p_utt.add_argument("--since", default=None, help="ISO8601. --topic 유도보다 우선")
    # 범위를 사용자가 못박는다. 플래그가 있으면 기존 저장본이 있든 없든 그대로 실행한다 —
    # 「중복인가」를 모델이 추론하다 저장을 거부한 사고가 있었다(2026-08-17).
    g_scope = p_utt.add_mutually_exclusive_group()
    g_scope.add_argument("--delta", dest="scope", action="store_const", const="delta",
                         help="직전 저장 이후만. 직전 저장이 다른 세션이어도 강제한다")
    g_scope.add_argument("--full", dest="scope", action="store_const", const="full",
                         help="세션 전체. 직전 저장이 있어도 무시하고 1번부터 다시 센다")
    p_utt.set_defaults(scope="auto")
    p_utt.add_argument("--format", dest="fmt", choices=("claude", "codex"), default="claude",
                       help="트랜스크립트 형식. 어댑터가 명시한다(코어가 추측하지 않는다)")

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
        if getattr(args, "directives_only", False):
            print(result.get("resume_directives") or "")
            return 0
    elif args.command == "archive":
        result = cli.cmd_archive(cwd, args.topic, args.root)
    elif args.command == "decisions":
        result = cli.cmd_decisions(cwd, args.root, args.id, include_archived=args.all)
    elif args.command == "negative":
        result = cli.cmd_negative(cwd, args.root, include_archived=args.all)
    elif args.command == "utterances":
        result = cli.cmd_utterances(cwd, args.session, args.root, args.transcript,
                                    topic=args.topic, since=args.since, fmt=args.fmt,
                                    scope=args.scope)
    elif args.command == "reindex":
        result = cli.cmd_reindex(cwd, args.root, args.global_root, args.source)
    else:  # pragma: no cover
        parser.error(f"알 수 없는 명령: {args.command}")

    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
