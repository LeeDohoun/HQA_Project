**활성화는 보류해야 합니다. P1 네 건입니다.** 정상 완료 표식의 마감 검사, 수익률을 배제한 매칭, E/V/F·HAC 전체 격자·판정 우선순위는 사전등록과 일치합니다. FW001 inventory와 겹치는 변경은 없습니다. 아래는 정적 검토 결과입니다.

1. **P1 — 수동 실행이 코드 봉인을 우회합니다.** [CLI:75](/home/are01/src/HQA_Project/HQA_bb003/scripts/bb003/__main__.py:75)는 활성화 여부만 확인하고 `record/post-entry/capture`를 실행합니다. 배포 manifest·환경·버그수정 연결 검사가 없어, 다른 코드로 기록한 뒤 원복하면 최종 평가의 현재 코드 검사로는 발견하지 못합니다. **모든 운영 진입점을 동일한 검증 함수로 통합하고, 입력 읽기 전과 봉인 직전에 검증하며 기록마다 사용한 manifest 해시를 남기십시오.**

2. **P1 — 공유 잠금은 FW001의 마감을 보호하지 않습니다.** [runtime.py:20](/home/are01/src/HQA_Project/HQA_bb003/deploy/collector-bb003/runtime.py:20)의 최대 600초 점유와 08:48:55 시작 제한은 BB003 시간만 고려합니다. 예컨대 08:40부터 10분 점유하면 뒤따르는 FW001의 보고된 9분25초 실행이 08:59를 넘습니다. 제한 검사도 환경 전체 해싱 **후**입니다. **잠금 획득 전후에 FW001/shadow의 마감 작업 우선권을 확인하고, 완료 전에는 BB003이 필요한 시간대를 점유하지 않도록 하십시오.**

3. **P1 — 중단된 HTTP 호출이 재시도 예산에서 사라집니다.** [fetch.py:150](/home/are01/src/HQA_Project/HQA_bb003/backtesting/experiments/bb003/fetch.py:150)는 호출을 마친 뒤 185행에서 시도를 기록합니다. 요청 중 강제 종료되면 재시작마다 같은 횟수로 다시 요청하여 고정 4회 제한을 넘습니다. **요청 전에 시도 번호·시작시각을 불변 기록으로 예약하고, 중단된 시도도 소모하도록 바꾸십시오.**

4. **P1 — 최종 평가가 결과·종료 기록 없이 영구 잠길 수 있습니다.** [evaluate.py:151](/home/are01/src/HQA_Project/HQA_bb003/backtesting/experiments/bb003/evaluate.py:151)는 코드·입력 무결성 검사보다 먼저 단일 평가를 소비합니다. 이후 누락 blob, 해시 불일치, 기록 손상 또는 저장 중단은 terminal 결과 없이 종료하고 재실행도 막습니다. **수익률 계산 없는 사전검증을 먼저 수행하고, 평가 시작 후 오류에는 명시적 중단 기록을 남기십시오. 이미 계산·봉인한 결과의 저장/registry 등록만 복구하는 경로도 필요합니다.**

P2는 세 가지입니다.

- **달력·세율 변경 검증의 연도별 구멍:** [policy.py:146](/home/are01/src/HQA_Project/HQA_bb003/backtesting/experiments/bb003/policy.py:146)는 2026 안의 후속 변경에는 공식 근거를 요구하지 않습니다. 초기 기준과 모든 후속 변경을 검증하고, 변경 필드별 근거·효력일을 연결하십시오.
- **실문서 호환성과 서버 시간은 미검증:** [fetch.py:46](/home/are01/src/HQA_Project/HQA_bb003/backtesting/experiments/bb003/fetch.py:46)는 HTML도 XML 파서로 처리하며 BB001의 인코딩/본문 정규화를 재사용하지 않습니다. [리허설:79](/home/are01/src/HQA_Project/HQA_bb003/backtesting/experiments/bb003/rehearsal.py:79)는 이미 정규화된 ArchiveClient를 사용합니다. 실제 형식의 저장 ZIP 회귀 검증이 필요합니다. **6.3초/302MiB는 서버 시간의 대용치가 아닙니다:** 운영 환경 해싱, HTTP 지연, 누적 목록 증가, 공유 잠금 경쟁까지 포함해 실제 호스트에서 측정해야 합니다.
- **registry 복구의 큰 필드 처리:** [publish.py:21](/home/are01/src/HQA_Project/HQA_bb003/backtesting/experiments/bb003/publish.py:21)의 기본 `csv.DictReader`는 기존 큰 `metrics_json`에서 실패할 수 있습니다. 저장소의 큰 필드 지원 reader를 재사용하십시오.

활성화 전에는 위 수정, 최종 커밋·private 환경 봉인, 실제 서비스 권한/잠금/중단 복구·디렉터리 mtime 보존 검증이 남습니다. **2027 공식 달력도 가동 전에 필요합니다.** 첫 사건의 21거래일 보유기간부터 2027로 넘어가므로, 현재 [활성화 검사:37](/home/are01/src/HQA_Project/HQA_bb003/deploy/collector-bb003/activate.py:37)는 기본 2026 정책만으로 통과하지 못합니다. 모두 **2026-11-30 23:59:59 KST까지** 완료하지 못하면 등록대로 `not_started`이며 T0를 미룰 수 없습니다.

