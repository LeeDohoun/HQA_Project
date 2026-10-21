# 인수인계: 전략 실행 경로 (M8) 개발 브랜치

다른 컴퓨터나 새 Claude Code 세션에서 이 작업을 이어 갈 때 먼저 읽는 문서입니다. 기준 시점은 2026-10-21입니다.

## 한 줄 요약

연구를 통과한 전략(HF001 `f_both`, AV001 `av_all`)을 사전 등록 규칙 그대로, 전략 전용 KIS 모의투자(PAPER) 계좌에서 일괄 리밸런싱하는 경로를 만들었습니다. 현재 두 전략은 보류 구간을 통과하지 못해 `observe`(관찰) 단계이고, 실제 주문 전송은 꺼져 있습니다.

## 브랜치

- 작업 브랜치: `claude/strategy-paper-runtime` (GitHub `LeeDohoun/HQA_Project`)
- 구성 (first-parent 순서)
  1. `8840e9e` 팀 브랜치 `origin/feat/team-integration`(이도훈, LLM PAPER 운영) 병합. 데이터 규칙을 연구 쪽에 맞춤
  2. `f7b622a` M8: Python 실행기, 백엔드 일괄 주문·위험 한도, `/strategies` 화면
  3. `24c39ed` 연구 브랜치 `claude/strategy-research-system`(FW001 기록기 포함) 병합
  4. `a97056d` FW001 완료 증명(`fw001.completion_proof`)으로 결정 읽기
  5. `97f2407` 하루 작업(`daily`), 텔레그램 알림, systemd 파일
  6. 이후 커밋: 연구실 `hqa-server` 배포 구성, 이 문서
- main에는 아직 들어가지 않았습니다. 들어가려면 팀과 합의할 것이 있습니다 (아래 "팀과 맞출 것").

## 문서

- 명세: [실행 계획 M8](strategy-execution-plan.md) — 산출물, 단계·승격 규칙, 완료 조건
- 운영: [전략 실행 경로 운영 안내](strategy-runtime.md) — 내부 API 계약, 사유 코드, FW001 완료 증명, 배포 구성, 하루 일정과 알림, 설치 절차, 미확인 사항
- 로드맵: [strategy-research-roadmap.md](strategy-research-roadmap.md) 6·8장 (위험 한도, 수명주기 단계)

## 핵심 결정과 이유

1. **연구 그대로 매매 (방식 A)**: 두 전략은 매월 수백 종목(약 586·588) 동일 비중입니다. 종목 수를 줄이면 검증되지 않은 새 전략이라, 백엔드에 일괄 리밸런싱 경로를 따로 만들었습니다. 기존 LLM 소수 종목 경로는 그대로 둡니다.
2. **결정은 읽기만**: 실행 경로는 FW001 기록기의 봉인된 결정을 `src.forward.fw001.completion_proof()`로 확인하고 읽기만 합니다 (같은 코드 원칙). 완료 증명이 없거나 08:59 이후면 그달은 거래하지 않고, 손상된 증거는 오류로 알립니다.
3. **단계 자동 판정**: `observe < shadow < paper`. 섀도는 실험 대장의 설계·보류 판정 `pass` 후, PAPER는 섀도 20거래일·28일 이상, 사고 0건, FW001 체결과 독립 대조 일치, 사람 승인(`config/strategy_approvals.yaml`) 후입니다.
4. **백엔드 독립 검사**: 전략 계좌는 LLM 계획·수동 주문을 거부합니다. 같은 세션·단계에는 일괄 주문이 하나만 허용됩니다. 일일 손실 2%, 낙폭 10%·15%, 중단 스위치, 주문 수·취소 비율 상한이 있습니다. 주문 전송은 기본 꺼짐(`hqa.strategy.orders-enabled=false`)입니다.
5. **데이터 규칙 (팀 병합)**: 휴장일 표는 하나로 합쳤습니다. 수능일(2026-11-19)만 공지 전까지 막고, 연구·전진 기록은 공식 공지 전 2027년을 막습니다 (실시간 분석은 팀 기준 2027-09-30까지). 영문 종목코드는 허용합니다.
6. **배포 분리**: 수집기와 FW001은 FW001 봉인본(`release/collector-fw001`, FW001 담당 세션 관리)에서만 실행합니다. M8은 같은 연구실 `hqa-server`의 별도 경로 `/opt/hqa-runtime`에서 돌고, 수집 자료를 `rsync -a`로 복사해 읽습니다.
7. **알림**: 텔레그램 봇. 토큰은 서버 환경 파일에만 둡니다.

## 검증 상태 (2026-10-21)

