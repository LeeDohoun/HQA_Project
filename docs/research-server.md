# 수집 서버에서 연구 실험 실행

PC가 대부분 꺼져 있어도 실험을 계속하려면 기존 Naver Cloud 수집 서버의 사양을
실험 기간에만 늘리고, 연구 데이터용 블록 스토리지를 별도로 연결합니다. 운영자가
측정한 RAM 최고 사용량은 HC003 약 6.4 GB, HC002 약 2.6 GB, LH002 screening 약
2 GB입니다. HC003에는 콘솔에서 **RAM 8 GB 이상, vCPU 2개 이상**인 사용 가능한
사양을 검토하세요. 실제 `MemTotal`에서 600 MiB를 뺀 한도와 실행 중 사용량을
확인해야 하며, 이 측정치가 모든 데이터 크기의 상한을 보장하지는 않습니다.

연구 checkout은 `/srv/hqa-research`이며 전용 시스템 사용자 `hqares`가 소유합니다.
로그인 셸은 `/usr/sbin/nologin`이고 sudo 권한을 주지 않습니다. 기존 수집 사용자
`hqa`, `/opt/hqa`, `/var/lib/hqa/data`의 권한·수집 unit·비밀 설정은 변경하지 않습니다.
기존 설치 방식은 [수집 서버 운영](collector-server.md)을 참고하세요.

아래 네트워크·설치·서버 변경·실험 명령은 **운영자가 실제 실행을 결정한 뒤** 수행합니다.
이 마일스톤의 개발 검증은 로컬 fixture와 오프라인 테스트입니다.

## 콘솔과 서버 준비 체크리스트

- [ ] 현재 서버 사양, 서버 식별자, 리전, 공인 IP, 연결된 디스크와 ACG를 기록합니다.
  진행 중인 수집·연구 작업을 확인하고 서버 정지 시 잠시 수집이 중단되는 시간을 정합니다.
- [ ] 콘솔에서 서버를 **정지**하고 정지 완료를 확인합니다.
- [ ] 해당 서버에서 변경 가능한 사양을 확인한 뒤 RAM과 vCPU를 늘립니다.
  메뉴 이름과 변경 제한은 현재 콘솔에서 확인합니다.
- [ ] 새 블록 스토리지를 생성하고 **같은 서버에 연결**합니다. 설치 시점에 남은 공간이
  최소 **20 GB(20,000,000,000 bytes)**여야 합니다. PC 데이터 크기와 결과·캐시 증가량을
  보고 용량을 정합니다.
- [ ] 서버를 **시작**합니다. SSH 접속과 기존 수집 서비스·타이머의 상태를 확인합니다.
  정지 중 누락된 타이머 일정은 기존 `Persistent=true` 정책에 따라 시작 후 실행될 수 있습니다.
- [ ] `lsblk -f`로 새 장치와 기존 루트/수집 디스크를 구분합니다. **빈 새 볼륨에만**
  파일시스템을 만들고 `/mnt/hqa-research` 등에 마운트합니다. 기존 파일시스템이
  있는 디스크에는 포맷 명령을 실행하지 않습니다.

```bash
lsblk -f
# 아래 장치 이름은 lsblk에서 확인한 새 빈 볼륨으로 바꿉니다.
sudo mkfs.ext4 /dev/NEW_RESEARCH_VOLUME
sudo mkdir -p /mnt/hqa-research
sudo mount /dev/NEW_RESEARCH_VOLUME /mnt/hqa-research
findmnt --mountpoint /mnt/hqa-research
df -B1 /mnt/hqa-research
```

- [ ] 재부팅 후에도 연결할 볼륨은 확인한 UUID로 `/etc/fstab`에 등록하고
  `sudo mount -a`로 검증합니다. 예: `UUID=확인한_UUID /mnt/hqa-research ext4 defaults,nofail 0 2`.
  `nofail`을 사용하면 볼륨 누락이 수집 서버 부팅을 막지 않습니다. 연구 설치·실행기는
  별도 mount가 없으면 거부하므로 빈 루트 디스크 경로에서 실험하지 않습니다.
- [ ] 이 마일스톤 파일들이 공개 저장소의 지정 브랜치에 반영되었는지 확인합니다.
  공개 GitHub 브랜치는 `claude/strategy-research-system`입니다. 서버는 push하지 않습니다.
  PC에서 설치 스크립트만 임시 경로로 보내 bootstrap할 수 있습니다.

```bash
# PC / WSL의 프로젝트 루트
scp -i ~/.ssh/ncp_hqa deploy/research/setup_research.sh ubuntu@PUBLIC_IP:/tmp/hqa-setup-research.sh

# 서버: 먼저 읽기 전용 preflight와 실행 계획
sudo bash /tmp/hqa-setup-research.sh /mnt/hqa-research
sudo bash /tmp/hqa-setup-research.sh --execute /mnt/hqa-research
```

