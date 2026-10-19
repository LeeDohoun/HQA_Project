**대부분 해결됐지만, 새 P1 한 건 때문에 활성화는 아직 보류입니다.** 정적 검토이며 테스트는 재실행하지 않았습니다.

- **P1-1 해결:** 공통 진입 검증과 봉인 직전 재검증, 기록별 manifest 해시가 연결됐습니다. [deployment.py:127](/home/are01/src/HQA_Project/HQA_bb003/backtesting/experiments/bb003/deployment.py:127), [storage.py:181](/home/are01/src/HQA_Project/HQA_bb003/backtesting/experiments/bb003/storage.py:181)
- **P1-3 해결:** HTTP 이전의 불변 예약을 기준으로 횟수를 세므로 중단·재시작도 예산을 소비합니다. [fetch.py:155](/home/are01/src/HQA_Project/HQA_bb003/backtesting/experiments/bb003/fetch.py:155)
- **P1-4 핵심 해결:** 무결성 사전검증이 평가 소비보다 앞서며, 계산 checkpoint 이후에는 재계산 없이 저장을 복구합니다. 다만 아래 registry 보완이 남습니다. [evaluate.py:208](/home/are01/src/HQA_Project/HQA_bb003/backtesting/experiments/bb003/evaluate.py:208)
- **P2 코드 수정 확인:** 모든 정책 변경의 공식 근거 검사, BB001 본문 정규화, 큰 CSV 필드 처리가 들어갔습니다. 실제 서버·실문서 검증은 여전히 별개입니다. [policy.py:170](/home/are01/src/HQA_Project/HQA_bb003/backtesting/experiments/bb003/policy.py:170), [fetch.py:43](/home/are01/src/HQA_Project/HQA_bb003/backtesting/experiments/bb003/fetch.py:43), [publish.py:22](/home/are01/src/HQA_Project/HQA_bb003/backtesting/experiments/bb003/publish.py:22)

**새 P1 — FW001 보호 대신 BB003의 유효 결정 시간대를 없앴습니다.** 현재 [타이머:5](/home/are01/src/HQA_Project/HQA_bb003/deploy/collector-bb003/systemd/hqa-bb003-daily.timer:5)는 05:55 다음 실행이 09:01이고, 수동 실행도 [우선권 검사:225](/home/are01/src/HQA_Project/HQA_bb003/backtesting/experiments/bb003/deployment.py:225)가 아침을 차단합니다. 실제 평일 첫 KRX 트리거가 08:02이면 차단 구간은 **07:51:55~08:59**입니다.

BB003은 진입 직전 거래일까지의 가격이 필요한데, 저장된 [공개시각 probe:24](/home/are01/src/HQA_Project/HQA_fw001/research/experiments/FW001_forward_replication/krx_publish_probe_20261019.jsonl:24)는 전일 자료가 07:55에는 없고 08:00에 생겼음을 보여줍니다. 이런 사건은 새 자료를 읽을 첫 실행이 이미 마감 후여서 반드시 무거래가 됩니다. **기존 FW001 지연 문제는 차단됐지만 BB003 운영 가능성이 깨졌습니다.**

수정은 야간 준비와 아침 최종 봉인을 분리하고, **KRX 완료 및 FW001/shadow의 필요한 작업 완료 증거를 확인한 뒤, 검증된 최악 실행시간이 들어가는 마감 전 구간만 허용**하는 방식이어야 합니다. 단순히 08:40 실행을 복원하면 기존 문제가 재발합니다.

**청산일 지연 확정에는 동의합니다.** 진입일·마감·21거래일 규칙은 먼저 봉인하고, 평가 때 공식 달력으로 청산일만 파생합니다. 기존 봉인 파일은 바꾸지 않고 진입 조건 불일치도 거부합니다. 따라서 이제 **2027 달력은 활성화의 선행조건이 아닙니다.** [policy.py:60](/home/are01/src/HQA_Project/HQA_bb003/backtesting/experiments/bb003/policy.py:60), [evaluate.py:151](/home/are01/src/HQA_Project/HQA_bb003/backtesting/experiments/bb003/evaluate.py:151)

**남은 P2:** 계산 checkpoint 없이 중단되면 `evaluation_abort.json`만 남고, `publish_trial`은 정상 evaluation 표식이 없다는 이유로 거부합니다. 이런 terminal abort도 **재계산 없이 registry에 한 번 기록**하는 경로가 필요합니다. [evaluate.py:159](/home/are01/src/HQA_Project/HQA_bb003/backtesting/experiments/bb003/evaluate.py:159), [publish.py:16](/home/are01/src/HQA_Project/HQA_bb003/backtesting/experiments/bb003/publish.py:16)

활성화 전 남은 일은 다음입니다.

1. 위 아침 실행 경로와 중단 registry 경로 수정·검증 후 최종 커밋.
2. 최종 호스트·private 환경에서 실제 운영 검증까지 포함한 시간/RSS, KRX 네 번의 트리거와 잠금 경쟁, 강제 종료·재시작 검증. 이전 6.3초 측정은 재사용 불가.
3. 별도 루트·쓰기 권한·공유 잠금 inode·EnvironmentFile 설정, 목록 반복판정 구간과 가격 20거래일 자료, 실제 DART 취득 경로 확인.
4. 공식 근거를 포함한 2026 정책과 코드/환경 manifest 봉인, 백업·PC 복사 시 디렉터리 mtime 및 복구 검증.
5. **2026-11-30 23:59:59 KST까지** 유효 activation 표식과 타이머 가동 확인. 실패하면 `not_started`; 이후 달력·세율은 해당 날짜가 필요해지기 전에 갱신합니다.

