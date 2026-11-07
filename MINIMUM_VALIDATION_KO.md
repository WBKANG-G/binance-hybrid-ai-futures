# 최소 수량 DEMO 실행 준비 검증 기록

Python 전체: 350 passed / 0 failed / 0 skipped (59.08초).
실제 PostgreSQL 격리 schema와 localhost WebSocket + 합성 Binance 응답/자격증명으로 검사했습니다.
최종 결과에 entry_state/position_state를 추가한 후 검증 실행 관련 10개를 다시 실행해 모두 통과했습니다(6.31초).
Ruff check/format 통과, 전체 mypy strict 62개 source 파일 통과. 최종 보고서 변경 후 해당 모듈 타입 검사도 통과.

추가 확인:
- 수량 상한은 위험 계산으로 커지지 않으며 예약/재시작/POST/UNKNOWN 복구에서 유지됩니다.
- float/비유한수/음수/0/step 불일치/최소 명목금액 미충족 상한을 거부합니다.
- 같은 신호의 상한 변경/삭제, 잘못된 CLI 옵션 혼용을 거부합니다.
- DEMO_VALIDATION purpose는 DEMO에서만 허용하며 strategy=null이어야 합니다. 일반 전략 신호는 여전히 기존 전략을 요구합니다.
- 실제 데이터 형식의 확정 1분봉/현재 호가/시간/계좌 snapshot 증거를 요구합니다. 테스트 데이터는 합성임을 명시합니다.
- 지정가 단일 진입·부분/전체 체결 보호·미체결 잔량 취소·reduce-only 종료·실제 flat 이후 Algo 정리를 검사했습니다.
- 청산 ACK 이후 새 position=0 증거로 동일 작업을 재대조하는 경로를 확인했습니다.
- 같은 작업 ID는 새 진입을 생성하지 않습니다. UNKNOWN exit는 무조건 재전송하지 않습니다.
- 보호 실패 후 flat이어도 체결·보호 수락 성공으로 표시하지 않습니다. 미체결/부분/전체를 구분합니다.

실제 Binance DEMO 주문/SL·TP 생성/트리거/장애 복구는 아직 Windows에서 실행하지 않았습니다.
테스트 통과는 실제 체결 가능성이나 수익성을 뜻하지 않습니다. 이 배포로 Phase 4 완료를 선언하지 않습니다.
실거래/Phase 5 미착수. main/기존 ZIP/의존성 lockfile/MASTER_SPEC/AGENTS/실거래 위험값은 변경하지 않았습니다.
프런트엔드는 변경하지 않았으며 이번에 프런트엔드 테스트를 재실행하지 않았습니다.
