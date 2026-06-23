# CDE 2026 재현성 복구 작업 (맥에서 진행) — TODO

작성: 2026-06-23 (Windows 측 점검 후 인계)

## 목적

논문 Table 2 수치를 만든 **원본 백테스트 실행 파일(raw `result_json`) 50개**가
현재 Windows 워크스페이스에는 없다. 이 파일들은 원본 맥 저장소
(`/Users/leedohoun/Desktop/HQA_Project/...`)에는 남아 있으므로,
맥에서 동일 상대경로로 채워 넣고 manifest를 재생성하면
재현성 상태가 `18/68` → `68/68`로 완성된다.

- 표 숫자 자체는 이미 보존된 요약 산출물
  (`artifacts/paper_backtesting_exports/ai-strategy-comparison.json`)과 일치함을 확인했다.
- 따라서 이 작업은 **논문 수정이 아니라 "완전 재현 패키지" 보강**이다. 표/주장은 그대로다.

## 누락 파일 목록

`docs/cde_2026_reproducibility_manifest.md`의 "Missing Result JSON Files" 50개.
모두 아래 디렉터리 하위에 있다.

```
experiment_results/backtesting/ai_strategy_comparison/
```

## 맥에서 실행할 절차

```bash
# 0) 원본 저장소(cde_2026 브랜치)로 이동
cd ~/Desktop/HQA_Project
git checkout cde_2026

# 1) 현재 누락 개수 확인
python scripts/build_cde_paper_evidence_audits.py
grep "Missing in workspace" docs/cde_2026_reproducibility_manifest.md
#   -> "Missing in workspace: 50" 이면 아래로 진행

# 2) 누락 raw JSON 채우기
#   경우 A) 맥 워크트리에 파일이 이미 있고 git에 안 올라간(ignored/untracked) 경우:
git add -f experiment_results/backtesting/ai_strategy_comparison

#   경우 B) 별도 백업/다른 경로에서 가져와야 하면, 동일 상대경로로 복사:
#   rsync -av /원본경로/HQA_Project/experiment_results/backtesting/ai_strategy_comparison/ \
#             experiment_results/backtesting/ai_strategy_comparison/

# 3) manifest 재생성 후 0 확인 (스크립트가 manifest를 자동으로 덮어씀)
python scripts/build_cde_paper_evidence_audits.py
grep "Missing in workspace" docs/cde_2026_reproducibility_manifest.md
#   -> "Missing in workspace: 0" 이어야 성공

# 4) 커밋 (그래야 Windows/OneDrive 쪽으로도 동기화됨)
git add experiment_results docs/cde_2026_reproducibility_manifest.md
git commit -m "data: restore raw backtest result_json for CDE 2026 reproducibility"
```

## 검증 포인트

- `Missing in workspace: 0` 이 되면 재현성 보강 완료.
- (선택) 표 값 재확인이 필요하면 DOCX를 다시 빌드한다. 단 Table 2 값은 이미 일치하므로 필수는 아니다.
  ```bash
  python scripts/build_cde_paper_docx.py \
    --source-md docs/cde_2026_paper_final_ko.md \
    --out-docx docs/cde_2026_paper_final_ko.docx
  ```
- 완료 후 `docs/cde_2026_submission_readiness.md`의
  "Raw-run reproducibility" 행을 `Partial` → `Pass`로 갱신한다.

## 주의

- `scripts/build_cde_paper_evidence_audits.py`는 manifest를 **통째로 다시 쓴다.**
  따라서 manifest 본문에는 수동 메모를 남기지 말 것(지워짐). 메모는 이 파일에 둔다.
- raw JSON을 다시 "재실행"으로 만들 수도 있으나(원본 코퍼스/가격 데이터 필요),
  맥에 원본 파일이 이미 있으므로 위 복사/커밋 방식이 가장 빠르고 정확하다.
