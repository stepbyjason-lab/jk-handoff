"""공용 handoff CLI 코어.

Claude command 어댑터와 Codex skill 어댑터가 공유하는 단일 실행가능 코어다.
두 어댑터는 narrative 생성과 구조화 JSON 입력 전달만 담당하고, 상세 본문 ·
LATEST.md · INDEX.md · 글로벌 CURRENT.md 의 모든 파일쓰기는 이 패키지에 위임한다.

설계 정본: ../../.handoff/handoff-redesign-implementation-contract.md (v2.1)
"""

__all__ = ["__version__"]

__version__ = "0.2.0"
