# 반대 호가 지정가 DEMO 검증 준비 기록

Python 전체: 361 passed / 0 failed / 0 skipped (60.27초).
실제 PostgreSQL 격리 schema와 localhost WebSocket, 합성 거래소 응답/자격증명을 사용했습니다.
전체 mypy strict 62 source 통과. 변경 파일 Ruff check/format 통과.

추가 검증:
- 읽기 전용 LONG=ask / SHORT=bid, 정확한 tick 가격 사용, tick 불일치 거부.
- LONG/SHORT에서 LIMIT 1회·수량 상한 유지·미체결 취소·체결 후 SL/TP 확인 및 실제 flat 정리.
- 같은 작업 ID의 방식 변경 거부, 동일 방식 복구 시 가격 추격/추가 진입 없음.
- 일반 실행에 명시적 반대 호가 옵션을 잘못 혼용하면 인증 전 거부.
- 거래소 필드 누락/숫자 형식 오류를 DemoFailure로 정제해 기존 최소 positionRisk 위험 축소 경로로 연결.
  기존 필터 불량 테스트도 동일한 필드 거부와 주문 미전송을 유지하며 예외 종류만 정제된 도메인 오류로 갱신했습니다.
- 체결 후 격리 담보 필드 누락을 주입해 reduce-only 종료 시도가 실행됨을 확인했습니다.

기본 PASSIVE·TTL·금액/수량 한도·LIVE false·PAPER 기본 API·계좌 마진 모드 자동 변경 금지는 유지합니다.
main/이전 ZIP/의존성 lockfile/MASTER_SPEC/AGENTS/실거래 위험 설정은 변경하지 않았습니다.
프런트엔드는 변경/재검증하지 않았습니다.

실제 Windows Binance 관측: 이전 작업은 entry_state=CANCELED / entry_outcome=UNFILLED / FINISHED_FLAT,
수량 0.0007 BTC, stop/target 미검증, session_error=false였습니다(사용자 화면 증거).
새 OPPOSITE_QUOTE 실제 Binance 실행은 아직 하지 않았습니다. 체결이나 보호 주문 지원을 보장하지 않습니다.
Phase 4 전체 완료를 선언하지 않으며 실거래와 Phase 5는 시작하지 않았습니다.
