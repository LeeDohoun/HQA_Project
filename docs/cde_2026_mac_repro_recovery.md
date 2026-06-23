# CDE 2026 재현성 복구 작업 기록

작성: 2026-06-23  
완료: 2026-06-23

## 목적

논문 Table 2 수치를 만든 **원본 백테스트 실행 파일(raw `result_json`) 50개**가
현재 워크스페이스에는 없었으나, 원본 맥 저장소
(`/Users/leedohoun/Desktop/HQA_Project/...`)에 남아 있는 것을 확인했다.
동일 상대경로로 복사하고 manifest를 재생성하여 재현성 상태를
`18/68`에서 `68/68`로 보강했다.

- 표 숫자 자체는 이미 보존된 요약 산출물
  (`artifacts/paper_backtesting_exports/ai-strategy-comparison.json`)과 일치함을 확인했다.
- 따라서 이 작업은 **논문 수정이 아니라 "완전 재현 패키지" 보강**이다. 표/주장은 그대로다.

## 누락 파일 목록

`docs/cde_2026_reproducibility_manifest.md`의 "Missing Result JSON Files" 50개.
모두 아래 디렉터리 하위에 있다.

```
experiment_results/backtesting/ai_strategy_comparison/
```

## 실행한 절차

```bash
# 1) 복구 전 누락 개수 확인
python scripts/build_cde_paper_evidence_audits.py
grep "Missing in workspace" docs/cde_2026_reproducibility_manifest.md
#   -> Missing in workspace: 50

# 2) 원본 저장소의 동일 상대경로에서 누락 raw JSON 50개 복사
#    source: /Users/leedohoun/Desktop/HQA_Project/
#    target: /Users/leedohoun/Desktop/HQA_Project_cde_2026/

# 3) manifest 재생성 후 0 확인
python scripts/build_cde_paper_evidence_audits.py
grep "Missing in workspace" docs/cde_2026_reproducibility_manifest.md
#   -> Missing in workspace: 0
```

## 검증 포인트

- `docs/cde_2026_reproducibility_manifest.md`: `Present in workspace: 68`, `Missing in workspace: 0`.
- `docs/cde_2026_submission_readiness.md`: `Raw-run reproducibility`를 `Pass`로 갱신했다.
- 표 값 자체는 이미 보존된 요약 산출물과 일치하므로 논문 본문 수치는 변경하지 않았다.

## 주의

- `scripts/build_cde_paper_evidence_audits.py`는 manifest를 **통째로 다시 쓴다.**
  따라서 manifest 본문에는 수동 메모를 남기지 말 것(지워짐). 메모는 이 파일에 둔다.
- raw JSON은 재실행으로 새로 만든 것이 아니라 원본 실행 산출물을 복구한 것이다.
