# 11월5일 종료된 TP 주문 시간 확인

대상은 bd2a629e-7d8f-4b60-9504-8de2d12f780b 한 건이다. 실제 사용자 결과는 진입 POST1, filled0.0000/CANCELED/CLOSED/UNFILLED, TP_FILL_NOT_OBSERVED/NOT_ACCEPTED, VERIFIED_FLAT/NORMAL/final_paused true다. stop/target_verified false, operator exit POST0, SL DELETE0, history_and_baselines_preserved true. 현재 TP 성공으로 수락하지 않는다. 과거 reconciliation mismatch/VERIFIED_INITIAL_FILL 표시를 이번 체결로 해석하지 않는다.

ZIP을 새 폴더에 풀고 CHECK-NOV5-TP-TIMING.cmd가 보이는 폴더에서 CMD를 열어 실행한다.

```bat
CHECK-NOV5-TP-TIMING.cmd
```

기존 API 안에서 과거 한 건만 조회한다. 설치·재빌드·재시작 없이 SQL READ ONLY 트랜잭션과 읽기 전용 DEMO client를 사용한다. 키가 유효하면 시계 조회와 기존 주문 서명 GET을 수행한다. 없거나 검증 실패면 로컬 증거만 출력한다. 새 주문/취소/리스 획득/기준금 변경은 없다. 출력 JSON 전체를 공유한다. 키/원본 응답/원장/토큰은 출력하지 않는다. BLOCKED/원격 미검증이면 결과를 공유하고 실제 TP 명령을 반복하지 않는다.

비교할 값은 local_cancel_vs_signal_expiry_ms, remote_created_to_last_update_ms, remote_last_update_vs_local_expiry_ms 및 취소가 finish-pause 이전인지다.11월3일 참고값은 로컬7070ms, 원격 생성→최종갱신12163ms, 원격 최종갱신→만료7278ms였다. 실제 신규 값을 아직 측정하지 않았다. 서로 다른 시세/RTT/시계 오프셋/부하의 단일 관찰 비교이므로 성능 보장이나 원인별 시간 분해로 확대하지 않는다.

저장된 만료는 신호 생성부터10초 기준이고 전체 관찰300초와 다르다. 감사 시각은 DB 행 잠금 획득 후 apply 직전 캡처 시각이며 거래소 송신/응답/커밋 시각이 아니다. 이전 진단의 LOCAL_TRANSACTION_START 표시는 이 버전에서 정정했다. updateTime은 거래소 최신 갱신 시각이다. 원장 스냅샷에 주문이 존재한다고 모든 이벤트가 그 주문에서 발생했다고 단정할 수 없다.

주문·수량·TTL·SL/TP 정책은 변경하지 않는다. 실제 TP/부분체결 성공 또는 Phase4 전체/LIVE/Phase5 완료를 증명하지 않는다.
