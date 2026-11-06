# TP 관찰15d30a13의 실제 미체결·안전 종료

근거는 사용자가 공유한 Windows CMD 결과 캡처다. 배포 windows-demo-tp-trigger-check-20261101 / 512e169, ZIP SHA256 3da7206f161a6f877ed61067ed3ab4dfc12c6273a2d3101b3d34bc911d1fa30c를 사용했다. 원본 캡처 파일 자체가 이 문서에 포함된 것은 아니다.

작업15d30a13-4f65-407d-ae41-f58dd61d429f는 DEMO/LONG/PASSIVE, 수량/cap0.0007 BTC, 지정가77695.40, SL77306.90, TP78478.40으로 예약됐다. 최종 일반 검증은 FINISHED_FLAT, entry_filled false, filled_quantity0.0000, entry_state CANCELED, position_state CLOSED, entry_outcome UNFILLED였다. stop_verified/target_verified/filled_protection_and_flat_verified는 false였다. 체결0이므로 보호 검증 성공으로 취급하지 않는다.

최종 TP 전용 결과는 NOT_ACCEPTED, failure TP_FILL_NOT_OBSERVED, flat_confirmation VERIFIED_FLAT, risk_state NORMAL, final_paused true, history_and_baselines_preserved true였다. entry_post_attempts/new_entry_post_count는1, operator_exit_post_attempts와 sl_delete_attempts는0이었다. TP 체결 및 SL 취소 증거 플래그는 false였다. observation_seconds300은 설정값이며 실제 monotonic 경과 시간은 이 출력에 별도로 없다. 원래 진입 만료 조건도 연장하지 않았다.

세션 오류false, session_failure NONE, completion_stage FLAT_CONFIRMATION, completion_failure/completion_database_failure NONE이었다. private 진단 PING/NONE/event NONE, callback17619ms, received2/coalesced0, offset-83ms/RTT149ms/wait0/localage6843ms였다. 이전 진단 필드 목록과 reconciliation_settle_reason VERIFIED_INITIAL_FILL이 남아 있었지만 현재 작업의 initial_fill_settled false 및 체결0과 구분한다. 과거 진단을 이번 체결 증거로 재사용하지 않는다.

판정은 미체결 주문 취소·flat·NORMAL/paused의 안전 종료 범위 통과이며 TP 실제 체결/잔여 SL 정리는 미수락으로 유지한다. 추가 주문 반복이나 수량/가격 변경, 비상 복구 적용은 하지 않았다. Docker가 표시한 일반 오류 도움말을 Docker 장애 증거로 해석하지 않는다. LIVE/Phase5·Phase4 전체 완료를 의미하지 않는다.
