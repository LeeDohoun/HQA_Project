# NCP 경량 수집 서버 운영

이 서버는 DART 첫 관측 폴러와 과거 전시장 backfill, KRX 전종목 일봉 수집,
선택적인 KIS 투자자별 수급 조회, 로컬 상태 보고만 실행합니다.
백엔드·DB·LLM 분석·주문은 실행하지 않습니다. 필수 API 키는
`DART_API_KEY`, `KRX_OPEN_API_KEY`이며, 수급 수집에만 전용 모의투자 데이터 키
`KIS_DATA_APP_KEY`, `KIS_DATA_APP_SECRET`을 추가할 수 있습니다. PC의 `.env`, `.env-ai`,
계좌 파일, 자동매매용 KIS·OpenAI 키를 서버에 복사하지 마세요. 아래 외부 연결·설치·실행 명령은 운영자가
서버를 가동하기로 결정한 뒤 실행합니다.

## 1. 서버와 SSH 준비

1. NCP 콘솔에서 제공되는 유형 중 **1 vCPU, 메모리 1–2 GB**의 가장 작은 서버를
   선택합니다. Ubuntu **22.04 또는 24.04**를 선택하고 SSH 로그인용 공개 키를
   등록합니다. 개인 키는 PC에만 보관하고 비밀번호 로그인은 끕니다.
2. 공인 IP를 할당합니다. DART/KRX에 접속할 수 있도록 외부 인터넷으로 나가는
   DNS 및 HTTPS 연결을 허용합니다. 수집용 HTTP 서버를 열 필요는 없습니다.
3. ACG 인바운드는 **내 공인 IP/32 → TCP 22**만 허용합니다. `0.0.0.0/0`으로
   SSH를 열지 않습니다. PC의 공인 IP가 바뀌면 이 규칙을 갱신합니다.
4. NCP에서 안내하는 SSH 로그인 사용자와 공인 IP로 접속합니다. 아래 예시의
   `ubuntu@PUBLIC_IP`, `~/.ssh/ncp_hqa`는 실제 사용자·IP·키 경로로 바꿉니다.

```bash
chmod 600 ~/.ssh/ncp_hqa
ssh -i ~/.ssh/ncp_hqa ubuntu@PUBLIC_IP
```

최초 서버 세션에서 전송을 위한 rsync와 코드 디렉터리를 준비합니다.

```bash
sudo apt-get update
sudo apt-get install -y rsync
sudo install -d -m 0755 /opt/hqa
sudo -n true
```

전송 스크립트는 원격 `sudo -n rsync`를 사용합니다. SSH 사용자는 비밀번호 입력 없이
이를 실행할 수 있어야 합니다. 처음 연결할 때 SSH 호스트 키를 확인하고 등록하세요.
전용 서비스 사용자 `hqa`에는 SSH 로그인 권한을 주지 않습니다.

## 2. WSL에서 코드 전송

WSL 프로젝트 루트에서 실행합니다. 두 전송 스크립트는 기본값이 rsync dry run입니다.
dry run도 SSH에 접속하므로 서버와 SSH 설정이 준비되어 있어야 합니다.

```bash
bash scripts/ops/push_collector_code.sh ubuntu@PUBLIC_IP ~/.ssh/ncp_hqa
bash scripts/ops/push_collector_code.sh --execute ubuntu@PUBLIC_IP ~/.ssh/ncp_hqa
```

`src/`, `scripts/data/`, `scripts/ops/`, `scripts/__init__.py`, `deploy/collector/`,
루트 requirements 파일만 `/opt/hqa`에 전송합니다. 로컬 수집 데이터, venv,
`.env*`, 협업 기록, 프론트엔드·백엔드, 연구·테스트, Python 캐시는 제외합니다.
코드를 갱신할 때도 같은 명령을 사용합니다. 코드 갱신 전에는 아래 중지 명령으로
수집을 멈추고, 전송·재설치 후 다시 가동하세요.

## 3. 설치와 키 설정

