**다섯 P1 모두 해결됐고, 검토 범위에서 새 P1은 발견하지 못했습니다. 구현 완료 상태로 커밋해도 됩니다.** 505개 테스트 통과는 구현자 보고이며, 저는 정적 검토만 했습니다.

1. **Conflicts 경합:** FW002를 제외하고 FW001/shadow의 oneshot 계약을 검증합니다. FW002와의 동시 실행을 인정하고 메모리 예산에 반영하므로, 기존의 역방향 중지 문제는 해소됐습니다. 현재 600+600 MiB 구성은 961 MiB 호스트를 거부합니다. [service:3](/home/are01/src/HQA_Project/HQA_bb003/deploy/collector-bb003/systemd/hqa-bb003-daily.service:3), [resources.py:188](/home/are01/src/HQA_Project/HQA_bb003/backtesting/experiments/bb003/resources.py:188)
2. **root→hqa 권한:** 감사 디렉터리·임시 파일의 소유권과 권한을 공개 전에 설정합니다. [admission.py:369](/home/are01/src/HQA_Project/HQA_bb003/backtesting/experiments/bb003/admission.py:369), [storage.py:48](/home/are01/src/HQA_Project/HQA_bb003/backtesting/experiments/bb003/storage.py:48)
3. **타이머:** 소진된 OnBoot와 미확정 반복 타이머를 구분하고 `d/w`를 처리합니다. [admission.py:43](/home/are01/src/HQA_Project/HQA_bb003/backtesting/experiments/bb003/admission.py:43), [admission.py:106](/home/are01/src/HQA_Project/HQA_bb003/backtesting/experiments/bb003/admission.py:106)
4. **cgroup:** 실제 계층을 탐색하며, 정상 루트의 `memory.max` 부재만 제한 없음으로 처리합니다. 하위 경로 결측·권한 오류는 거부합니다. [resources.py:26](/home/are01/src/HQA_Project/HQA_bb003/backtesting/experiments/bb003/resources.py:26)
5. **FW002 기간:** 결정·fill 검사가 2029-09까지의 동일한 36개월 집합을 사용합니다. [admission.py:21](/home/are01/src/HQA_Project/HQA_bb003/backtesting/experiments/bb003/admission.py:21), [admission.py:258](/home/are01/src/HQA_Project/HQA_bb003/backtesting/experiments/bb003/admission.py:258)

활성화 전에는 다음이 남습니다.

- 최종 호스트·봉인 환경에서 전체 아침 경로 **180초 미만**, RSS 및 cgroup 메모리 검증. **FW002 decision·fill이 함께 존재하는 경우의 추가 메모리도** resident 측정에 포함해야 합니다. 이 요구는 문서에 명시돼 있습니다. [배포 README:221](/home/are01/src/HQA_Project/HQA_bb003/deploy/collector-bb003/README.md:221)
- 실제 systemd 시작 경합, root→hqa 접근, 중단·재시작·마감 처리, mtime 보존 복구, 입력 자료 및 공식 달력·세율 확인.
- 최종 코드·환경·측정 증거 봉인 후 **2026-11-30 23:59:59 KST까지 activation 표식과 타이머 가동 확인**. 미충족이면 `not_started`.

