# 직전 반대 호가 DEMO 안전 종료의 읽기 전용 확인

작업243598bb-900a-45d5-8af3-a776d543a504는 사용자 출력상0.0007 BTC 전량 진입, SL/TP 확인, 안전 종료 POST1회, VERIFIED_FLAT/EMERGENCY/paused, TP_FILL_NOT_OBSERVED/NOT_ACCEPTED였다. 실제 수신 힌트6개/coalesced5는 admission replay를 통과했으며 raw private frame 전체 재생은 아니다.

기존 validate_once는 관찰 후 포지션이 남으면 uuid5(operation,"finish")의 EMERGENCY_FLATTEN을 요청한다. 이 명령은 EMERGENCY를 유지한다. 따라서 flat이어도 자동 NORMAL 복구되지 않는다. 이번 진단은 해당 마커가 실제 원장에 있는지와 기존 주문 증거를 조회한다. 다른 위험 원인을 배제하거나 전체 복구 적합성을 판정하지 않는다.

ZIP을 새 폴더에 풀고 REVIEW-LAST-SAFETY-EXIT.cmd가 보이는 위치의 CMD에서 실행한다.

```bat
REVIEW-LAST-SAFETY-EXIT.cmd
```

기존 API를 재빌드/재시작하지 않는다. SQL은 READ ONLY 트랜잭션으로 계정 및 제한된 감사 기록만 읽는다. 키가 유효하면 시계 조회와 최대10회의 GET으로 현재 포지션/open regular/open algo를 전후 확인하고, 해당 기존 진입/종료 주문 및 SL/TP를 조회한다. POST/DELETE/리스 획득/잠금 해제/기준금 변경은 없다. 과거 export/asyn/income 요청을 반복하지 않는다. 캐시 키를 쓰며 키 입력/키 내용 출력은 없다.

출력은 last-exit-review.json에도 저장하며 기존 파일을 덮어쓰지 않는다. 출력 전체를 공유한다. SIGNED_ENTRY_EXIT_AND_CURRENT_EMPTY_ACCOUNT_VERIFIED는 이번 진입·reduceOnly 종료 체결 및 현재 원격 empty의 한정된 증거다. stop/target은 CANCELED/EXPIRED/FINISHED/REJECTED 중 실제 상태로 표시하며 TP 성공으로 확대하지 않는다. GET 실패/응답 불일치/현재 잔존 위험이면 NOT_VERIFIED_KEEP_LOCK_DO_NOT_RETRY, 키/시계/설정이 사용 불가하면 로컬 증거만 표시한다.

어떤 결과에서도 실제 TP 명령이나 잠금 해제를 반복하지 않는다. full_recovery_eligibility_reviewed와 unresolved_history_acknowledged는 false다. 기존 미해결 요청, 손실 게이트 및 전체 이력 복구 검토는 별도다. 감사 시각은 행 잠금 후 apply 직전 시각이며 거래소 처리/커밋 시각이 아니다. 원장 스냅샷에 대상 주문이 있다고 모든 이벤트가 그 주문 원인이라고 단정하지 않는다. 동시 계정 변경은 전체 row 재확인으로 차단한다.

원장/키/응답 원문은 출력하지 않고 허용한 상태·시각·수량만 표시한다. 이 단계는 기존 EMERGENCY를 유지한다. LIVE/Phase5/Phase4 전체 완료를 포함하지 않는다.