서버에서 먼저 키 없이 설치합니다. 전용 사용자·디렉터리·venv·systemd 파일은
준비하지만 폴러나 타이머는 시작하지 않습니다. 설정이 없거나 키가 빈 값이면 기존
수집 서비스와 타이머도 중지·비활성화하고 다음 단계 안내 후 정상 종료합니다.

```bash
sudo bash /opt/hqa/deploy/collector/install.sh
sudo cp -n /etc/hqa/collector.env.example /etc/hqa/collector.env
sudo chown root:hqa /etc/hqa/collector.env
sudo chmod 600 /etc/hqa/collector.env
sudoedit /etc/hqa/collector.env
```

편집기에서 DART/KRX 키만 채웁니다. 키를 셸 명령줄이나 채팅에 붙여 넣지 마세요.
예시처럼 한 줄에 `KEY=value` 하나를 쓰고 `export`나 셸 명령은 넣지 않습니다.

```dotenv
HQA_DATA_DIR=/var/lib/hqa/data
DART_API_KEY=
KRX_OPEN_API_KEY=
KIS_DATA_APP_KEY=
KIS_DATA_APP_SECRET=
TZ=Asia/Seoul
```

`/etc/hqa`는 `root:hqa`, 0750이고 실제 환경 파일은 `root:hqa`, **0600**입니다.
systemd 관리자가 환경 파일을 읽고 `hqa` 프로세스에 전달하므로 `hqa`가 이 파일을
직접 읽을 필요는 없습니다. 설치기는 실제 키 파일을 생성하거나 덮어쓰지 않고
`collector.env.example`만 복사합니다. 위 여섯 항목 외의 설정은 거부하며, 쓰기 허용
경로와 일치하도록 `HQA_DATA_DIR=/var/lib/hqa/data`를 유지해야 합니다.
선택적인 `KIS_DATA_APP_KEY`, `KIS_DATA_APP_SECRET`은 둘 다 채우거나 둘 다 비워야
합니다. 하나만 설정하면 설치기가 거부합니다. DART/KRX 필수 키가 준비된 상태에서
전용 KIS 키 두 값도 있으면 수급 타이머를 활성화하며, 없으면 해당 타이머와 서비스를
중지·비활성화합니다. 기존 DART/KRX 일정은 그대로 유지됩니다.

```bash
sudo bash /opt/hqa/deploy/collector/install.sh
sudo systemctl status hqa-dart-poller.service --no-pager
sudo systemctl list-timers 'hqa-*' --all --no-pager
```

재실행해도 기존 데이터를 지우지 않습니다. 설치기는 Ubuntu 패키지와 수집용 Python
의존성을 설치하며 별도 브라우저, LLM SDK, DB는 설치하지 않습니다. 서비스는
`NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`를 사용하고
`/var/lib/hqa/data`에만 영구 데이터를 씁니다.

## 4. 수집 검증과 일정

한 번의 DART 실제 요청을 `hqa` 사용자로 검증합니다. root 전용 환경 파일은
systemd가 읽습니다. 이 명령은 API를 호출합니다.

```bash
sudo systemd-run --unit=hqa-dart-check --wait --pipe --collect \
  -p User=hqa -p Group=hqa -p WorkingDirectory=/opt/hqa \
  -p EnvironmentFile=/etc/hqa/collector.env \
  /opt/hqa/venv/bin/python /opt/hqa/scripts/data/dart_poller.py --once --execute
```

- DART: `--loop --execute`로 상주하며 기존 코드의 평일 **07:00–19:30 KST** 창과
  오류 backoff를 따릅니다. 종료 시 systemd가 30초 후 재시작합니다.
- KRX: 평일 **08:40 KST**. 마지막 저장일을 포함해 **한국 기준 어제까지** 요청
  범위를 만들고 저장된 파일과 토·일요일을 건너뜁니다. 저장 파일이 전혀 없으면
  14일 전부터 시작합니다. 오늘 일봉은 요청하지 않습니다. 휴일 달력을 임의로
  적용하지 않으며 평일 빈 응답은 기존 `_state.json`에 남깁니다.
