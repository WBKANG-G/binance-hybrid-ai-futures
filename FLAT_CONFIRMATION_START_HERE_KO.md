# DEMO 청산 확인 지연 수정 적용

기존 원장이 있는 Galaxybook PC에서 사용합니다. 새 폴더에 ZIP을 추출하고 해당 폴더 주소창에서 cmd를 실행하세요. 기존 PostgreSQL 및 demo_export_data 볼륨을 그대로 사용합니다. down -v, 초기화, 새 계정 생성, export 생성 재실행을 하지 않습니다.

## 1. 이미지 적용

```cmd
docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait
```

프로젝트명 hybrid-windows를 유지합니다. 이 명령은 일별/주별 기준이나 비상 잠금을 해제하지 않습니다.

## 2. 기존 검증 기록 확인 — API 키 불필요, DB 읽기 전용

```cmd
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -T api python -c "import json; from sqlalchemy import create_engine,select,text; from packages.domain.config import database_url; from packages.persistence.demo import accounts; from services.execution.demo import DemoCoordinator; e=create_engine(database_url().get_secret_value(),connect_args={'connect_timeout':2}); c=e.connect(); c.execute(text('SET TRANSACTION READ ONLY')); d=c.execute(select(accounts.c.state).where(accounts.c.account_id=='DEMO-WINDOWS-ISOLATED-BTC-V1')).scalar_one(); k='9e4674fb-8d83-4c72-af25-0c5cded1e7e6'; o=d['orders'][k]; print(json.dumps({'check':'LOCAL_COMPLETION_READ_ONLY','flat_confirmation_available':hasattr(DemoCoordinator,'confirm_flat'),'risk_state':d['risk_state'],'paused':d['paused'],'position_state':o['position_state'],'open_quantity':o['open_quantity'],'exit_state':o['exit_state'],'validation_evidence':d.get('validation_evidence',{}).get(k,{}),'stored_day_kst':d['daily_key'],'orders_enabled':False,'live_trading_enabled':False})); c.close(); e.dispose()"
```

결과를 공유해 주세요.flat_confirmation_available=true, 기존 주문의 CLOSED/open_quantity=0/exit_state=FILLED 및 stop_verified/target_verified=true를 확인합니다. 로컬 저장 기록 확인이며 새로운 거래소 조회는 아닙니다.

## 変更内容

기존 검증에는 청산 POST 접수 직후 포지션이 아직 남아 보이면 확인을 일찍 끝내 UNKNOWN이 되는 경로가 있었습니다. 수정판은 동일한 청산 의도를 한 번 실행한 뒤 기존 주문 ID를 최대 4회, 간격 1초로 관측합니다. 각 관측에는 기존 HTTP 타임아웃이 적용되므로 전체 실행 시간이 4초라는 뜻은 아닙니다. 관측 중 신규 진입 POST나 청산 POST 재전송은 하지 않습니다.

포지션 0만으로 통과하지 않습니다. 기존 정리 주문의 FILLED, 원래 주문과 수량의 정합성, 로컬 잔여 수량 0과 대조 완료가 필요합니다. 이 조건을 확인한 뒤 알려진 잔여 SL/TP만 취소하고, 다시 조회해 조건 주문도 0인지 확인합니다. ACK/UNKNOWN/주문 불명/잔여 주문/조회 실패/lease 만료는 성공으로 처리하지 않습니다. 세션 종료 후에도 같은 lease가 유효한지 확인하며, 만료된 lease를 되살리지 않습니다.

출력에 completion_stage와 completion_failure를 추가했습니다. 예외 원문·키·서명·URL은 출력하지 않습니다.

## 이번 실제 계정 결과의 취급

기존 사용자 검증은 SL/TP 등록 확인에 성공했고, 별도의 동일 ID 정리 명령으로 VERIFIED_FLAT이 됐습니다. 이를 수정판 자동 종료의 실제 DEMO 성공 실적으로 바꾸지 않습니다. 이 ZIP 적용으로 새 주문은 제출하지 않습니다. 새 DEMO 검증, 일자 경계·기준 확인과 나머지 Phase4 인수 확인은 별도 단계입니다. LIVE와 Phase5는 시작하지 않습니다.
