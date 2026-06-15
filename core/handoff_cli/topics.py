"""토픽 slug 검증 + traversal 차단.

허용: 한글, 영문 소문자, 숫자, `-`, `_`. 공백→`-`, 영문 대문자→소문자.
거부: `/`, `\\`, `..`, 앞의 `.`, 절대경로 문자(`:`), null byte, UNC, 상위탈출.
"""

from __future__ import annotations

import re

__all__ = ["normalize_topic", "TopicError"]


class TopicError(ValueError):
    """토픽 검증 실패."""


# 허용 문자: 한글 음절(가-힣), ascii 소문자/숫자, `-`, `_`.
# (자모 낱자 범위는 파일명에서 음절과 다르게 표시될 수 있어 제외 — 공백은 이미
# 위에서 `-` 로 변환되므로 클래스에 공백 불필요.)
_ALLOWED_RE = re.compile(r"^[가-힣a-z0-9_-]+$")
_TRAVERSAL_TOKENS = ("..", "/", "\\", "\x00")


def normalize_topic(raw: str) -> str:
    """토픽을 정규화하거나 위험하면 `TopicError` 를 던진다.

    정규화는 거부 전에 적용한다: 공백→`-`, 영문 대문자→소문자. 그 뒤
    traversal/절대경로/앞점/빈문자열을 거부한다.
    """
    if raw is None:
        raise TopicError("토픽이 비어 있다.")
    if "\x00" in raw:
        raise TopicError("토픽에 null byte 가 있다.")

    candidate = raw.strip()
    candidate = re.sub(r"\s+", "-", candidate)
    candidate = candidate.lower()

    if not candidate:
        raise TopicError("토픽이 비어 있다.")

    for token in _TRAVERSAL_TOKENS:
        if token in candidate:
            raise TopicError(f"토픽에 경로 분리/traversal 문자가 있다: {token!r}")

    # `:` (드라이브/ADS `topic:stream`), 앞점, 절대경로 거부.
    if ":" in candidate:
        raise TopicError("토픽에 `:` (드라이브/스트림) 문자가 있다.")
    if candidate.startswith(".") or candidate.startswith("-"):
        raise TopicError("토픽은 `.` 또는 `-` 로 시작할 수 없다.")

    if not _ALLOWED_RE.match(candidate):
        raise TopicError(
            "토픽에 허용되지 않은 문자가 있다 (허용: 한글·영문 소문자·숫자·`-`·`_`)."
        )

    return candidate
