# 보호 worker 종료 검증 상세

부모는 phase4-db-disconnect-review-20261101 / 326ce4c, ZIP SHA256 550b4006e2ca9ca85ab74de6aa5c9caf496eb18b79e8dbe176af112b78929fa9다. 부모443개 파일 모두 바이트 동일하게 보존했다. 새 시험 helper/통합 시험/단위 시험과 한국어 보고서·검증 문서5개만 추가했다. 원래 시험을 변경하지 않았다.

추가 파일:

- tests/fixtures/demo_protected_worker.py: 실제 child가 원래 DemoStore/DemoCoordinator/session을 실행한다. 별도 TEST_ONLY_PROTECTED_WORKER 플래그, 고유 시험 schema 및127.0.0.1 PostgreSQL/HTTP/ws 주소만 허용한다. 고정 합성 키만 사용하고 BINANCE 환경 값이 있으면 DB 접근 전에 차단한다. 실제 키 캐시를 읽지 않는다. 프록시와 redirect를 사용하지 않는다.
- tests/integration/test_demo_protected_worker_sigkill.py: 고유 PostgreSQL schema, child와 별도 부모의 공유 합성 HTTP 거래소, 실제 로컬 WebSocket 연결을 사용한다. 준비 표식/SL·TP 검증 후 실제 SIGKILL한다. 원래60초 리스가 실제 UTC 시간으로 만료될 때까지 기다리고 monotonic 경과와 비교한다. lease_until이 그대로임도 확인한다.
- tests/unit/test_demo_protected_worker_scope.py: 원격/모호한 주소5종, 잘못된 키 전송 차단, 명시적 시험 플래그 및 BINANCE 환경 거부를 검사한다.

보호 상태 확인은 원래 stop_verified/target_verified와 추가 서명 조회를 함께 사용한다. 종료 후 shared venue가 유지되며31회 점검과 동일 포지션 확인을 수행했다. 새로운 fixture에 주문을 다시 만들어 복구하는 방식이 아니다. worker PID는 직접 생성한 Popen 객체만 종료하며 다른 프로세스를 찾거나 종료하지 않는다. finally 표식이 없고 exit code-9임을 요구한다.

이전 리스의 부작용 callback과 renew를 새 펜스 이후 거부하는지 확인한다. 새 리더 acquire 뒤 stream/reconciled false를 검사하고, 진입을 시도해 거부되는지 검사한다. 별도의 읽기 전용 장부 복사에서 paused만 false로 하여 신뢰 플래그 자체로도 _fresh가 거부하는지 확인하며 실제 DB 상태를 변경하거나 주문을 전송하지 않는다. 새 WebSocket handshake와 REST 대조 후 원래 client/server 진입 ID 및 SL/TP ID를 회복한다. 명시적 일시정지는 유지한다.

회복 후 기존 submit 재호출도 추가 POST가 없고, 원래 진입1/보호2만 존재하는지 확인한다. 후속 정리는 별도 합성 reduce-only 비상 종료와 VERIFIED_FLAT이며 실제 시장 주문이 아니다. 일간·주간 기준일/기준금·순이체/정책 일치, 고점 비감소를 검사한다. 시험 schema/서버/프로세스는 finally에서 정리한다.

검증 수치: 전체1291개 통과(376.16초), 집중9개 통과(61.96초), 실패·건너뛰기 없음. Ruff 검사/포맷 검사 및 mypy115개 소스 통과.

집중 시험 JSON 기록은 natural_wait_ms59969, remaining_at_wait_start_ms59962, protection_probe_count31, child_exit_code-9, fence1→2, wss_connections2, entry_posts_before_cleanup1, protection_posts2, final_flat VERIFIED_FLAT, real_exchange_calls0이었다. 이 수치는 집중 실행의 결과이며 전체 회귀 실행에서 측정한 다른 시간을 같은 수치라고 주장하지 않는다.

검증 환경은 Linux/POSIX, 실제 격리 PostgreSQL, ws:// 로컬 서버 및 합성 거래소다. 실제 Binance WSS/TLS·listen-key 수명, 보호 중 실제 시장 변동/TP·SL 체결, 제출 중 종료·미보호 상태·호스트 전체 종료·거래소나 DB 동시 장애는 별도다. 실제 사용자 DB/계좌/키/export와 main/LIVE/Phase5는 건드리지 않았다. 이 묶음은 Windows 앱 교체나 새 명령을 요구하지 않는다.
