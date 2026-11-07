# Windows 복구 검토 시간 제한 중단

사용자 CMD 캡처: windows-demo-tp-fault-check에서 scripts.review_demo_recovery를 인자 없이 실행했습니다.

- recovery: BLOCKED_KEEP_LOCK
- stage: RECOVERY_REVIEW
- error_code: REQUEST_AUDIT_TIME_BUDGET
- periods: {}
- next_action: SHARE_CODE_DO_NOT_RETRY_OR_UNLOCK
- orders_enabled / live_trading_enabled: false

이 결과는 복구 검토가 완료되지 않았다는 뜻입니다. 잠금을 풀거나 같은 명령을 반복하지 않습니다. Docker의 Gordon 안내 문구는 별도 Docker 안내이며 애플리케이션이 반환한 시간 제한 코드를 대체하지 않습니다.

직전 TP 조립 실패 시험은 운영자 비상 종료 뒤 VERIFIED_FLAT를 확인했습니다. 그 과거 결과만으로 현재 상태를 확정하거나 새 검토를 생략하지 않습니다. EMERGENCY/paused는 그대로 유지합니다. 이번 수정본의 새 읽기 전용 검토가 성공하더라도 잠금은 자동 해제되지 않습니다.
