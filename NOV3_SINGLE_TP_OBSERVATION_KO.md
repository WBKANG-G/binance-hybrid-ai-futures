# 11월3일 준비 확인 및 실제 DEMO TP 단일 관찰

## 실제 준비 확인

사용자 CMD는 READ_ONLY_READY_ENTRIES_STILL_PAUSED, period_keys_match true, stored/current day2026-11-03 및 stored/current week2026-11-02를 보고했다. NORMAL/paused, credential_source PRIVATE_TMPFS_SESSION, exchange_empty_verified true, database_changed/orders_enabled/live_trading_enabled false였다. POOLED_HTTPS22회/연결1회/재사용21회/1407ms, signed GET22회/clock0회였다. 이 준비 결과를 TP 체결 성공으로 확대하지 않는다.

## 실행할 범위

기존 check_demo_tp_trigger의 실제 DEMO 주문 한 건이다. 기존 PASSIVE 지정가 LONG/BTCUSDT ISOLATED2배, 최대0.0007 BTC/명목100USDT, SL0.5%/TP1%를 유지한다. 원래 최소 수량/진입 신선도/기간/손실/담보·리스/펜스 검사도 그대로다. 최소 수량이나 명목 한도를 충족하지 못하면 주문을 만들지 않는다. 시장가 신규 진입·추격/재가격 조정·보호 거리 단축은 하지 않는다.

새 전용 작업 ID는 6703f8c0-0c8a-4a2b-84d5-37e39693dbba다. 과거15d30a13 미체결 결과나 일간 기준금 작업을 재사용하지 않는다. 실제 주문은 사용자 실행 전으로 아직 생성하지 않았다.

```bat
docker exec hybrid-windows-api-1 python -m scripts.check_demo_tp_trigger --enable-demo --enable-tp-trigger-observation --operation-id 6703f8c0-0c8a-4a2b-84d5-37e39693dbba
```

준비/연결 이후 최대300초 관찰이며 원래 만료/정리로 일찍 끝날 수 있다. 준비와 후속 정리 시간은 별도다. 실제 TP는 시세에 달려 발생을 보장하지 않는다. 관찰 중 창 닫기/Ctrl+C/API·Docker 재시작이나 다른 봇/수동 거래를 동시에 하지 않는다.

TP가 없으면 원래 진입 정지·미체결 취소·잔여 포지션 종료와 flat 확인 절차를 사용하며 성공/UNKNOWN 여부는 실제 출력으로 확인한다. 모든 종료가 항상 성공한다고 약속하지 않는다. NOT_ACCEPTED/BLOCKED/UNKNOWN이면 같은 명령 반복이나 새 UUID 주문 없이 결과를 공유한다. 미체결/TP 미발생을 없애려고 수량·정책·관찰 시간을 늘리지 않는다.

## 실제 수락 조건

TP_FILLED_THEN_REMAINING_SL_CANCELED_FLAT_VERIFIED만 TP 범위 통과다. TP FINISHED와 actualOrderId FILLED/SELL/MARKET/reduceOnly/체결 수량, TP 후 원격 포지션0, 당시 잔존 SL NEW와 원래 DELETE 후 CANCELED, operator exit POST0, 최종 VERIFIED_FLAT 및 기존 이력/기준금·NORMAL/paused 유지가 모두 필요하다. SLTP 접수/전량 진입 체결이나 운영자 종료만으로 통과시키지 않는다.

기존 코드/시험/위험값/종료·수락 조건은 변경하지 않았다. 이 준비·기록 작업에서 새 테스트를 실행하거나 실제 거래소 요청을 클라우드에서 보내지 않았다. Windows 실제 관찰 결과는 아직 사용자 실행 전이다. 부분체결/원문 WSS 재생/보호 중 실제 장애 및 Phase4 전체 완료/main/LIVE/Phase5와 구분한다.