- 상태 보고: 매일 **20:00 KST**. 외부 접속 없이 로컬 파일과 여유 공간만 읽습니다.
- 타이머는 `Persistent=true`라 서버가 꺼져 있는 동안 놓친 일정을 부팅 후 실행합니다.
  실패한 KRX 작업은 자동 재시도하지 않으며 다음 평일 작업이 저장일 이후를 따라잡습니다.

KRX 범위를 요청 없이 확인하거나, 필요하면 실제 작업을 수동 시작합니다.

```bash
sudo -u hqa /opt/hqa/venv/bin/python /opt/hqa/scripts/ops/krx_catchup.py \
  --data-dir /var/lib/hqa/data
sudo systemctl start hqa-krx-daily.service
sudo journalctl -u hqa-dart-poller.service -n 50 --no-pager
sudo journalctl -u hqa-krx-daily.service -n 50 --no-pager
sudo journalctl -u hqa-collector-status.service -n 50 --no-pager
sudo systemctl --failed --no-pager
```

API 승인이 없거나 응답 검증에 실패하면 KRX 작업은 실패 상태로 끝납니다. `empty`
응답은 휴장일·미공개 가능성을 그대로 기록하며 오류를 정상 일봉으로 대체하지 않습니다.

### DART 일일 전시장 backfill (M-ops 확장)

`hqa-dart-backfill.timer`는 매일 **00:20 KST**에 실행하며 최대 300초의 무작위
지연을 둡니다. `Persistent=true`로 놓친 일정은 부팅 후 실행합니다. 먼저
**2023-01-01부터 한국 기준 어제까지** 목록을 채우고, 완료된 날짜는 건너뜁니다.
오늘이나 미래 날짜는 요청하지 않습니다. 이후 `contract,convertible_bond,bond_subtype,
capital_raise` 순서로 본문·구조화 행을 수집하고, 남은 예산으로 전체 카테고리를
처리합니다. `--priority-categories`로 우선순위를 지정할 수 있습니다.

세 단계와 같은 날의 재실행은 동일한 `/var/lib/hqa/data/disclosures/dart_full/_quota.json`을
사용합니다. 기본 `--max-requests`는 **KST 하루 누적 17,000회**이며 재시도도
포함합니다. DART 키의 일일 20,000회 한도에서 3,000회를 남겨 평일 07:00–19:30의
첫 관측 폴러(약 1,000회/일)와 추가 여유를 확보합니다. 이 로컬 원장은 backfill
요청 수이며 폴러 요청 수를 합산하지 않습니다. 제공자 한도 응답도 해당 날짜에
기록해 추가 backfill 요청을 중단합니다. 쿼터 도달은 정상 종료(0)이며 다른 제공자
오류는 실패 종료합니다. 한 번 실행할 때 단계별 상태·요청 수와 backfill이 보고한
대기 건수를 JSON 한 개로 출력합니다.

서비스는 `hqa` 사용자로 실행하고 기존 수집 서비스와 같은 환경 파일·보호 설정을
사용합니다. `Nice=10`, 최대 실행 시간 6시간이며 순차 처리합니다. 기본 명령은
dry run이고 API 키를 읽거나 요청하지 않습니다. 아래 첫 명령으로 계획을 확인합니다.
두 번째 명령은 환경 파일을 systemd가 읽어 **실제 API 요청을 한 번 실행**합니다.
수동 실행 전 예약 작업이 실행 중인지 확인하세요. 동일 원장의 잠금은 데이터와
요청 예산을 보호하지만 중복 실행을 예약할 필요는 없습니다.

```bash
sudo -u hqa /opt/hqa/venv/bin/python /opt/hqa/scripts/ops/dart_backfill_daily.py \
  --data-dir /var/lib/hqa/data
sudo systemd-run --unit=hqa-dart-backfill-manual --wait --pipe --collect \
  -p User=hqa -p Group=hqa -p WorkingDirectory=/opt/hqa \
  -p EnvironmentFile=/etc/hqa/collector.env \
  -p NoNewPrivileges=true -p ProtectSystem=strict -p ProtectHome=true \
  -p ReadWritePaths=/var/lib/hqa/data -p PrivateTmp=true -p UMask=0027 \
  -p TimeoutStartSec=6h -p Nice=10 \
  /opt/hqa/venv/bin/python /opt/hqa/scripts/ops/dart_backfill_daily.py --execute
sudo journalctl -u hqa-dart-backfill.service -n 50 --no-pager
```

