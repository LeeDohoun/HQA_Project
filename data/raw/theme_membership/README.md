# Theme Membership Evidence

이 폴더는 백테스트용 point-in-time 테마 편입 근거를 저장합니다.

기존 `data/raw/theme_targets/<theme_key>.jsonl`은 현재 시점의 테마 종목 목록입니다. 백테스트에서는 현재 목록만 사용하면 미래에 편입된 종목이 과거 후보군에 들어갈 수 있으므로, 이 폴더의 멤버십 이력을 사용해 `as_of_date` 기준 후보군을 제한합니다.

## 현재 데이터

- `ai.jsonl`: AI 테마 멤버십 이력
- `ai.meta.json`: 생성 방식과 주의사항

## 주의사항

현재 `source=local_corpus_inferred` 행은 공식 과거 테마 편입 이력이 아닙니다. 로컬 AI 테마 corpus에서 종목이 처음 관측된 날짜를 기준으로 추정한 보수적 편입 근거입니다. `first_seen_at`은 백테스트 편입 시작일로 쓰고, `last_observed_at`은 마지막 증거일입니다. `last_seen_at`은 실제 편입 종료일을 알 때만 채웁니다.

공식 또는 포털 스냅샷 기반 과거 편입 이력을 확보하면 같은 schema로 교체하거나 source를 구분해 병합할 수 있습니다.