설치기는 root로 실행합니다. 기본값은 **dry run**으로, mount·파일시스템·여유 공간과
기존 계정·checkout만 확인하고 GitHub 접속이나 패키지 설치를 하지 않습니다.
`--execute`는 git/Python venv/rsync를 설치하고, 전체 Git 이력을 가진 clone 또는
`merge --ff-only`를 수행합니다. shallow clone은 사전등록 이력 검증에 사용할 수 없습니다.
기존 checkout이 dirty이거나 origin·브랜치가 다르면 갱신하지 않습니다. 실험 결과로
dirty인 경우도 설치·코드 갱신은 거부하므로 먼저 산출물을 보존하고 운영자가 상태를 정리해야 합니다.
깨끗한 상태에서 재실행하면 기존 계정·venv·데이터 연결을 재사용합니다.

`data/`만 sparse checkout에서 제외하고 `/mnt/hqa-research/hqa-research-data`로
심볼릭 링크를 만듭니다. 과거에 Git으로 추적된 legacy data를 clone의 작업 트리에
놓지 않고 연결 자체도 로컬 Git 제외 목록의 `/data`로 제외합니다. 이 링크가 코드
변경으로 취급되지 않으며 Git 이력과 연구 폴더는 유지됩니다.
`runs/`와 계정의 `.codex/`·`.npm/`·`.cache/`는 checkout의 로컬 Git 제외 목록에 넣습니다.
origin의 push URL은 비활성화하고 pre-push hook도 push를 거부합니다.

연구 venv는 [requirements-research.txt](../deploy/research/requirements-research.txt)를 사용합니다.
수집 requirements의 `python-dotenv, pandas, numpy, requests, beautifulsoup4, lxml,
PyYAML, pydantic, rank-bm25, exchange-calendars==4.13.2`를 재사용하고
`jsonschema>=4.18.0`만 추가합니다. 마지막 항목은 LH runner의 출력 스키마 검증용입니다.
전체 애플리케이션 requirements나 LLM SDK를 설치할 필요는 없습니다.

## PC 데이터와 실험 이력 준비

- [ ] 원본 KRX 일봉 기간과 데이터 크기를 확인합니다. 서버 mount가 유지되는지도 확인합니다.
  SSH 호스트 키는 운영자가 먼저 확인하고 등록합니다. 전송 스크립트는 `BatchMode=yes`와
  원격 `sudo -n -u hqares rsync`를 사용하므로 **SSH 운영자**에게 이 명령의 실행 권한이
  있어야 합니다. `hqares` 자신에게 sudo 권한을 주지 않습니다.
- [ ] PC에서 계획을 확인한 후 누락 파일을 전송합니다.

```bash
bash scripts/ops/sync_research_data.sh ubuntu@PUBLIC_IP ~/.ssh/ncp_hqa
bash scripts/ops/sync_research_data.sh --execute ubuntu@PUBLIC_IP ~/.ssh/ncp_hqa
```

PC repo의 `data/`에서 다음 경로만 전송합니다.

| 데이터 경로 | 용도 |
| --- | --- |
| `market/krx_daily` | 전시장 일봉과 이력 |
| `fundamentals`, `reference` | 분기 재무, 회사·업종 참조 |
| `disclosures/dart_full/list` | 저장된 DART 공시 목록 |
| `market_context` | LH probe의 시장 benchmark 등 |
| `research/lh001`, `research/lh002` | frozen input, 호출 캐시, budget, 단계 checkpoint |
| `disclosures/dart_buyback/structured`, `disclosures/dart_buyback/documents` | BB001이 읽는 자사주매입 구조화 자료와 원문 |
| `disclosures/business_text` | LH 텍스트 단계가 읽는 저장된 사업보고서 본문 |

기본값은 rsync `--dry-run`이며 `--execute`로 실제 전송합니다. 항상
`--ignore-existing`으로 서버에 이미 있는 파일을 보존합니다. 기존 파일 내용 갱신이나
삭제는 수행하지 않습니다. LH cache와 budget 파일을 서로 다른 버전으로 혼합하지 말고,
공식 실험 도중에는 입력을 갱신하지 마세요. PC 일봉 디렉터리가 없으면 거부하며,
없는 추가 경로는 `Not present (not transferred)`로 표시합니다.
`.env*`, 잠금 파일 `*.lock`, 키·계정·credential·token·secret 파일과 심볼릭 링크를 제외합니다.
누락된 실험 입력을 내려받거나 더미 데이터로 채우지 않습니다.

