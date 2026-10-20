# Phase 4 최소 수량 DEMO 주문 검증

이 버전에는 실제 Binance DEMO 모의 주문을 전송하는 명령이 있습니다. 실거래는 금지이며 Phase 5는 시작하지 않습니다.
아래 설치 명령은 주문을 보내지 않습니다. 설치 결과 확인 후 주문 검증 명령을 사용합니다.
기존 계좌는 DEMO·BTCUSDT·Isolated·2배, one-way/single-asset, 자동 증거금 추가 꺼짐을 유지합니다.
포지션/미체결 주문 0개, 다른 봇/수동 거래가 없는 전용 DEMO 계좌에서만 시작합니다.

## 설치

Docker Desktop을 켭니다. 새 ZIP을 모두 추출하고 windows-demo-minimum 폴더에서 infra가 보이는 위치로 들어갑니다.
탐색기 주소창에 cmd를 입력하고 Enter를 누릅니다.

```cmd
docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait
```

기존 hybrid-windows 프로젝트와 DB 볼륨을 재사용합니다. 기존 폴더/DB/볼륨을 삭제하지 마세요.
실제 .env/API 키 파일을 복사하지 마세요. API/웹은 계속 PAPER·주문 비활성입니다.

## 설치 결과 확인 후: 최소 수량 DEMO 지정가 1건 검증

다음 명령은 조회 전용이 아닙니다. LONG 방향의 기능 점검용 DEMO 지정가 진입을 한 번 시도합니다.
추세/돌파/AI 매매 신호로 주장하지 않으며 purpose=DEMO_VALIDATION, strategy=null로 따로 기록합니다.
실제 공개 API의 최근 확정 1분봉과 현재 호가/시각/계좌 대조를 증거로 사용합니다.
매매 방향 LONG은 기능 점검 선택이며 투자 판단이 아닙니다.

```cmd
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -c "import getpass,os,runpy,sys; os.environ['LIVE_TRADING_ENABLED']='false'; os.environ['BINANCE_DEMO_API_KEY']=getpass.getpass('DEMO API Key (hidden): '); os.environ['BINANCE_DEMO_API_SECRET']=getpass.getpass('DEMO Secret (hidden): '); sys.argv=['scripts.run_demo','--config','configs/demo-isolated.example.yaml','--account','DEMO-WINDOWS-ISOLATED-BTC-V1','--enable-demo','--validate-minimum','LONG','--operation-id','b8454548-08d4-4cc3-9e77-d411b5659eff']; runpy.run_module('scripts.run_demo',run_name='__main__')"
```

DEMO key/secret을 숨김 입력에 순서대로 붙여넣고 Enter를 누릅니다. 숨김 입력 불가 경고가 뜨면 Ctrl+C로 중단합니다.
키 값/서명/listenKey는 채팅이나 스크린샷으로 보내지 마세요.
기존 격리 원장을 사용하므로 --initialize를 넣지 않습니다.
작업 ID는 DEMO_MINIMUM_OPERATION_ID.txt와 동일합니다. 오류 후 새 ID로 재시도하지 마세요.
같은 ID는 기존 의도를 조회/복구하며 신규 진입을 다시 만들지 않습니다. 실제 재시도는 정제 결과를 검토한 후 진행합니다.

## 한 번의 검증에서 하는 일

1. 쓰기 비활성으로 계좌를 먼저 대조합니다. 다른 포지션/주문, 모드 불일치, 비정상 위험 상태는 거부합니다.
2. 비공개 WSS를 연결하고 REST 대조 후, 현재 호가와 거래 규칙으로 최소 유효 수량을 계산합니다.
3. 그 수량을 영속 상한으로 고정합니다. 위험 예산 계산으로 수량을 키우지 않습니다. 위험 예산이 더 작으면 거부합니다.
4. 제출 지정가×수량이 100 USDT를 넘으면 거부합니다. 이것은 DEMO 점검용 명목금액 한도이며 LIVE 위험 승인값이나 최대 손실 보장이 아닙니다.
5. 손절 거리 0.5%/익절 거리 1%는 이전 미리보기와 같은 기능 점검용 가정입니다. 실행 직전 mark가 bracket 밖이면 거부합니다.
6. 지정가만 신규 진입합니다. 미체결을 시장가 진입으로 바꾸지 않습니다. 의도/상한을 먼저 commit한 뒤 1회 제출합니다.
7. 부분/전체 체결은 실제 체결 수량으로 SL Algo를 먼저 확인하고 TP Algo를 확인합니다. 미지원/보호 실패는 신규 진입 차단 및 위험 축소 경로로 처리합니다.
8. 제출 준비가 끝난 뒤 약 30초 관찰합니다. 지정가 신호 TTL은 10초이며 만료 잔량은 감시 루프에서 취소합니다.
9. 세션 종료 시 남은 진입을 취소하고, 포지션이 있으면 긴급 reduce-only 종료를 시도합니다. ACK만으로 종료 성공이라 하지 않습니다.
10. 실제 position=0을 새 조회로 확인한 뒤 동일한 종료 작업을 재대조하여 잔여 SL/TP를 정리합니다. UNKNOWN exit를 무조건 재전송하지 않습니다.

