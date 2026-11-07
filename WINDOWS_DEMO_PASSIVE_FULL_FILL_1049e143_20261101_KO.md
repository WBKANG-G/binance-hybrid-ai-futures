# 11월1일 단일 PASSIVE 체결 및 후속 복구

근거는 사용자 CMD 결과 캡처다. 실행 배포는 windows-demo-one-key-session-20261101 / 7e290f1이며 원본 캡처 파일 자체를 이 문서에 저장한 것은 아니다.

## 실제 진입·보호·종료

작업1049e143-af7e-4d62-a793-93164688c4c6, LONG/PASSIVE, 수량과 cap0.0007 BTC, 진입 지정가77675.40, SL77287.00, TP78452.20이었다. 최종 출력은 FINISHED_FLAT, entry_filled true, filled_quantity0.0007, entry_state FILLED, position_state CLOSED, entry_outcome FULL_FILL이었다. stop_verified/target_verified/filled_protection_and_flat_verified는 모두 true, session_error false, session_failure NONE, completion_stage FLAT_CONFIRMATION, completion_failure/completion_database_failure NONE이었다.

초기 반영 검증은 VERIFIED_INITIAL_FILL 및 initial_fill_settled true였다. 마지막 불일치 필드 목록은 available_balance, entry_prices, equity, isolated_margins, isolated_wallets, liquidation_prices, margin_used, orders, positions, wallet_balance였다. 이 진단 목록을 현재 실행 실패라고 해석하지 않는다. private 진단 RECEIVE/NONE/event NONE, callback23680ms, received6/coalesced5, clock offset-168ms/RTT144ms/wait0, localage2491ms가 출력됐다.

이번 범위는 전량 체결·SL/TP 접수/조회 확인·원래 운영자 종료 및 flat 검증이다. 실제 부분체결이나 TP 트리거 체결을 수락하지 않는다. 부분체결을 얻기 위한 반복 주문은 실행하지 않았다.

## 종료 이후 상태와 복구

NORMAL 전용 준비 확인은 EMPTY_NORMAL_PAUSED_REQUIRED로 차단됐다. 이후 읽기 전용 전체 검토에서 EMERGENCY/paused true를 확인했다. 해당 차단은 Docker 장애나 포지션 잔존을 그 자체로 증명하지 않는다.

전체 복구 검토 결과는 REVIEW_ONLY_NO_DATABASE_CHANGE, audited_request_count71, audit_batch_count9, signed_query_count308, audit_clock_probes0, unresolved_count4, risk_gates PASS, position_nonzero false, active_orders0이었다. POOLED_HTTPS 연결1/재사용307/서버close0/전송17865ms, orders/LIVE false였다. 토큰은 aee8a90adae664b3b830e38b9ffb494e65bb25afc59edbd36cbbc1431a36a1b5였다.

후속 명시적 적용 작업3efd448c-765b-45fd-ac5d-ae2713046c99는 EMERGENCY_ACKNOWLEDGED_ENTRIES_STILL_PAUSED, orders_enabled false, live_trading_enabled false를 반환했다. 기존 검토 경로가 전체 검토와 토큰 일치를 재검증한 후 비상 상태를 승인하고 진입 정지를 유지했다. 과거 요청4건을 삭제하거나 성공으로 재분류하지 않는다. 현재 잔고는 이 출력에 없으므로 추정하지 않는다.

Phase4 전체 완료, LIVE 또는 Phase5 전환을 의미하지 않는다.
