# Contributing

> Short English intro: this project is maintained solo, best-effort. Bug
> reports and PRs are welcome in Korean or English. Please run the test suite
> in both default and `HANDOFF_LANG=en` modes before opening a PR, and keep
> changes scoped to the issue you're fixing.

이 문서는 jk-handoff 에 기여하는 방법을 정리한다.

## 테스트 실행

```bash
python -m unittest discover -s tests
```

Windows 에서는 cp949 로케일 때문에 CLI 의 JSON/유니코드 출력이 깨질 수 있다. 실행 전
`PYTHONUTF8=1` 을 설정한다.

```bash
# bash
PYTHONUTF8=1 python -m unittest discover -s tests

# PowerShell
$env:PYTHONUTF8 = "1"
python -m unittest discover -s tests
```

## PR 기대사항

- **테스트는 두 언어 모드에서 모두 통과해야 한다** — 기본 모드와 `HANDOFF_LANG=en` 모드.

  ```bash
  PYTHONUTF8=1 python -m unittest discover -s tests
  PYTHONUTF8=1 HANDOFF_LANG=en python -m unittest discover -s tests
  ```

- **변경 범위를 좁게 유지한다.** 하나의 PR은 하나의 이슈/기능만 다룬다. 관련 없는 리팩터링이나
  포맷팅 변경을 함께 묶지 않는다.
- 어댑터(`claude/handoff.md`, `codex/handoff/SKILL.md`)를 수정할 때는 두 어댑터의 문구를
  동일하게 유지한다 — on-disk 출력이 바이트 동일해야 하는 계약(`tests/test_11_adapters.py`)이
  이를 검증한다.
- 커밋 메시지는 간결하게, 무엇을 왜 바꿨는지 설명한다.

## 이슈

버그 리포트·기능 제안은 한국어·영어 모두 환영한다. 재현 가능한 최소 예시가 있으면 처리가
빨라진다.

## 유지보수 안내

이 프로젝트는 **1인 유지보수(best-effort)** 로 운영된다. 응답이 늦을 수 있는 점 양해 바란다.

## 라이선스

기여한 코드는 이 프로젝트의 [MIT 라이선스](LICENSE)를 따른다.
