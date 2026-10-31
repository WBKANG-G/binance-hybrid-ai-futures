# 손절 확인 후 로컬 익절 조립 실패·진입 차단·비상 정리 검증

새 windows-demo-tp-fault-check 폴더의 CMD 하나에서 실행한다. 기존 Galaxybook hybrid-windows DB/볼륨/기준금/이력을 유지한다. 이번 검증은 신규 DEMO 지정가 주문1건을 제출한다. 익절 실패는 로컬 주문 조립 단계의 명시적 장애 주입이며, 거래소 거부 응답을 만들어내지 않는다.

## 순서

1. ZIP을 새 폴더에 풀고 해당 폴더에서 CMD를 열어 빌드가 모두 Healthy가 될 때까지 기다린다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait
```

2. 교체된 API 컨테이너의 private tmpfs 키를 등록한다. DEMO API Key와 Secret은 숨김 입력한다. 채팅에 보내지 않는다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -m scripts.demo_key_session --register
```

3. 아래 명령을 한 번만 실행한다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -T api python -m scripts.check_demo_tp_fault --enable-demo --enable-tp-build-fault --operation-id 426dd7a2-805b-4576-a1f4-85313a7c4f6c
```

예약 정보 → TP_NOT_SENT_ENTRIES_BLOCKED_SL_RETAINED → 최종 결과까지 기다린다. CMD를 닫거나 API를 재시작하지 않는다. 오류/미체결/NOT_ACCEPTED가 나와도 재실행하지 않고 최종 출력 전체를 보낸다. 이미 생성된 동일 의도는 복구·정리만 수행하며 새 주문이나 장애를 추가하지 않는다.

## 실행 내용과 판정

빈 NORMAL/paused ISOLATED BTCUSDT 2x 계좌에서 기존 prepare의 LONG OPPOSITE_QUOTE를 사용한다. 최신 ask 기준의 지정가이며 시장가 진입으로 바꾸지 않는다. 기존 최소 허용 수량 및 cap,100 USDT 진입명목 상한, 신호TTL10초, 손절/익절·리스크·freshness·서명·lease 제한을 유지한다. 체결을 보장하지 않는다. 미체결이면 취소·flat으로 끝나고 이 장애 검증은 통과하지 않는다.

체결 뒤 기존 보호 코드가 손절을 등록·조회한다. 익절 조립 함수에 들어갈 때 같은 의도의 손절을 signed GET으로 다시 확인한다. 거래소 ID/종목/방향/트리거/closePosition/MARK_PRICE/priceProtect=false/NEW 및 기존 보호 상태가 모두 맞아야, 이 의도의 TAKE_PROFIT_MARKET 조립에만 ValueError를1회 주입한다. SDK로 익절 POST를 전송하지 않는다. 원래 코드의 NOT_SENT + LOCAL_VALIDATION_FAILED 기록 경로를 그대로 사용한다. 따로 상태를 바꾸어 NOT_SENT를 만들어내지 않는다.

원래 TP 실패 대응은 신규 진입을 멈추고 이미 등록된 SL을 유지하는 것이다. 자동 즉시 청산이라고 주장하지 않는다. 도구가 paused/reconciled false, 진입 게이트 거부, SL 보호·노출 유지, TP NOT_SENT 증거를 먼저 확인한다. 이후 세션과 그 finally를 완전히 drain하고 기존 비상 정리 명령으로 잔량을 취소·실제 포지션을 줄인다. 기존 confirm_exit로 flat 및 청산 FILLED를 확인한다. UNKNOWN 종료를 재POST하지 않는다.

성공 결과: TP_BUILD_FAILURE_PAUSE_SL_RETAINED_OPERATOR_FLAT_VERIFIED. 장애1, signed_stop_before_fault_verified/entries_paused_after_fault/entry_gate_blocked_after_fault/tp_not_sent_proof_verified true, unexpected_tp_transport_attempts0, 신규 진입POST1, reduce-only 종료 시도1 이상, VERIFIED_FLAT/exit FILLED, 이력·기준 보존, 최종 EMERGENCY/paused를 모두 요구한다.

성공해도 비상 정리 명령이 남기는 EMERGENCY 잠금은 유지한다. 자동 해제나 신규 진입 재개를 하지 않는다. 결과를 확인한 뒤 별도 읽기 전용 복구 검토로 진행한다. flat UNKNOWN은 정리 완료를 뜻하지 않으므로 결과 확인 전 다른 주문/재실행/잠금 해제를 하지 않는다.

임시 함수 교체는 이 CLI Python 프로세스의 own TP에만 적용되고 정리까지 drain한 뒤 원래 함수가 복원된다. Uvicorn/다른 프로세스나 저장된 SDK·coordinator 파일을 바꾸지 않는다. 손절 요청은 원래 조립·전송·검증 그대로다. TP가 뜻밖에 전송 경로까지 오면 추가 장벽이 SDK 전에 차단하며 그 경우 성공을 거부한다.

로컬 TP 조립 실패에 대한 실제 DEMO 대응 검증이며 실제 거래소의 Algo 거부/네트워크 실패나 손절 생성 실패를 직접 일으킨 것이 아니다. 부분체결 전체 보호 통과, 보유 Worker 강제 종료,24시간 운용 또는 Phase4 전체 통과로 확대하지 않는다. LIVE/Phase5를 시작하지 않는다.
