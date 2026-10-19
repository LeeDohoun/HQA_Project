**아침 실행 공백과 abort 등록 경로는 해결됐지만, FW002 연동·메모리 관련 P1 두 건이 남아 활성화는 보류입니다.** 정적 검토만 했으며 테스트는 재실행하지 않았습니다.

- **기존 아침 P1: 핵심 해결.** 야간 19거래일 준비와 아침 마지막 거래일 처리가 분리됐고, 실행 상한 180초와 여유 60초가 다음 우선 작업 전에 들어가야 합니다. 08:12 FW001 종료 직후에는 08:15 복구 타이머 때문에 기다릴 수 있지만, 이후 충분한 빈 구간에서는 08:59 전 봉인이 가능합니다. 지연되면 무거래로 남습니다. [inputs.py:182](/home/are01/src/HQA_Project/HQA_bb003/backtesting/experiments/bb003/inputs.py:182), [admission.py:221](/home/are01/src/HQA_Project/HQA_bb003/backtesting/experiments/bb003/admission.py:221), [deployment.py:239](/home/are01/src/HQA_Project/HQA_bb003/backtesting/experiments/bb003/deployment.py:239)

- **P1-1 — FW002 fill이 admission에서 빠졌습니다.** 타이머 목록과 완료 검사는 **decision만** 다룹니다. 이전 월의 미완료 fill은 월초가 아닌 날에도 실행될 수 있습니다. BB003 시작 당시 inactive여도 이후 FW002 dispatcher가 fill을 시작하므로, 봉인 직전 재검사만으로 실행 중 겹침을 막지는 못합니다. [admission.py:14](/home/are01/src/HQA_Project/HQA_bb003/backtesting/experiments/bb003/admission.py:14), [admission.py:215](/home/are01/src/HQA_Project/HQA_bb003/backtesting/experiments/bb003/admission.py:215), [FW002 runtime.py:191](/home/are01/src/HQA_Project/HQA_fw002/deploy/collector-fw002/runtime.py:191)  
  **수정:** decision과 **모든 미완료 월의 fill** 각각에 완료/현재 실행 불필요 판정을 추가하십시오. Fill은 세 비용배수 기록과 decision 연결을 검증하고, monotonic-only fill timer와 path trigger도 포함해야 합니다. 야간 준비에도 적용해야 합니다.

- **P1-2 — 961 MiB 호스트의 동시 실행 예산이 성립하지 않습니다.** BB003 600 MiB + FW002 worker 600 MiB만으로 1,200 MiB입니다. Root dispatcher도 별도 600 MiB 한도입니다. 현재 활성화 검사는 BB003 단독 RSS만 검사하므로 작은 서버도 통과합니다. [BB003 service:13](/home/are01/src/HQA_Project/HQA_bb003/deploy/collector-bb003/systemd/hqa-bb003-daily.service:13), [FW002 fill service:18](/home/are01/src/HQA_Project/HQA_fw002/deploy/collector-fw002/systemd/hqa-fw002-fill.service:18), [deployment.py:128](/home/are01/src/HQA_Project/HQA_bb003/backtesting/experiments/bb003/deployment.py:128)  
  **수정:** 실효 RAM/cgroup 한도와 dispatcher·worker·기존 서비스의 동시 실행 예산을 활성화 조건으로 봉인하십시오. 현재 구성의 NCP 활성화는 거부하고, 충분한 연구실 호스트 또는 검증된 직렬화/메모리 축소가 필요합니다. 공유 flock만으로 FW002는 보호되지 않습니다.

- **Abort P2: 기능 해결.** checkpoint 없는 중단은 재계산 없이 한 번 등록하고, checkpoint가 있으면 복구를 요구합니다. 다만 `uses_holdout:false`인 BB003에 `holdout_aborted`를 쓰는 명칭은 **`forward_aborted`로 정정**하는 편이 맞습니다. [publish.py:25](/home/are01/src/HQA_Project/HQA_bb003/backtesting/experiments/bb003/publish.py:25)

활성화 전 남은 항목은 다음입니다.

1. 위 두 P1 수정·검증 후 최종 커밋과 배포 inventory 확정.
2. 최종 호스트/private 환경에서 전체 아침 경로의 시간·메모리 측정. 네 KRX 트리거, FW001/shadow, FW002 decision·fill·dispatcher 경쟁과 강제 종료·재시작을 포함.
3. 별도 루트·권한·공유 잠금 inode·EnvironmentFile, 반복판정용 목록과 가격 20거래일, 실제 DART 취득·문서 검증 확인.
4. 공식 근거가 있는 2026 정책 및 코드/환경 manifest 봉인, 디렉터리 mtime을 보존하는 백업·PC 복구 확인.
5. **2026-11-30 23:59:59 KST까지** 유효 activation 표식과 타이머 가동 확인. 미완료면 `not_started`. 2027 이후 달력·세율은 활성화 시 전체가 필요하지 않지만, 해당 날짜가 필요해지기 전에 갱신해야 합니다.