- [ ] **실험 시작 전 두 원장의 이력을 확인합니다.** 데이터 sync는
  `research/experiments/registry.csv`와 `holdout_ledger.jsonl`을 전송하지 않습니다.
  서버 clone에 있는 커밋된 이력이 PC의 최신 기록을 전부 포함해야 합니다.
  PC에만 있는 행이 있으면 실험을 중단하고 운영자가 먼저 그 이력을 서버로 이관합니다.
  이관할 때도 기존 서버 줄 전체가 PC 파일의 접두부인지 확인하고 누락된 전체 줄만
  서버 순서 끝에 추가합니다. 서로 다른 행이나 순서가 있으면 자동 병합하지 않습니다.
  PC와 서버에서 같은 이력을 기반으로 실험을 동시에 진행하지 않습니다.
  특히 holdout 원장 누락은 이미 소비한 평가를 재실행하게 만들 수 있습니다.
- [ ] 필요한 사전등록·프롬프트가 지정 브랜치에 커밋되어 있고 실험 데이터 coverage가
  충족되는지 확인합니다. 보호된 holdout은 기존 `backtesting/holdout.py`의 1회 접근
  규칙을 따릅니다. 이 배포는 사전등록·판정 기준·실험 runner를 변경하지 않습니다.

## 선택 사항: LH001/LH002 인증

- [ ] LH001/LH002를 실행할 때만 Codex CLI를 설치합니다. 관리자가 Node.js/npm을
  준비한 후 아래 명령은 운영자가 **hqares로 대화형 실행**합니다.

```bash
sudo -u hqares -H /bin/bash
npm install --global --prefix "$HOME/.local" @openai/codex@0.160.0
export PATH="$HOME/.local/bin:$PATH"
codex --version
codex login --device-auth
exit
```

저장소의 `backtesting/experiments/lh001/__init__.py`가 고정한 버전은
`codex-cli 0.160.0`입니다. 임의 업그레이드는 runner가 거부합니다.
`codex login --device-auth`는 로컬 CLI 도움말로 확인한 명령이며, 실제 로그인·계정
사용 한도는 운영자가 확인합니다. 설치기가 로그인하거나 인증 파일을 복사하지 않습니다.
PC의 인증 파일·OpenAI API 키·KIS 키를 서버 연구 계정으로 보내지 않습니다.
Codex 인증 상태는 해당 계정 홈 아래에만 둡니다. LH의 모델 호출과 사용 한도는
기존 실험 규칙을 따르며 일반 수치 실험에는 Codex가 필요하지 않습니다.

## 실험 실행과 결과 회수

- [ ] 서버에서 명령과 입력을 검토한 뒤 실제 실험을 시작합니다.

```bash
sudo bash /srv/hqa-research/scripts/ops/research_run.sh hc002
sudo bash /srv/hqa-research/scripts/ops/research_run.sh hc003
sudo bash /srv/hqa-research/scripts/ops/research_run.sh lh001 screening --experiment LH002
sudo bash /srv/hqa-research/scripts/ops/research_run.sh status
```

실행기는 `hc002, hc003, hi001, r001, hf001, bb001, lh001, lh002`만 허용합니다.
`research_run.sh <module>`은 **즉시 `--execute`로 실행**합니다. 나머지 인수는 원래
CLI에 그대로 전달합니다. 예를 들어 HC003 holdout 옵션은 별도 `--holdout`이고,
해당 사전등록의 접근 조건을 충족할 때만 사용합니다.

`systemd-run`이 `hqares` 계정으로 각 실행을 소유하므로 SSH 종료 후에도 계속됩니다.
unit은 `hqa-research-<module>-<UTC 시각>-<PID>`로 구분하고 stdout/stderr는
`/srv/hqa-research/runs/<unit>.log`에 기록합니다. 시작 시 출력하는 `sudo tail -f ...`
명령으로 로그를 확인합니다. 실행기 환경은 새로 구성하며 OpenAI 환경변수는 테스트와
같은 `offline-disabled` 및 `http://127.0.0.1:9/v1`, tracing은 false입니다.
수집기의 비밀 환경 파일이나 SSH 사용자의 환경을 전달하지 않습니다.

`MemoryMax = MemTotal - 600 × 1024² bytes`,
`CPUQuota = (전체 vCPU 수 - 1) × 100%`입니다. CPUQuota는 전체 CPU 사용량 제한이며
특정 코어 고정은 아닙니다. vCPU가 1개이거나 RAM이 예약량 이하면 실행을 거부합니다.
동시에 한 연구 실험만 허용하고, 원자적 시작 잠금으로 중복 실행도 막습니다.
이 한도는 기존 수집 unit의 한도를 변경하지 않으며 모든 상황에서 수집 성능을 보장하지는 않습니다.
OOM·실험 오류를 성공으로 바꾸거나 자동 재시도하지 않습니다.