예약과 실행 중인 backfill을 중지하려면 아래 명령을 사용합니다. 수동 실행 중인
경우 마지막 명령도 실행합니다. 저장된 체크포인트는 다음 실행에 재사용됩니다.

```bash
sudo systemctl disable --now hqa-dart-backfill.timer
sudo systemctl stop hqa-dart-backfill.service
sudo systemctl stop hqa-dart-backfill-manual.service
```

### KIS 투자자별 일일 수급 (M-FLOW)

KIS Developers에서 **수집 전용 모의투자** 신청/앱을 별도로 준비하고, 모의투자용
App Key와 App Secret을 발급합니다. 발급 화면에서 실전투자가 아닌 모의투자인지
확인하세요. 이 쌍은 시세 조회에만 쓰며, PAPER 자동매매 계정에서 쓰는 앱 키와도
달라야 합니다. 기존 키를 재사용하거나 실전 계정 키를 넣지 않습니다. 별도 모의투자
앱/키 발급 가능 여부와 화면의 메뉴 이름은 계정에서 확인해야 합니다.

서버의 `sudoedit /etc/hqa/collector.env`에서 `KIS_DATA_APP_KEY`,
`KIS_DATA_APP_SECRET` 두 항목을 추가하고 발급받은 쌍을 채웁니다. 셸 명령줄에 값을
쓰지 마세요. 기존 DART/KRX 키와 데이터 경로는 유지합니다. 운영자가 예약 수집을
가동하기로 결정한 뒤 다음 명령으로 재설치합니다.

```bash
sudo bash /opt/hqa/deploy/collector/install.sh
sudo systemctl list-timers hqa-investor-flow.timer --all --no-pager
```

`hqa-investor-flow.timer`는 평일 **19:00 Asia/Seoul**, `Persistent=true`입니다.
서비스는 `hqa` 사용자와 기존 환경 파일·보호 설정을 사용하며 실행 제한은 **2시간**입니다.
최신 로컬 KRX 전시장 저장일의 종목 목록을 사용합니다. KRX 파일이 없으면 `--codes`로
명시적인 목록을 줘야 하며, 이 옵션은 저장된 목록이 있어도 수동 범위로 우선합니다.
기본 요청 속도는 초당 2회이고 종목별 오류는 최대 3회 시도합니다. 한도 응답은
속도를 절반으로 줄이고 실패 종목을 기록한 뒤 다음 종목을 계속 처리합니다.

먼저 요청·키 조회·저장 없이 계획을 확인하고, 실제 smoke test는 3종목으로 제한합니다.
두 번째 명령은 API를 호출합니다. 예약 서비스가 실행 중이면 완료 후 테스트하세요.

```bash
sudo -u hqa /opt/hqa/venv/bin/python /opt/hqa/scripts/ops/investor_flow_daily.py \
  --data-dir /var/lib/hqa/data --max-stocks 3
sudo systemd-run --unit=hqa-investor-flow-smoke --wait --pipe --collect \
  -p User=hqa -p Group=hqa -p WorkingDirectory=/opt/hqa \
  -p EnvironmentFile=/etc/hqa/collector.env \
  -p NoNewPrivileges=true -p ProtectSystem=strict -p ProtectHome=true \
  -p ReadWritePaths=/var/lib/hqa/data -p PrivateTmp=true -p UMask=0027 \
  -p TimeoutStartSec=2h \
  /opt/hqa/venv/bin/python /opt/hqa/scripts/ops/investor_flow_daily.py --execute --max-stocks 3
sudo journalctl -u hqa-investor-flow.service -n 50 --no-pager
```

