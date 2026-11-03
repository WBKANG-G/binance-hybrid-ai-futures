# 11월3일 일간 기준금 실제 적용 완료

근거는 사용자 JSON 텍스트 미리보기와 후속 CMD 적용 성공 화면이다. 앱 변경/새 배포가 아니라 기존 NORMAL/paused 일간 도구를 사용한 결과다.

검토 토큰은 bb363899fbe402e033bcf92a379f9bfc7389aa105491c8dddb983db5ab6343a0이며 캡처에서 다시 판독하지 않고 사용자 JSON 텍스트의 값을 검증해 명령에 넣었다. 작업 ID는 abd1db6a-0474-429c-a3d8-86d97a978f95다.

## 검토된 후보

- source OPERATOR_ATTESTED_FLAT_WITH_SIGNED_USDT_EVIDENCE.
- 이전 일간2026-11-02/4998.99761493 USDT → 후보2026-11-03/4998.99761493 USDT.
- boundary_utc2026-11-02T15:00:00+00:00.
- anchor_utc2026-11-01T01:55:09.565889+00:00.
- known_exit_utc2026-11-01T01:30:26.775000+00:00.
- net_deposit_basis0, operator_attestation_used true, account_wide_exchange_export_verified false.
- POOLED_HTTPS, HTTP354회/연결1회/재사용353회, signed GET353회/clock1회.

이 값들은 사용자 제공 텍스트의 후보에 속한다. 새로운 현재 잔고를 임의 추정하지 않는다. 운영자 확인과 서명 이력에 근거한 수동 출처이며 자정 직접 잔고 관측이나 계좌 전체 export 검증이 아니다.

## 실제 적용 출력

- day_baseline OPERATOR_ATTESTED_DAY_BASELINE_ADOPTED_ENTRIES_STILL_PAUSED.
- 같은 작업 ID와 source.
- risk_state NORMAL / paused true.
- daily_baseline_changed true.
- weekly_base_preserved/high_water_preserved/leader_released true.
- account_wide_exchange_export_verified/unresolved_requests_acknowledged/recovery_eligible/orders_enabled/live_trading_enabled false.

금액이 같은 일간 후보를 채택해 기준일을11월3일로 옮긴 성공이다. 주간 기준일/기준금과 고점, 이전 금융 이력·미해결 증거는 유지한다. 출력에 새 별도 잔고 숫자는 없으며 후보의 값과 연결된 적용 성공 범위로 기록한다. 같은 적용 반복이나 신규 진입 재개는 필요하지 않다. Docker Debug 도움말은 별도 오류 근거가 아니다.

## 다음 실제 DEMO 검증 준비

현재 남은 실제 TP 체결·잔여 SL 정리 항목을 준비한다. 이전15d30a13의 미체결 종료는 그대로 미수락이며 체결을 강제하려고 수량·레버리지·가격 정책·보호 거리·관찰 시간을 바꾸거나 반복하지 않는다.

먼저 기존 check_demo_session_ready로 키 RAM·현재 기간·로컬 NORMAL/paused/empty와 서명된 현재 원격 노출·활성 주문 및 장부 불변을 읽기 전용 확인한다. 과거 닫힌 주문 전체의 재감사로 확대하지 않는다. 키를 묻거나 리더를 얻거나 장부/기준금을 변경하지 않는다.

```bat
docker exec hybrid-windows-api-1 python -m scripts.check_demo_session_ready
```

READ_ONLY_READY_ENTRIES_STILL_PAUSED일 때만 후속 실제 주문이 포함된 별도 단일 관찰 범위를 안내한다. 지금은 그 주문 명령을 발행하지 않는다. 이후 도구는 기존 PASSIVE LONG/BTCUSDT ISOLATED2배/수량cap0.0007/명목100USDT/최대300초 및 원래 종료 절차를 유지한다. 실제 TP+SL 취소+VERIFIED_FLAT 증거가 모두 있어야 범위 통과다. 준비 결과가 BLOCKED이면 그대로 확인하고 우회/반복/잠금 해제를 하지 않는다.

이번 작업은 기존 구현/완료 조건 확인과 기록만이며 앱이나 테스트를 바꾸지 않았다. 새 테스트 통과 수를 더하지 않는다. 실제 부분체결 보호·TP 체결/SL 정리·원문 캡처 재생·실제 포지션 장애와 Phase4 전체 완료/main/LIVE/Phase5는 별도다.
