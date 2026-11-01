# DB 연결 단절 검증 상세

부모는 windows-demo-tp-trigger-check-20261101 / 512e169, ZIP SHA256 3da7206f161a6f877ed61067ed3ab4dfc12c6273a2d3101b3d34bc911d1fa30c다. 부모439개 파일을 바이트 동일하게 보존하며 새 통합 시험 하나와 한국어 보고서/검증/실제 TP 미체결 기록만 추가한다. 원래 시험의 기대나 실행 구현은 바꾸지 않는다.

추가 시험은 tests/integration/test_demo_database_disconnect.py다. 기존 isolated DEMO fixture와 고유 PostgreSQL schema를 사용한다. 제어 엔진은 실행 엔진과 별도 연결이고, 정확히 실행 중인 연결의 backend PID만 종료한다. 관리자와 대상 PID가 다름을 확인하며 pg_terminate_backend(pid, 2000)의 성공·실제 DBAPIError·connection_invalidated를 요구한다. listener는 context 종료 시 제거하고 제어 엔진은 dispose한다. 시험 schema는 원래 fixture가 정리한다.

예약/의도 단계는 이전 장부 완전 일치 및 이벤트/outbox 수량 동시 롤백, POST0을 검사한다. effect 단계는 의도 이벤트/outbox 한 쌍만 COMMIT되고 효과 이벤트가 없음을 검사한다. SUBMITTING 증거와 동일 주문 조회를 보존하며 ACK를 임의로 기록하지 않는다. 연결 복구 직후 같은 ID 재호출이 추가 POST를 만들지 않는지 검사한다.

보호 중 heartbeat 단절은 원래 실제 로컬 WSS session에서 발생한다. session_failed와 장부의 차단 상태, 기존 SL/TP NEW, 새 진입 거부를 확인한다. 논리 시계61초 전진 뒤 펜스+1과 이전 소유자 부작용/갱신 거부를 확인한다. 새 WSS handshake 및 원래 REST 대조를 거친 새 리더가 동일한 보호 ID를 회복하는지 검사하며, 이후 합성 포지션은 기존 비상 종료와 VERIFIED_FLAT으로 정리한다.

실제 DBAPI 예외를 로컬로 바꿔치기하지 않고 PostgreSQL backend를 종료한다. 거래소는 합성이므로 실제 Binance DB 장애 수락으로 확대하지 않는다. 논리 시계 이동은 명시적이며 실제60초 경과나 운영 DB 리스 강제 수정을 하지 않는다. 기존 SDK·리스크·시계·리스/펜스·기준금·키·Compose·exports는 모두 그대로다.

검증 수치: 전체1282개 통과(314.23초), 집중4개 통과(2.02초), 실패·건너뛰기 없음. Ruff 검사/포맷 검사 및 mypy115개 소스 통과.

재현은 별도의 격리 PostgreSQL에서 TEST_DATABASE_URL을 설정하고 위 시험 파일을 pytest로 실행한다. 실제 앱 DB/계좌를 대상으로 장애 시험을 실행하지 않는다. 이 자료는 사용자 Windows에서 실행할 새 명령이나 컨테이너 중지 절차를 요구하지 않는다. 클라우드 검증에 실제 키/거래소 인증 요청은 사용하지 않았다. main/LIVE/Phase5 및 Phase4 전체 완료 판정은 변경하지 않는다.