수동 종목 목록은 `--codes 005930,000660,012450`처럼 지정합니다. JSON 요약의
`stocks_attempted`, `stocks_ok`, `stocks_failed`, `rows_saved`, `failures`를 확인하세요.
종목별 오류를 기록하고 완료한 실행은 **종료 코드 0**이므로 종료 코드만으로 성공을
판정하지 않습니다. 키 누락·잘못된 로컬 설정/파일은 종료 코드 1로 실패합니다.
마지막 실행 요약과 실패 목록은 `market/investor_flow/_last_run.json`에 저장되며
다음 실행이 갱신합니다. dry run은 이 파일도 갱신하지 않습니다.

일자별 데이터는 `market/investor_flow/<YYYY>/<YYYYMMDD>.jsonl`입니다. 종목·거래일별
동일 값은 건너뛰고 변경된 값은 별도 관측 이력으로 추가합니다. 오늘 행은 **18:30 KST
이후**만 저장하며 이전 실행에서는 과거 날짜만 저장합니다. `collected_at`,
`available_at`, 내용 해시 `version`으로 관측 시점을 보존합니다.

FHKST01010900의 다음 응답 계약은 **저장소 코드로 확인되지 않은 가정**입니다.
실제 모의투자 endpoint 지원 여부, `output` 배열과 약 30거래일 반환, 날짜
`stck_bsop_date`(YYYYMMDD), 종가 `stck_clpr`, 개인/외국인/기관 순매수 수량
`prsn_ntby_qty`/`frgn_ntby_qty`/`orgn_ntby_qty`, 순매수 금액
`prsn_ntby_tr_pbmn`/`frgn_ntby_tr_pbmn`/`orgn_ntby_tr_pbmn`을 smoke test에서
확인해야 합니다. 누락된 필드나 잘못된 수치는 실패로 기록하며 0으로 대체하지 않습니다.
수량은 정수, 금액은 부호 있는 십진수 문자열로 검증·저장합니다. 금액의 원/백만원
단위는 미확인이라 환산하지 않고 `net_value_unit=provider_reported_unverified`로
표시합니다. 이 단위를 확인하기 전에는 금액을 원 단위로 해석하지 마세요.

보안 경계는 코드에서도 강제합니다. 대상 호스트는
`https://openapivts.koreainvestment.com:29443`으로 고정하며 실전 도메인을 거부합니다.
조회 허용 목록은 GET `/uapi/domestic-stock/v1/quotations/inquire-investor`
(`tr_id=FHKST01010900`, `FID_COND_MRKT_DIV_CODE=J`, `FID_INPUT_ISCD=<종목코드>`)
하나뿐입니다. 인증용 POST `/oauth2/tokenP`만 별도로 허용하고 주문·계좌 경로와
HTTP 리다이렉트는 거부합니다. 키의 발급 용도/자동매매 키와의 분리는 운영자가 확인하고,
코드는 모의 호스트와 허용 경로를 검증합니다.

토큰은 데이터 디렉터리의 `.kis_tokens/`(0700) 아래 키 해시별 **root 또는 hqa 소유
0600** 캐시에만 보관합니다. 키와 시크릿은 캐시에 저장하지 않고 인증 정보나 원 응답을
로그·실패 목록에 남기지 않습니다. 토큰 캐시는 PC로 가져오는 `market/`·`ops/` 경로
밖에 있습니다. 같은 키는 KST 하루 최대 1회 발급을 요청하고 명시된 만료 시점까지
재사용합니다. 토큰 거절 때만 하루 최대 1회 추가 발급하며, 실패한 발급도 카운터에
포함합니다. 같은 날 단순 만료로 두 번째 일반 발급을 하지 않습니다. 캐시가 손상되면
요청 이력을 초기화하지 않고 실패합니다.
`expires_in`은 Java 참조 코드에서, 토큰 발급 한도 코드 `EGW00133`는 그 코드의
주석에서 확인했습니다. 절대 만료 필드 `access_token_token_expired`의 형식과
거절 코드 `EGW00121`/`EGW00123`, 조회 한도 코드 `EGW00201`는 가정으로
smoke test에서 확인해야 합니다.