실행 시 코드·사전등록·프롬프트 등 변경이 있으면 거부합니다. `data/`,
`research/experiments/*/results/**`·`diagnostics/**`, registry와 holdout 원장의 변경만
허용합니다. `status`는 진행·종료 unit의 `Result, ExecMainCode, ExecMainStatus`를
표시합니다. 성공 unit도 `exited` 상태로 유지해 종료 코드 0을 확인할 수 있습니다.
signal로 종료되면 `ExecMainCode`도 함께 해석합니다. 임시 unit은 재부팅 후에는
보존되지 않으며 로그와 결과 파일은 남습니다. 재부팅은 진행 중 실험을 중단합니다.

- [ ] 서버 실험이 종료된 뒤 PC에서 결과를 새 staging snapshot으로 가져옵니다.

```bash
bash scripts/ops/pull_research_results.sh ubuntu@PUBLIC_IP ~/.ssh/ncp_hqa
bash scripts/ops/pull_research_results.sh --execute ubuntu@PUBLIC_IP ~/.ssh/ncp_hqa
# .research_pull/ 내용과 A(추가), M(변경), 원장 새 줄 수/CONFLICT를 검토한 뒤:
bash scripts/ops/pull_research_results.sh --apply
```

수신 범위는 `research/experiments/*/results/**`,
`research/experiments/*/diagnostics/**`, `registry.csv`, `holdout_ledger.jsonl`,
`data/research/**`뿐입니다. 기본 dry run은 메타데이터를 포함한 rsync 비교를 출력합니다.
`--execute`도 로컬 원본 파일을 변경하지 않고 `.research_pull/`에 받은 뒤 내용 차이를
요약합니다. 이후 수신에는 새 빈 경로를 `--stage-dir .research_pull/next-run`으로 지정합니다.
기존 staging을 덮거나 서로 다른 수신을 혼합하지 않습니다.

`--apply`는 SSH 접속 없이 이미 받은 snapshot에만 적용합니다. 성공한 수신의
`.complete` 표식이 없는 중단된 snapshot은 거부합니다. 표식을 수동으로 만들지 마세요.
결과·진단·연구 캐시 파일은 명시적 apply 때 복사하며 로컬 파일을 삭제하지 않습니다.
원장은 **두 파일을 모두 검증한 후** 잠금 아래 기존 bytes를 보존하며 append합니다.
로컬의 모든 전체 줄이 서버 파일의 처음부터 순서대로 일치해야 합니다.
없는 전체 줄만 서버 순서대로 추가하고 중복 줄은 건너뜁니다. 로컬에만 있는 행,
재정렬, 줄바꿈 없는 마지막 줄, 서버에서 사라진 이력이 있으면 **어떤 결과 파일도
적용하지 않습니다**. CONFLICT는 원장 덮어쓰기로 해결하지 않습니다.
staging이 Git 추적 파일을 포함하거나 경로·파일에 심볼릭 링크가 있으면 거부합니다.

## 비용 확인과 축소 체크리스트

- [ ] 현재 콘솔에서 변경 전후 서버 사양의 과금 단위·가동/정지 상태별 과금을 확인합니다.
- [ ] 추가 블록 스토리지의 종류·프로비저닝 용량·성능 옵션·연결/분리 후 과금,
  스냅샷·백업 비용을 확인합니다.
- [ ] 공인 IP, 외부 데이터 전송과 결과 회수 트래픽, 선택적 Codex 사용 한도/요금을
  확인합니다. 이 문서에는 고정 가격을 기재하지 않습니다.
- [ ] 결과·registry·holdout 원장·재개용 캐시 회수를 확인하고 진행 중인 연구 unit이
  없는지 `status`로 확인합니다.
- [ ] 서버를 **정지**, 기존 작은 사양으로 변경, 다시 **시작**하고 기존 수집 상태를
  확인합니다. 추가 스토리지는 재개 필요성과 지속 과금을 보고 유지 여부를 정합니다.
  분리 전에는 볼륨을 정상 unmount하고 fstab도 정리합니다. 삭제 전 데이터 보존을 확인합니다.
- [ ] ACG는 기존 SSH 접근 규칙을 유지합니다. 연구를 위해 새 인바운드 포트를 열거나
  `0.0.0.0/0`으로 확대하지 않습니다. 연구 서버에서 push, KIS 키 사용, 수집 설정 변경을
  하지 않습니다.
