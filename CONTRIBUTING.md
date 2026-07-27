# Contributing

> Short English intro: this project is maintained solo, best-effort. Bug
> reports and PRs are welcome in Korean or English. Please run the smoke check
> in both default and `HANDOFF_LANG=en` modes before opening a PR, and keep
> changes scoped to the issue you're fixing.

이 문서는 jk-handoff 에 기여하는 방법을 정리한다.

## 검증 실행

```bash
python -m pip install -e .
python .github/smoke.py
```

스모크는 CLI 를 실제 서브프로세스로 불러 `save` → 본문 11섹션 조립 → `resume` → `list`
왕복이 되는지 **산출물을 직접 단언**한다. 실패하면 비-0 으로 죽고 무엇이 어긋났는지 출력한다.

Windows 에서는 cp949 로케일 때문에 CLI 의 JSON/유니코드 출력이 깨질 수 있다. 실행 전
`PYTHONUTF8=1` 을 설정한다.

```bash
# bash
PYTHONUTF8=1 python .github/smoke.py

# PowerShell
$env:PYTHONUTF8 = "1"
python .github/smoke.py
```

> 참고: 유지보수자는 이 스모크보다 넓은 회귀 스위트를 별도로 돌린다. 그 스위트는 배포본에
> 포함되지 않으므로, PR 이 올라오면 유지보수자 쪽에서 함께 확인한다.

## PR 기대사항

- **스모크가 두 언어 모드에서 모두 통과해야 한다** — 기본 모드와 `HANDOFF_LANG=en` 모드.

  ```bash
  PYTHONUTF8=1 python .github/smoke.py
  PYTHONUTF8=1 HANDOFF_LANG=en python .github/smoke.py
  ```

- **변경 범위를 좁게 유지한다.** 하나의 PR은 하나의 이슈/기능만 다룬다. 관련 없는 리팩터링이나
  포맷팅 변경을 함께 묶지 않는다.
- 어댑터(`claude/handoff.md`, `codex/handoff/SKILL.md`)를 수정할 때는 두 어댑터의 문구를
  동일하게 유지한다 — 저장 포맷(섹션 헤딩·순서·frontmatter 키)이 writer 와 무관하게 같아야
  하는 계약이며, 유지보수자 쪽 회귀 스위트가 이를 검증한다.
- **PowerShell 에서 JSON 페이로드를 파이프로 넘기지 않는다.** PS 5.1 파이프가 UTF-8 BOM 을
  끼워 넣는 경우가 있어(실측) 받는 쪽 파싱이 깨진다. `save --input <file>` 로 파일을 넘긴다.
- 커밋 메시지는 간결하게, 무엇을 왜 바꿨는지 설명한다.

## 이슈

버그 리포트·기능 제안은 한국어·영어 모두 환영한다. 재현 가능한 최소 예시가 있으면 처리가
빨라진다.

## 유지보수 안내

이 프로젝트는 **1인 유지보수(best-effort)** 로 운영된다. 응답이 늦을 수 있는 점 양해 바란다.

## 라이선스

기여한 코드는 이 프로젝트의 [MIT 라이선스](LICENSE)를 따른다.