## 5. 상태 파일 확인

```bash
sudo systemctl start hqa-collector-status.service
sudo cat /var/lib/hqa/data/ops/status.json
```

JSON과 journal 출력은 동일한 보고서를 담습니다. 환경 파일이나 키 값을 읽지 않습니다.

| 항목 | 의미 |
| --- | --- |
| `dart_last_completed_date` | `_state.json`에 기록된 마지막 성공한 폴링 날짜 |
| `newest_first_seen_at` | 최신 first_seen JSONL 안에서 가장 최근의 첫 관측 시각 |
| `newest_poll_completed_at` | 그 JSONL 안에서 가장 최근의 폴링 완료 시각 |
| `poller_stale` | 평일 10:00 KST 이후 오늘 완료 기록이 없으면 true |
| `dart_backfill` | 완료 목록 날짜·상세 접수·건너뛴 접수 건수, KST 오늘의 backfill 요청 수와 제공자 한도 여부 |
| `investor_flow.latest_stored_date` | 로컬 수급 JSONL의 최신 저장 거래일; 파일이 없으면 null |
| `investor_flow.last_run_failures_count` | 마지막 실행 요약의 실패 종목 수; 실행 기록이 없으면 null |
| `krx_newest_date`, `krx_expected_date` | 최신 저장 날짜와 어제까지의 마지막 평일 |
| `krx_lag_days` | 최신 저장일 이후 기대 날짜까지의 미수집 평일 수. 1보다 크면 경고 |
| `empty_dates` | KRX 상태 파일의 빈 응답 날짜. 휴장으로 확정된 날짜가 아님 |
| `calendar_unverified_dates` | 저장된 KRX 파일 중 `calendar_status`가 verified가 아닌 날짜 |
| `disk_free_bytes`, `low_disk` | 남은 바이트와 1 GB(1,000,000,000바이트) 미만 경고 |
| `flags` | 현재 경고 이름 목록. 일봉 파일이 없으면 `krx_store_empty` 포함 |

공시가 없는 날에도 성공한 폴링은 상태 파일에 날짜를 남기므로 정상으로 판단합니다.
폴링 기록이 있더라도 마지막 10분의 가동 여부까지 보장하는 지표는 아닙니다.
`dart_backfill`은 `dart_full/_state.json`과 `_quota.json`만 읽고 목록·본문 파일은
순회하지 않습니다. 데이터가 아직 없으면 건수는 0, 제공자 한도 여부는 false입니다.
`investor_flow`는 로컬 날짜 파일명과 `_last_run.json`만 읽고 KIS 키·토큰 캐시나 외부
API를 읽지 않습니다. 최신 날짜는 전종목 수집 완료를 보장하지 않으므로 실패 수를 함께
확인합니다.
KRX 지연은 거래소 휴일을 제외하지 않은 **평일 수**입니다. 달력 검증 상태는
`src/runner/trading_calendar.py`의 실제 저장 결과를 사용하며, 2026-11-01 이후 특별장
검증 범위는 기존 코드에서 미확인 상태입니다. 상태 보고는 모든 저장 일봉 파일을
순차적으로 읽어 과거 미검증 날짜도 보존합니다. 파일이 잘못된 JSON이면 작업이 실패합니다.

## 6. PC로 데이터 가져오기

로컬 HQA venv가 있는 WSL 프로젝트에서 실행합니다. PC의 `get_data_dir()`를 사용하므로
로컬 환경 설정의 `HQA_DATA_DIR`와 동일한 위치로 가져옵니다. 이 단계에서 로컬 설정을
읽지만 서버로 전송하지 않습니다. 디렉터리 이름을 유지해 `disclosures/`, `market/`,
`ops/`만 가져오며 로컬 파일을 삭제하지 않습니다. 최초 수집·상태 보고 후 실행하세요.

```bash
bash scripts/ops/pull_collector_data.sh ubuntu@PUBLIC_IP ~/.ssh/ncp_hqa
bash scripts/ops/pull_collector_data.sh --execute ubuntu@PUBLIC_IP ~/.ssh/ncp_hqa
```

