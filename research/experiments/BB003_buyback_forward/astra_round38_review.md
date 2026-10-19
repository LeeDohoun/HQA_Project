**아직 implementation-complete로 승인할 수 없습니다.** fill 전체 검사와 `forward_aborted` 수정은 확인했습니다. 메모리 증거 봉인도 추가됐지만, 아래 P1이 남습니다. 정적 검토이며 테스트는 재실행하지 않았습니다.

1. **Conflicts가 우선순위를 보장하지 않습니다.** BB003은 `Type=oneshot`이므로 실행 중 start job이 유지됩니다. 이때 FW002의 `--job-mode=fail` 시작은 BB003 중지와 충돌하여 **FW002 쪽이 거절**됩니다. 반대로 admission 직후 FW002가 `Type=exec`의 active 상태가 되면, BB003 시작이 FW002를 중지할 수 있습니다. `fail`은 **pending job 교체**만 막습니다.  
   **수정:** FW002와 공통 원자적 시작 계약을 마련하거나, FW002 대상 Conflicts를 제거하고 가능한 동시 실행 조합의 RAM·시간을 검증하십시오. SIGTERM 모사만으로는 이 거래 경합이 검증되지 않습니다. [BB003 service:3](/home/are01/src/HQA_Project/HQA_bb003/deploy/collector-bb003/systemd/hqa-bb003-daily.service:3), [BB003 runtime:46](/home/are01/src/HQA_Project/HQA_bb003/deploy/collector-bb003/runtime.py:46), [FW002 runtime:145](/home/are01/src/HQA_Project/HQA_fw002/deploy/collector-fw002/runtime.py:145)

2. **root dispatcher가 남긴 누락 기록을 hqa worker가 읽지 못합니다.** 마감 때 `missed()`가 root 소유 디렉터리와 `mkstemp`의 0600 파일을 만듭니다. 이후 09:01 worker가 이를 읽다가 `PermissionError`로 멈출 수 있습니다.  
   **수정:** 감사 파일을 공개하기 전에 hqa가 읽을 소유권·권한을 보장하고, root→hqa 실제 권한 경계를 검증하십시오. [runtime.py:48](/home/are01/src/HQA_Project/HQA_bb003/deploy/collector-bb003/runtime.py:48), [storage.py:48](/home/are01/src/HQA_Project/HQA_bb003/backtesting/experiments/bb003/storage.py:48), [recorder.py:194](/home/are01/src/HQA_Project/HQA_bb003/backtesting/experiments/bb003/recorder.py:194)

3. **정상 monotonic 타이머 상태를 오류로 처리합니다.** FW002 decision은 `OnBootSec`와 `OnCalendar`를 함께 씁니다. 일회성 OnBoot가 소진되어 다음 monotonic 시각이 없는 상태도 현재는 거절합니다. `_seconds()`도 장기 uptime의 `d/w` 표기를 지원하지 않습니다.  
   **수정:** 소진된 일회성 타이머와 미확정 반복 타이머를 구분하고, 원시 D-Bus 정수 또는 전체 시간 단위를 처리하십시오. [admission.py:40](/home/are01/src/HQA_Project/HQA_bb003/backtesting/experiments/bb003/admission.py:40), [admission.py:102](/home/are01/src/HQA_Project/HQA_bb003/backtesting/experiments/bb003/admission.py:102), [FW002 timer:5](/home/are01/src/HQA_Project/HQA_fw002/deploy/collector-fw002/systemd/hqa-fw002-decision.timer:5)

4. **cgroup 루트의 `memory.max` 존재를 가정합니다.** 정상 cgroup v2 루트에는 이 파일이 없을 수 있으며, 현재 검토 환경에서도 없습니다. 무조건 읽으므로 측정·활성화부터 실패합니다.  
   **수정:** 실제 cgroup 계층을 확인해 루트의 정상적인 제한 없음과 잘못된 구성·읽기 실패를 구분하고, unit/ancestor 한도와 물리 RAM을 계산하십시오. [resources.py:38](/home/are01/src/HQA_Project/HQA_bb003/backtesting/experiments/bb003/resources.py:38)

5. **FW002 기간이 1년 짧습니다.** 실제 종료는 **2029-09**인데 admission은 **2028-09**까지만 허용합니다. `202810.json`부터 오류가 나서 BB003의 V까지 필요한 후속 가격 수집도 중단됩니다.  
   **수정:** 결정·fill 검사 범위를 FW002의 등록된 36개월 전체로 맞추십시오. [admission.py:225](/home/are01/src/HQA_Project/HQA_bb003/backtesting/experiments/bb003/admission.py:225), [admission.py:247](/home/are01/src/HQA_Project/HQA_bb003/backtesting/experiments/bb003/admission.py:247), [FW002 MONTHS:42](/home/are01/src/HQA_Project/HQA_fw002/src/forward/fw002.py:42)

**P2는 해결됐습니다:** 중단 상태·registry 모두 `forward_aborted`, `uses_holdout:false`이며 재계산을 허용하지 않습니다. [publish.py:25](/home/are01/src/HQA_Project/HQA_bb003/backtesting/experiments/bb003/publish.py:25)

위 수정 후에도 최종 호스트의 **실제 systemd 경합·권한·중단 복구·시간/RAM 검증**, 자료·정책·배포 manifest 봉인, **2026-11-30 23:59:59 KST 이전 활성화**가 필요합니다.

