# 2026-10-10 커밋 이력 재작성 기록

## 무엇을 바꿨나

`research/experiments/B001_disclosure_category/results/20261005T000205501551Z.json`(114,297,859바이트)은
GitHub의 파일 크기 한도(100MB)를 넘어 푸시할 수 없었습니다. 이 파일을 내용이 같은 gzip 압축본
`20261005T000205501551Z.json.gz`(11,592,806바이트, `gzip -9 -n`)로 바꿔, 그 파일이 처음 커밋된
`f6cc363`부터 이후 커밋 52개를 다시 만들었습니다. 각 커밋의 작성자, 시각, 메시지는 그대로이고, 바뀐
파일은 위 결과 파일 하나뿐입니다.

## 실험 기록에 미치는 영향

커밋 해시가 바뀌면서, 그 뒤에 커밋된 사전 등록 7개의 첫 커밋 해시도 바뀌었습니다. 실험 대장
(`registry.csv`)의 기존 행은 고치지 않았습니다. 대신 `commit_map.csv`에 옛 해시, 새 해시, 사전 등록
파일의 blob 해시를 기록했습니다. `backtesting/experiment_registry.py`는 새 해시가 현재 사전 등록 커밋과
같고 blob 해시가 커밋된 파일과 같을 때만 옛 해시를 같은 계획으로 인정합니다. 사전 등록 파일 내용은
7개 모두 재작성 전후로 바이트 단위까지 같습니다.

| 실험 | 옛 커밋 | 새 커밋 |
| --- | --- | --- |
| HC002_operating_leverage_monthly | c660be3 | 71ed9ca |
| LH001_llm_hegemony_judge | d17d240 | c4b43ff |
| LH002_llm_hegemony_judge | 572b5c9 | 0fac519 |
| HF001_hegemony_filters | 8fafcf8 | bd0fcf1 |
| R001_industry_adjusted_reversal | 8fafcf8 | bd0fcf1 |
| BB001_direct_buyback | 5846eba | 833be47 |
| HC003_operating_leverage_turnover | d96533b | 1db3fe4 |

D001, B001, HC001, HI001의 사전 등록은 재작성 지점 이전에 커밋되어 해시가 바뀌지 않았습니다.

결과 파일과 결과 문서 안에 적힌 커밋 해시(옛 값)도 고치지 않았습니다. 위 표로 대응시켜 읽으면 됩니다.
압축하지 않은 원본은 작성자 PC의 `data/research/archive/`에 보관합니다.