- Python `tests/test_live_*.py` 138개 통과. Python 전체는 FW001 테스트 45건과 시각 의존 테스트 2건을 빼고 통과합니다.
  - FW001 테스트 실패는 예상된 것입니다. FW001은 실행 트리 전체가 봉인 목록과 같아야 실행되기 때문입니다 (FW001 담당 세션과 합의).
  - 시각 의존 테스트(`test_market_context_ingestion` 2건)는 오전 8시 전에 돌리면 실패합니다.
- 백엔드 `mvn test`: 400개 실패 0. Postgres 테스트 30개는 건너뜀.
- PAPER 종단 시뮬레이션: 167/167.
- 프론트엔드: 빌드 성공, 테스트 42개 통과.
- Python이 만든 일괄 주문 JSON을 백엔드 검증이 그대로 받아들이는지 교차 테스트로 확인했습니다.

## 다음 할 일

사람의 승인이 필요한 것에는 **[승인]** 표시를 했습니다.

1. ~~`hqa-server` 설치~~ **완료 (2026-10-21)**: `/opt/hqa-runtime`(f0946ca, root:hqa 750), venv(수집기와 같은 패키지 버전), `/etc/hqa/strategy-runtime.env`(root:hqa 640, 같은 서버 수집 자료 복사, 백엔드·텔레그램 미설정), 타이머 `hqa-strategy-runtime-morning.timer`(평일 09:10)·`-evening.timer`(평일 16:30) 활성. 첫 실행(systemd) 성공: 10-20 관찰 장부, `data/live/runs/20261021_morning.json`. 코드 갱신은 이 PC에서 `git bundle`을 보내 `/opt/hqa-runtime`에서 `git fetch`/`checkout` 합니다 (서버에 GitHub 인증 없음).
2. **[승인, 보류 중]** 텔레그램 봇 토큰·채팅 ID를 환경 파일에 넣은 뒤 시험 발송 (`strategy_runtime alerts --execute`). 그때까지 알림은 `data/live/alerts/*.jsonl` 파일에만 남습니다.
3. 10-30 FW001 첫 결정(10-30 일봉이 공개되는 11-02 아침 08:00~08:59에 기록·봉인), 11-02 진입일부터 관찰 기록 (11-02 장부는 11-03 아침 09:10에 작성).
4. 11-27까지 2027년 KRX 휴장일과 거래세율을 공식 자료로 반영 (`research/experiments/OPS_TODO_2027_KRX_CALENDAR.md`). 연구 쪽 작업입니다.
5. 보류 구간 평가: AV002(AV001 재평가), HF001. 연구 세션 몫이며 **[승인]**.
6. KIS 모의투자 동시호가 주문 확인 (장전 시장가 접수 여부, 시가 체결, 증거금, 매도 대금 반영, 주문 내역 필드). 다른 세션이 설계 중인 장전 기록기(XO001 가칭) 결과를 근거로 쓸 수 있습니다. **[승인]**
7. 백엔드를 `hqa-server`에 올리고 V15 마이그레이션을 실제 Postgres에 적용. **[승인]**
8. 전략 전용 PAPER 계좌를 연결 (`POST /api/v1/internal/strategies/accounts`). **[승인]**
9. 실계좌(로드맵 7단계)와 다른 사람 대상 서비스: 법률 검토(투자자문·일임업, 로보어드바이저 테스트베드, KIS 제휴 약관)가 먼저입니다.

## 팀과 맞출 것

- 팀 테스트 6곳을 연구 기준으로 고쳤습니다: 테마 오류 처리, 영문 종목코드, 수능일 처리, compose 키 차단 등. main 병합 전에 이도훈 님과 합의가 필요합니다.
- 팀 브랜치를 main에 넣는 일과 수집 서버 배포는 분리합니다. 수집 서버는 FW001 봉인본에 고정입니다.

## 다른 컴퓨터에서 이어 하기

```bash
git clone https://github.com/LeeDohoun/HQA_Project.git
```

```bash
git -C HQA_Project switch claude/strategy-paper-runtime
```

1. `python3 -m venv venv` 후 `venv/bin/pip install -r requirements.txt -r requirements-dev.txt`, `npm --prefix frontend ci`.
2. 자료는 `scripts/ops/pull_collector_data.sh --execute hqaops@100.68.100.97 ~/.ssh/<키>`로 받습니다. `rsync -a`라 FW001 완료 증명의 mtime이 유지됩니다.
3. `.env`와 KIS 키는 새 컴퓨터에서 직접 설정합니다. 저장소에는 없습니다.
4. Claude Code에게는 이 문서와 `AGENTS.md`, `docs/strategy-runtime.md`부터 읽게 합니다. 이전 PC의 메모리(`~/.claude/projects/<프로젝트 경로>/memory/`)를 옮기면 맥락이 더 이어집니다.
5. 테스트 명령은 `AGENTS.md`의 오프라인 명령을 씁니다. 실시간 KIS, DART, KRX 호출이나 서비스 실행은 하지 않습니다.