수집 데이터는 계속 증가하므로 디스크 공간을 점검하고 별도로 백업합니다. 원격 파일의
최신 사본을 받는 방식이며, PC에서 같은 수집 파일을 독립적으로 수정한 내용을 병합하지는 않습니다.

## 7. 전체 중지와 비용 확인

```bash
sudo systemctl disable --now hqa-dart-poller.service hqa-krx-daily.timer hqa-collector-status.timer hqa-dart-backfill.timer hqa-investor-flow.timer
sudo systemctl stop hqa-krx-daily.service hqa-collector-status.service hqa-dart-backfill.service hqa-investor-flow.service
sudo systemctl list-timers 'hqa-*' --all --no-pager
```

서비스를 중지해도 서버 자원 비용이 계속 발생할 수 있습니다. 사용하지 않을 서버와
공인 IP 등 자원은 NCP 콘솔에서 별도로 확인·정리합니다. 생성할 때 **예산 알림**을
설정하고 **크레딧 만료일과 잔액**, 만료·소진 후 등록 카드로 청구되는 조건을 확인하세요.
가격은 이 문서에서 지정하지 않습니다.

## 의존성 도출 기록

기존 Python 3.12.3 venv에서 두 `scripts.data` 진입 모듈을 import하고
`sys.modules`의 site-packages 경로를 확인한 뒤 `importlib.metadata`로 배포 패키지를
대응시켰습니다. 추가로 KRX가 실행 중 사용하는 `src.runner.trading_calendar`도 import했습니다.
이 확인은 네트워크와 `.env*` 읽기를 차단했으며 수집·모델 작업을 실행하지 않았습니다.

두 진입 모듈을 import했을 때 로드된 외부 최상위 모듈:

```text
81d243bd2c585b0f4821__mypyc, _cyutility, _distutils_hack, bs4, certifi,
charset_normalizer, dateutil, dotenv, idna, lxml, numpy, pandas, requests,
six, socks, soupsieve, typing_extensions, urllib3
```

거래일 달력 import 후 추가된 모듈:

```text
annotated_types, exchange_calendars, korean_lunar_calendar, pydantic,
pydantic_core, pyluach, rank_bm25, toolz, typing_inspection, yaml
```

| 배포 패키지 | 이 venv의 측정 버전 | requirements.txt 조건 |
| --- | --- | --- |
| python-dotenv | 1.2.2 | >=1.0.0 |
| pandas | 3.0.2 | >=2.0.0 |
| numpy | 2.4.4 | >=1.24.0 |
| requests | 2.33.1 | >=2.31.0 |
| beautifulsoup4 | 4.14.3 | >=4.12.0 |
| lxml | 6.0.2 | >=4.9.0 |
| PyYAML | 6.0.3 | >=6.0.1 |
| pydantic | 2.12.5 | >=2.0.0 |
| rank-bm25 | 0.2.2 | >=0.2.2 |
| exchange-calendars | 4.13.2 | ==4.13.2 |

배포 파일은 기존 requirements의 조건을 그대로 사용합니다. 정확히 고정된 항목은
exchange-calendars입니다. 측정한 pandas/numpy 최신 버전을 별도로 고정하면 Ubuntu
22.04의 Python 3.10에서 설치할 수 없으므로 pip가 해당 Python과 호환되는 버전을
선택하도록 원래 범위 조건을 유지했습니다. requests·pandas·BeautifulSoup·pydantic·달력의
하위 의존성은 pip가 설치합니다. `socks`는 기존 환경에 있는 requests의 선택적
프록시 지원이며 수집 경로에 필요하지 않아 별도로 추가하지 않았습니다. 나머지
확장 모듈과 setuptools 초기화 모듈도 독립적인 수집 요구 패키지는 아닙니다.
`src.ingestion`과 `src.runner`의 기존 재노출 구조를 수정하지 않았고, 후자의 import는
도우미 정의를 로드할 뿐 분석·KIS 작업을 호출하지 않습니다.
