# 실제 TP 체결 및 잔여 SL 정리 단일 관찰

이 도구는 DEMO 주문 한 건을 제출합니다. 실제 TP 트리거 체결은 시세에 달려 있으며, 최대5분 관찰해도 발생하지 않을 수 있습니다. 시간을 늘리거나 수량·레버리지·진입 가격 정책·TP 거리를 바꿔 체결을 강제하지 않습니다. 결과가 미수락이면 반복 주문하지 않습니다.

기존 보호 거리(SL0.5%, TP1%), PASSIVE 지정가, ISOLATED BTCUSDT/2배, 현재 거래소 최소 수량을 사용합니다. 수량 cap은 추가로 최대0.0007 BTC, 명목금액100 USDT 이하로 제한합니다. 현재 최소 수량이 cap을 초과하면 주문을 만들지 않습니다. 실제 제출 직전에 신선도·기간·손실·담보·리스/펜스를 원래 경로로 확인합니다. 시장가 신규 진입은 없습니다.

## 1. 설치 및 상태 확인

새 windows-demo-tp-trigger-check 폴더에 ZIP을 풀고 탐색기 주소창에서 cmd를 실행합니다. 기존 hybrid-windows 프로젝트 이름과 DB/export/RAM 볼륨을 유지합니다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait
```

키 세션이 유효하면 API 재빌드 뒤에도 다시 입력하지 않습니다. 등록 상태를 확보하는 아래 명령도 유효 세션에서는 질문 없이 끝납니다. 만료·잘못된 캐시 결과라면 자동 삭제하거나 우회하지 않고 결과를 공유합니다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -m scripts.demo_key_session --ensure
```

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -T api python -m scripts.check_demo_session_ready
```

READ_ONLY_READY_ENTRIES_STILL_PAUSED가 확인될 때만 아래 단일 관찰로 진행합니다. BLOCKED이면 다음 명령을 실행하지 않습니다. 초기화·기준금 재설정·볼륨 삭제·추가 export 요청은 없습니다.

## 2. 실제 DEMO 단일 관찰

아래 작업 ID는 이 관찰 전용입니다. 다른 거래나 미완료 작업에 재사용하지 않습니다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -T api python -m scripts.check_demo_tp_trigger --enable-demo --enable-tp-trigger-observation --operation-id 15d30a13-4f65-407d-ae41-f58dd61d429f
```

최초 준비/WSS 연결 이후 최대300초 관찰하며 준비/정리 시간은 추가됩니다. 관찰 중 창을 닫거나 Ctrl+C를 누르거나 API/Docker를 재시작하지 않습니다. 다른 봇·수동 거래를 동시에 실행하지 않습니다. 기본 API/브라우저의 주문 기능은 계속 비활성입니다. CLI 안에서만 명시적 DEMO 주문 한 건을 허용합니다.

TP가 체결되면 원래 coordinator가 flat 증거를 확인하고 남은 SL을 정리합니다. TP가 체결되지 않으면 원래 종료 절차로 진입을 정지하고 미체결 잔량을 취소하며, 남은 포지션이 있으면 한 번의 운영자 비상 종료 의도와 기존 flat 확인을 수행합니다. 이 경우 TP 성공으로 판정하지 않습니다. 기존 WSS/리스크/SL 장애 대응은 원래대로 유지됩니다. 관찰 시간 안에 종료됐다는 것과 모든 종료가 항상 성공한다는 것은 다릅니다. 오류/UNKNOWN은 미수락이며 추가 주문 없이 전체 결과를 공유해야 합니다.

## 3. 결과 판정

TP_FILLED_THEN_REMAINING_SL_CANCELED_FLAT_VERIFIED만 이 범위의 통과입니다. TP 자체의 FINISHED와 연결 actualOrderId의 FILLED/SELL/MARKET/reduceOnly/수량·가격, 당시 SL NEW, 서명 포지션0, 원래 SL DELETE 이후 CANCELED 조회, 자동 비상 종료를 포함한 reduce-only POST0(operator_exit_post_attempts), 원래 최종 VERIFIED_FLAT, 과거 이력·기준금 보존 및 NORMAL/paused가 모두 필요합니다. 성공은 SL/TP 접수 확인과 구별합니다.

NOT_ACCEPTED는 TP 미발생·미체결·SL 종료·증거 부족·세션/종료 오류 등을 포함합니다. 중간 FINISHED_FLAT 또는 보호 플래그 true만으로 TP 통과를 선언하지 않습니다. 모든 JSON과 오류를 공유하고 명령을 반복하지 않습니다. 실패 종료에서 EMERGENCY가 남을 수 있으며, 그때는 NORMAL 전용 준비 확인을 곧바로 반복하지 않고 별도 읽기 전용 복구 검토로 진행합니다.

이 도구는 기존 작업 ID가 장부 또는 시작 표식에 있으면 재제출을 거부합니다. 실패한 시험을 새 ID로 자동 재시도하지 않습니다. 부분체결 검증, 정확히30% 체결, Phase4 전체 완료/LIVE/Phase5 전환을 대신하지 않습니다.
