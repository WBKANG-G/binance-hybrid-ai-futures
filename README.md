# PostgreSQL 작업 중 연결 종료 검증 자료

사용자 Windows에서 실행할 새 명령은 없습니다. 앱/Compose/키/SDK/리스크와 부모439개 파일을 그대로 유지했습니다. 새 통합 시험과 한국어 보고서·검증·실제 TP 미체결 기록만 추가했습니다. 재빌드·키 재입력·DB 중지·추가 주문이나 TP 시험 반복을 요구하지 않습니다.

새 시험은 격리 PostgreSQL의 시험 연결만 pg_terminate_backend(pid,2000)로 실제 종료합니다. 예약/의도 COMMIT 이전에는 state/event/outbox 롤백 및 POST0을 확인합니다. 합성 거래소 ACK 뒤 effect COMMIT 실패에서는 SUBMITTING 의도만 남기고 같은 ID로 조회 복구해 POST1을 유지합니다. 보호 중 세션 heartbeat 실패에서는 진입 차단과 SL/TP NEW를 확인합니다. 논리 시계61초 전진 뒤 펜스+1, 이전 리더 갱신/삭제 차단, 새 로컬 WSS/REST 재검증 후 같은 주문·보호 ID 회복을 확인합니다.

전체1282개·집중4개 시험, Ruff/mypy115개 소스 통과. 실제 DB 연결 종료 + 합성 거래소 + 로컬 WSS의 증거이며, 실제 Windows DEMO 작업 중 DB 서버 정지·전원/디스크 손실·실행 worker SIGKILL·자연60초 경과를 대신하지 않습니다. 실제 API 키/인증 거래소 호출과 사용 중인 Windows DB의 장애 주입은 없습니다.

직전 실제15d30a13은 미체결 취소·VERIFIED_FLAT·NORMAL/paused이며 TP 시험은 TP_FILL_NOT_OBSERVED로 미수락입니다. 부분체결/TP 실제 체결·실행 worker 장애·외부 알림 등 남은 범위를 성공으로 바꾸지 않습니다. main/LIVE/Phase5 및 Phase4 전체 완료 판정은 변경하지 않습니다. 자세한 결과와 한계는 DB_DISCONNECT_REPORT_KO.md 및 DB_DISCONNECT_VALIDATION_KO.md를 참고합니다.
