"""결정적·원자적 파일 쓰기.

Windows에서 `Move-Item`/단순 rename 은 기존 파일 위 원자교체가 아니므로
`os.replace()` 를 쓴다. CPython 의 `os.replace` 는 Windows에서 ReplaceFile 의미로
같은 볼륨 내 atomic replace 를 보장한다. shutil.move 는 쓰지 않는다.

모든 텍스트는 `\n` 개행 + UTF-8 로 쓴다 (CRLF 로 인한 바이트 차이/idempotence 회귀 방지).
"""

from __future__ import annotations

import os
import tempfile

__all__ = ["atomic_write_text"]


def atomic_write_text(path: str, text: str, encoding: str = "utf-8") -> None:
    """`text` 를 `path` 에 원자적으로 쓴다.

    같은 디렉토리에 임시파일을 만들고 flush+fsync 후 `os.replace` 로 교체한다.
    교체가 실패하면 임시파일을 제거하고 예외를 재전파한다 — 기존 `path` 는
    절대 손상되지 않는다(교체 전에는 건드리지 않으므로).
    """
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".handoff-tmp-", suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        # 교체 실패 시 임시파일을 남기지 않는다.
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
