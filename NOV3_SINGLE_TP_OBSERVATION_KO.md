# 11월3일 실제 DEMO TP 관찰 결과

작업 6703f8c0-0c8a-4a2b-84d5-37e39693dbba는 사용자 Windows에서 실제 실행됐다. PASSIVE LONG 최대0.0007 BTC, 지정가77010.90/SL76625.80/TP77781.10이었다. 진입 POST1회, 체결0.0000, CANCELED/CLOSED/UNFILLED, FINISHED_FLAT, VERIFIED_FLAT로 종료됐다.

TP 검사는 NOT_ACCEPTED/TP_FILL_NOT_OBSERVED다. 실제 TP 체결·SL 취소 성공으로 수락하지 않는다. stop_verified/target_verified/filled_protection_and_flat_verified false, operator exit POST0/SL DELETE0, session_error false, completion_failure NONE이었다. 과거 reconciliation mismatch/VERIFIED_INITIAL_FILL 진단은 이번 체결 증거가 아니다.

기존 이력·기준금 유지, NORMAL/final_paused true, orders_enabled/live_trading_enabled false가 보고됐다. 설정상 observation_seconds300은 주문이 실제300초 유지됐다는 증거가 아니다. 코드상 신호 생성부터10초 유효기간과 구분한다. 실제 취소 원인과 시간은 아직 확정하지 않았다.

다음은 windows-demo-tp-timing-diagnostic-20261103 브랜치의 읽기 전용 과거 주문 시간 진단이다. 새 주문 재시도·수량 증가·가격 추격·TP거리/TTL 변경 없이 진행한다. 진단은 합성 PostgreSQL 테스트7개, Ruff 및 strict mypy를 통과했으나 Windows 실행 결과는 아직 없다. Phase4 전체 완료/main 병합/LIVE/Phase5와 구분한다.
