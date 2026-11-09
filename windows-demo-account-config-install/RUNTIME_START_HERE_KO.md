# 과거 청산 조회 누적으로 인한 DEMO 준비 지연 수정

사용자 실패 작업 ID는 3f197232-fd91-4201-83d0-e3539ea633cf입니다. 해당 작업은 예약 생성 전 실패했으며 원장에 주문이 생성되지 않았고 NORMAL/paused 상태였습니다. 이번에는 수정판에서 같은 작업 ID로 검증을 이어갑니다. 임의로 다른 작업 ID를 만들어 반복하지 않습니다.

## 1. 기존 Galaxybook PC 새 폴더에 설치

ZIP을 풀고 windows-demo-runtime-fix 폴더를 열어 주소창에 cmd를 입력하세요. CMD 경로가 새 폴더로 끝나는지 확인한 뒤 실행합니다.

```cmd
docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait
```

같은 hybrid-windows 프로젝트/DB/export 볼륨을 유지합니다. 초기화, down -v, 과거 기준 적용/복구 승인/export 재요청을 하지 않습니다. 기존 청산 확인·일별 기준·income 시각 수정도 포함합니다.

## 2. 같은 작업의 최소 수량 DEMO 검증

아래 명령은 실제 DEMO LONG 지정가 진입을 수행합니다. 격리 BTCUSDT 범위, 거래소 최소 수량과 명목금액100 USDT 상한, 기존 위험 규칙을 유지합니다. 신규 진입은 LIMIT이며 관찰 후 정리를 위한 MARKET은 reduce-only입니다. 약30초 관찰 후 체결/보호/잔여 주문·포지션 정리를 확인합니다. 설치 버전을 확인하는 import가 키 입력 전에 수행됩니다.

```cmd
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -c "import getpass,os,runpy,sys; from integrations.binance_usdm.demo_snapshot import operational_snapshot; os.environ['LIVE_TRADING_ENABLED']='false'; os.environ['BINANCE_DEMO_API_KEY']=getpass.getpass('DEMO API Key (hidden): '); os.environ['BINANCE_DEMO_API_SECRET']=getpass.getpass('DEMO Secret (hidden): '); sys.argv=['scripts.run_demo','--config','configs/demo-isolated.example.yaml','--account','DEMO-WINDOWS-ISOLATED-BTC-V1','--enable-demo','--validate-minimum','LONG','--entry-style','OPPOSITE_QUOTE','--operation-id','3f197232-fd91-4201-83d0-e3539ea633cf']; runpy.run_module('scripts.run_demo',run_name='__main__')"
```

마지막 결과를 공유하세요. 목표는 FINISHED_FLAT과 filled_protection_and_flat_verified=true입니다. 체결이 없거나 session_error=true이면 보호까지 성공한 것으로 처리하지 않습니다. 미확정/실패면 추가 실행 없이 결과를 공유합니다. 키와 Secret은 공유하지 않습니다.

## 변경 방식

운영 중에도 매번 현재 전체 포지션·일반/조건부 미체결·계정/마진 설정·income을 조회합니다. 로컬 원장이 이미 완료를 검증한 주문이고 잔여·노출·위험·보호가0이며, 해당 ID가 새 미체결 목록에도 없을 때만 개별 과거 진입/청산/보호 조회를 생략합니다. 채워진 주문은 확인된 FILLED 종료가 필요하며, 미체결 종료는 exit_id 없음/NONE이어야 합니다. UNKNOWN/SUBMITTING/부분 체결/보호 중인 주문과 동일 ID가 원격에 다시 나타나는 경우는 원래 조회를 유지합니다. 과거 주문을 삭제하거나 성공으로 바꾸지 않습니다.

복구·일별 기준·감사 스냅샷의 기본 함수는 계속 모든 원래 ID를 조회합니다. 최종 자동 청산 확인도 전체 이력을 조회하여 알 수 없는 보호 상태가 있으면 통과하지 않습니다.

최신 스냅샷에서 잔여 보호 주문이 실제 보이는 경우에만 CLOSED 주문의 후속 정리를 실행합니다. 이미 정리된 모든 과거 주문에 대한 같은 정리를 매번 반복하지 않습니다.

유효한 fence 확인 뒤 대조 시각을 동기화하고 준비/정리 단계에서도 시각을 갱신합니다. 60초 서명 시각 제한, 시계RTT/오차 한도, lease 만료/fencing, 5초 시세 유효시간, 설정된 계정/스트림 신선도와10초 신호 만료는 늘리지 않았습니다. 여전히 느리거나 상태가 바뀌면 차단합니다. 실패 시 validation_stage/session_failure/completion_stage/completion_failure는 비밀정보 없는 허용 분류만 출력합니다.

이번 ZIP은 일별 기준·주별 기준·고점·복구 기록·미확정 요청이나 잠금을 바꾸지 않습니다. 실제 수정판 자동 검증 결과는 Windows에서 별도로 확인합니다. LIVE/Phase5는 시작하지 않습니다.
