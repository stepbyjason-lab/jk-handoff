"""secret 차단 + 크로스호스트 가드.

secret 차단: 글로벌 CURRENT.md 후보 내용에 패턴이 적중하면 해당 라인을
`[REDACTED]` 로 치환하고 경고한다(코드 게이트, 규칙 아님). git 히스토리는 안 지움.

크로스호스트 가드: `/handoff` 는 네트워크(fetch/pull/push)를 일절 쓰지 않는다.
글로벌 `~/.claude` 를 읽기 전용으로만 검사 — 충돌마커 / divergent project_id /
로컬 known remote-tracking ref 기준 ahead. 하나라도 걸리면 글로벌 CURRENT.md
갱신만 skip + 경고. 상세 정본은 이미 저장됨.
"""

from __future__ import annotations

import re

from . import messages, repo

__all__ = [
    "redact_secrets",
    "REDACTION",
    "has_conflict_markers",
    "remote_ahead",
]

# 기본(하위호환) 리터럴 — lang 인자를 받는 redact_secrets() 는 lang 별로 다른 문자열을
# 만들어 쓴다. 이 상수는 REDACTION 을 직접 참조하는 외부 호출부용 ko 기본값.
REDACTION = "[REDACTED — 잠재 secret, 수동 편집]"
_REDACTION_TEXT = {
    "ko": REDACTION,
    "en": "[REDACTED — potential secret, manual review]",
}

# 문맥 기반으로 좁혀 false-positive 를 줄인다. 일반 단어("token 구현",
# "password 화면")는 redact 하지 않고, 키=값 형태나 실제 키 프리픽스만 잡는다.
_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{8,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{8,}"),
    re.compile(r"-----BEGIN"),
    re.compile(r"(?i)(api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|"
               r"secret[_-]?key|private[_-]?key)\s*[:=]\s*\S+"),
    re.compile(r"(?i)\b(password|passwd|token|secret)\b\s*[:=]\s*\S+"),
    re.compile(r"\b[0-9a-fA-F]{32,}\b"),          # hex >= 32 (32자 MD5/토큰 포함)
    re.compile(r"\b[A-Za-z0-9+/]{44,}={0,2}\b"),  # base64-ish >= 44 (32바이트 인코딩 포함)
]

# `<<<<<<<` / `=======` / `>>>>>>>` 머지충돌 마커.
_CONFLICT_RE = re.compile(r"^(<{7}|={7}|>{7})", re.MULTILINE)


def redact_secrets(text: str, skip_prefixes: tuple[str, ...] = (),
                   lang: str = "ko") -> tuple[str, list[str]]:
    """`(redacted_text, warnings)`. 적중 라인은 통째로 `[REDACTED]` 로 치환.

    `skip_prefixes` 로 시작하는 라인은 스캔 제외 — CLI 가 생성한 구조 라인(인덱스
    제목·고정 안내문·그룹 헤딩)은 사용자 secret 이 아니므로, 프로젝트 basename 이
    긴 hex/UUID 일 때 제목이 오탐 redaction 되는 것을 막는다. 사용자 content
    (요약·최근변경)는 계속 스캔된다.
    """
    redaction_text = _REDACTION_TEXT.get(lang, REDACTION)
    warnings: list[str] = []
    out_lines: list[str] = []
    for idx, line in enumerate(text.splitlines(), start=1):
        if skip_prefixes and line.lstrip().startswith(skip_prefixes):
            out_lines.append(line)
            continue
        if any(pattern.search(line) for pattern in _SECRET_PATTERNS):
            out_lines.append(redaction_text)
            warnings.append(messages.msg("warn_secret_redacted", lang, idx=idx))
        else:
            out_lines.append(line)
    redacted = "\n".join(out_lines)
    if text.endswith("\n"):
        redacted += "\n"
    return redacted, warnings


def has_conflict_markers(text: str) -> bool:
    return bool(_CONFLICT_RE.search(text or ""))


def remote_ahead(global_root: str) -> bool:
    """로컬에 **이미 알려진** remote-tracking ref(`@{u}`) 가 HEAD 보다 앞서는지.

    **fetch 하지 않는다** — 마지막 sync 가 남긴 ref 만 비교(`rev-list`, 로컬 전용).
    upstream 미설정이면 False.
    """
    proc = repo.run_git(global_root, "rev-list", "--count", "HEAD..@{u}")
    if proc.returncode != 0:
        return False
    try:
        return int(proc.stdout.strip() or "0") > 0
    except ValueError:
        return False