검증 중 창을 닫거나 컴퓨터를 절전하지 말고 수동 주문하지 마세요. 프로세스 종료 중 즉시 보호 SLA를 보장하지 않습니다.
위험 축소로 EMERGENCY 잠금이 남으면 자동 해제하지 않습니다. 같은 작업 ID의 복구 조회는 가능하지만 다음 신규 주문은 차단됩니다.

## 결과 읽기

- RESERVED_NOT_YET_SUBMITTED: 내부 예약 완료이며 아직 전송 전인 시점의 기록입니다.
- RECOVER_EXISTING_INTENT: 기존 작업을 복구하며 신규 진입을 재제출하지 않습니다.
- FINISHED_FLAT: 마지막 조회에서 포지션/활성 일반 주문/활성 Algo 주문이 없는 상태를 확인했습니다.
- entry_outcome=UNFILLED/PARTIAL_FILL/FULL_FILL을 구분합니다. entry_state=REJECTED면 미체결 정상 취소와 구분해야 합니다.
- filled_protection_and_flat_verified=true: 실제 체결이 있었고 SL/TP 주문 상태 확인 및 종료가 관측됐습니다. SL/TP가 실제로 trigger 체결됐다는 뜻은 아닙니다.
- entry_filled=false면 정상 체결 검증은 미완료입니다. FINISHED_FLAT 하나만으로 Phase 4 통과라고 하지 않습니다.
- UNRESOLVED_DO_NOT_REPEAT 또는 오류: 창/DB를 삭제하거나 새 작업 ID로 반복하지 말고 키가 없는 결과만 공유합니다.
- stop_verified/target_verified는 주문 생성·조회 검증입니다. 실제 SL/TP trigger, 장시간 session 갱신, 장애 복구 시나리오는 별도 검증이 남습니다.

실제 Windows DEMO 주문 실행은 아직 수행하지 않았습니다. 이번 배포만으로 Phase 4 완료나 수익성을 주장하지 않습니다.

# 최소 수량 DEMO 실행 준비 검증 기록

Python 전체: 350 passed / 0 failed / 0 skipped (59.08초).
실제 PostgreSQL 격리 schema와 localhost WebSocket + 합성 Binance 응답/자격증명으로 검사했습니다.
최종 결과에 entry_state/position_state를 추가한 후 검증 실행 관련 10개를 다시 실행해 모두 통과했습니다(6.31초).
Ruff check/format 통과, 전체 mypy strict 62개 source 파일 통과. 최종 보고서 변경 후 해당 모듈 타입 검사도 통과.

추가 확인:
- 수량 상한은 위험 계산으로 커지지 않으며 예약/재시작/POST/UNKNOWN 복구에서 유지됩니다.
- float/비유한수/음수/0/step 불일치/최소 명목금액 미충족 상한을 거부합니다.
- 같은 신호의 상한 변경/삭제, 잘못된 CLI 옵션 혼용을 거부합니다.
- DEMO_VALIDATION purpose는 DEMO에서만 허용하며 strategy=null이어야 합니다. 일반 전략 신호는 여전히 기존 전략을 요구합니다.
- 실제 데이터 형식의 확정 1분봉/현재 호가/시간/계좌 snapshot 증거를 요구합니다. 테스트 데이터는 합성임을 명시합니다.
- 지정가 단일 진입·부분/전체 체결 보호·미체결 잔량 취소·reduce-only 종료·실제 flat 이후 Algo 정리를 검사했습니다.
- 청산 ACK 이후 새 position=0 증거로 동일 작업을 재대조하는 경로를 확인했습니다.
- 같은 작업 ID는 새 진입을 생성하지 않습니다. UNKNOWN exit는 무조건 재전송하지 않습니다.
- 보호 실패 후 flat이어도 체결·보호 수락 성공으로 표시하지 않습니다. 미체결/부분/전체를 구분합니다.

실제 Binance DEMO 주문/SL·TP 생성/트리거/장애 복구는 아직 Windows에서 실행하지 않았습니다.
테스트 통과는 실제 체결 가능성이나 수익성을 뜻하지 않습니다. 이 배포로 Phase 4 완료를 선언하지 않습니다.
실거래/Phase 5 미착수. main/기존 ZIP/의존성 lockfile/MASTER_SPEC/AGENTS/실거래 위험값은 변경하지 않았습니다.
프런트엔드는 변경하지 않았으며 이번에 프런트엔드 테스트를 재실행하지 않았습니다.
