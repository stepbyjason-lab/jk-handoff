"""status taxonomy 정규화.

신 taxonomy: active / waiting / watching / done.
기존 detail 은 신 taxonomy 를 모르므로 기존 값을 정규화해 마이그레이션 없이 호환한다.
- open, open_planning → active
- done, closed, CLOSED → done (→archived 제외)
- waiting, watching, active → 그대로
- 그 외 미인식 → active + 경고 (조용히 버리지 않음)

fallback 정규식(`CLOSED|closed|done`, detail._read_topic_summary 사용)은 이미 영문
리터럴이라 언어중립 — 건드리지 않는다.
"""

from __future__ import annotations

from . import messages

__all__ = ["normalize_status", "GROUP_HEADINGS", "ACTIVE_GROUPS"]

# 신/구 값 → 정규화된 그룹.
_MAP = {
    "active": "active",
    "open": "active",
    "open_planning": "active",
    "in_progress": "active",   # 레거시 실데이터에서 관측
    "in-progress": "active",
    "wip": "active",
    "waiting": "waiting",
    "paused": "waiting",       # 레거시 — 멈춘 작업은 대기로 집계
    "watching": "watching",
    "done": "done",
    "closed": "done",
}

# CURRENT.md 본문 그룹 제목 (인덱스에 노출되는 active 계열만). lang 별 헤딩은
# messages.GROUP_HEADINGS 에 있다 — 이 상수는 하위호환용 ko 기본값으로 유지.
GROUP_HEADINGS = messages.GROUP_HEADINGS["ko"]
ACTIVE_GROUPS = ("active", "waiting", "watching")


def normalize_status(raw: str | None, lang: str = "ko") -> tuple[str, str | None]:
    """`(normalized, warning)` 를 돌려준다.

    normalized ∈ {active, waiting, watching, done}. 미인식 값은 active 로
    취급하고 경고 문자열을 함께 돌려준다(lang 별).
    """
    value = (raw or "active").strip()
    key = value.lower()
    if key in _MAP:
        return _MAP[key], None
    return "active", messages.msg("warn_unrecognized_status", lang, value=value)
