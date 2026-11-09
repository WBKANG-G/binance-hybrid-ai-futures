# Windows DEMO 비상 상태 복구 승인 완료

근거: 사용자 CMD 결과 캡처. 2026-10-31 한국 시간.
배포: windows-demo-recovery-https-reuse-20261031 / 4bd1ca2.
작업 ID: 75803610-46ec-4498-b169-3338412081fa.
검토 토큰: 136f461a61ecd53d2a027aa6cb11d7a8e1015a20714e9e46dc21b78260b11529.

사용자가 review_demo_recovery --apply-review와 --acknowledge-unresolved-history로 실행했다.

실제 결과:
- recovery EMERGENCY_ACKNOWLEDGED_ENTRIES_STILL_PAUSED
- orders_enabled false
- live_trading_enabled false

이 결과는 적용 직전 전체 복구 검토를 다시 통과하고 토큰 일치를 확인한 뒤 비상 상태를 NORMAL로 전환했음을 뜻한다. 신규 진입은 paused이며 stream_ready/reconciled는 false로 유지한다. 미해결 이력3건, 기존 요청/주문/전송하지 않은 증거 및 회계/기준금/과거 이벤트를 보존하는 원래 승인 함수의 결과다. 이 출력 자체가 현재 잔고를 제공하는 것은 아니다.

직전 읽기 전용 검토는 전체55개 요청,7배치,서명GET244회,추가 시계 프로브0회,미해결3건,리스크PASS,포지션없음,활성주문0이었다. HTTPS 연결1개/재사용243회/서버종료0/전송측정13891ms로 기존40초 제한 안에서 완료했다.

TP 로컬 조립 실패 대응과 그 후 복구 검토 시간 초과 수정 및 명시적 비상 상태 승인까지 확인했다. 신규 주문/자동 진입 재개/실제 부분체결 보호 통과/Phase4 전체 완료/LIVE/Phase5 승인으로 확대하지 않는다. 같은 복구 명령을 반복할 필요가 없다.
