# 미체결 진입 주문 중 WebSocket 단절·취소·복구 검증

새 폴더 windows-demo-open-disconnect-check에서 실행한다. Galaxybook 기존 hybrid-windows DB/볼륨과 기준값을 유지한다. down -v, 초기화, 기존 요청 삭제를 하지 않는다.

## 실행 순서

CMD창 하나만 사용한다. B창이나 직접 API 재시작은 필요 없다.

1. 새 ZIP을 풀고 해당 폴더 주소창에 cmd 입력. 다음 빌드가 Healthy까지 끝나기를 기다린다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait
```

2. API 재생성으로 사라진 키를 메모리 세션에 등록한다. DEMO 키와 Secret은 숨김 입력이며 채팅에 보내지 않는다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -m scripts.demo_key_session --register
```

3. 아래 한 번만 실행한다. 신규 DEMO 진입 주문 1건을 제출하는 명령이다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -T api python -m scripts.check_demo_open_disconnect --enable-demo --enable-fault-injection --operation-id 4e81bbe7-12f8-4e65-848a-f3a928b13a6e
```

READY → BLOCKED → RECONNECTED → 최종 결과까지 기다린다. 창을 닫거나 API를 재시작하지 않는다. 오류나 NOT_ACCEPTED가 나오면 재실행하지 않고 최종 화면 전체를 공유한다. 기존 ID 재사용은 복구 전용이며 신규 주문을 만들지 않는다. 새 주문을 위한 다른 ID를 임의로 생성하지 않는다.

## 범위와 판정

기존 API 재시작 실험과 같은 가격 계산을 사용한다. BTCUSDT ISOLATED 2x LONG, 현재 bid보다 0.3% 낮은 지정가, 거래소 최소 허용 수량, 진입명목 100 USDT 이하, 실험 주문 TTL 60초다. 기존 리스크·freshness·수량 cap·보호·청산·서명 경로를 그대로 통과한다. 체결되지 않는다고 보장하지 않는다.

서명 조회 NEW/체결0 확인 뒤 이 프로그램의 WSS를 code1011로 딱 한 번 닫는다. 기존 session finally의 PAUSE_ENTRIES가 잔량을 취소한다. stream_ready/reconciled false와 신규 진입 게이트 차단을 확인하고, 한 번만 새 WSS 연결과 전체 REST 대조를 실행한다. 같은 거래소 주문 ID가 CANCELED/체결0인지 확인하고 같은 의도 submit이 POST를 추가하지 않는지도 검사한다. 주문을 NEW로 되돌리거나 재제출하지 않는다.

성공: OPEN_ORDER_SOCKET_LOSS_CANCEL_RECONNECT_VERIFIED, 신규 진입 POST1/attempt1, injected close1/reconnect1, gate blocked true, cancelled_unfilled_order_verified true, VERIFIED_FLAT, NORMAL, paused true, stream/reconciled false, 기존 주문·요청·기준값 보존.

도중 체결·불확실한 조회·재연결 실패는 성공으로 판정하지 않는다. 기존 session 및 최종 pause/조건부 EMERGENCY_FLATTEN/confirm_exit로 보호와 정리를 시도하고 결과를 출력한다. UNKNOWN은 flat을 보장하지 않으므로 후속 확인이 필요하다. 재시도/위험 잠금 해제를 자동 실행하지 않는다.

이는 로컬 소켓 단절 실험이며 인터넷 단절, guard 프로세스 강제 종료, 호스트/DB 재시작, 보유 포지션 중 장애, 실제 부분체결 보호 또는 장기 무인운용을 검증한 것이 아니다. LIVE/Phase5 승인이 아니다.
