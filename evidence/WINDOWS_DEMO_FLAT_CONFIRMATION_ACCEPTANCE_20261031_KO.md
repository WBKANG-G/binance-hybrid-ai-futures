# Windows DEMO 기존 종료 확인 수정본 실제 통과

근거: 사용자 CMD 결과 캡처. 배포 windows-demo-flat-confirmation-fix-20261031 / 6e88c76.
ZIP SHA256 d738f8a5dbcbe8cc285dc2df779e7658164647cc654534a9cdccd48a80adda93.
컨테이너 DB/web/API Healthy, 키 private tmpfs RAM 등록 성공.
실행: run_demo --config configs/demo-isolated.example.yaml --account DEMO-WINDOWS-ISOLATED-BTC-V1 --enable-demo --confirm-flat.

## 실제 결과

- completion_confirmation VERIFIED_FLAT
- confirmation_stage VERIFY
- audit_transport_mode POOLED_HTTPS
- audit_http_attempts340 / audit_connection_attempts1 / audit_reused_requests339
- audit_server_close_responses0 / audit_transport_elapsed_ms18825
- audit_last_route OTHER_GET
- confirmation_signed_requests340 / confirmation_clock_probes0
- confirmation_cleanup_attempts0 / confirmation_last_route SIGNED_GET
- environment DEMO / risk_state NORMAL / paused true
- orders_enabled false / live_trading_enabled false

## 판정

기존 종료 체결·포지션/일반 주문/잔여 보호 정리의 원래 flat 증거 판정이 수정본에서 실제 Windows 통과했다. 전체 기존 과거 주문 증거를 포함한 조회340회를 연결1개로 수행하고 전송 측정18.825초로40초 예산 안에서 완료했다. 이는 명령 전체 실행 시간을 의미하지 않는다. 추가 시계 프로브0회는 확인 문맥 내부 추가 조회 수이며 명령 시작의 SDK 시계 동기화 생략을 뜻하지 않는다. 잔여 보호 정리 시도0회였으며 신규 진입/종료 POST를 허용하지 않는 확인 경로만 실행했다. 현재 NORMAL/paused여서 별도 비상 상태 복구 승인은 필요하지 않다.

이전 confirm-flat의 약60초 일반 오류 및 응답 유실 시험의 NOT_ACCEPTED는 삭제하거나 통과로 바꾸지 않는다. 이번 결과는 기존 종료 확인 경로 수정의 실제 수락 증거다. 이전 응답 유실 시험에서는 동일 ID 복구와 중복 진입 방지를 확인했고, 종료 체결/flat 및 복구 승인은 별도 후속 단계로 확인했다. 자동 단일 시험 전체 통과와 구분한다.

현재 신규 진입은 계속 일시정지다. 같은 명령 반복이나 신규 거래는 필요하지 않다. 손절 로컬 실패의 실제 DEMO 시험은 미체결로 미완료, 수정 후 실제 부분체결 보호 성공은 미완료다. Phase4 전체 완료/LIVE/Phase5 전환으로 확대하지 않는다.
