# 취소 주문 수량 오배정 수정 및 읽기 전용 이력 조회 검증

전체 484개 테스트 통과(103.92초). mypy 72개 소스 통과. Ruff 전체 lint/186개 파일 format 검사 통과. 실제 PostgreSQL과 합성 거래소 응답, 로컬 WS를 사용했습니다. 실제 Binance 인증·주문 호출은 클라우드에서 수행하지 않았습니다.

기존 코드에 새 회귀 테스트를 적용하면 4개가 실패했습니다. 수정 코드에서는 모두 통과하고 기존 테스트도 통과합니다. 실제 사용자 DB를 테스트 목적으로 수정하지 않았습니다.

수정: 비상 포지션 조회에서 체결량 0인 종료 주문에 같은 종목의 다른 포지션을 배정하지 않습니다. 기존에 저장된 모순된 기록은 자동 변경하지 않습니다. 귀속할 수 없는 실제 포지션도 EMERGENCY/paused를 유지하고 취소 주문에 임의 배정해 시장가 종료하지 않습니다. 신규 진입의 LIMIT 전용 규칙과 실제 체결 주문의 비상 reduce-only 종료 경로는 유지합니다.

새 읽기 전용 도구는 사용자 캡처의 제한된 모양(CANCELED, filled/remaining=0, open_quantity=원수량, EXIT_PENDING, UNKNOWN, protected=false)만 대상으로 합니다. 원래 clientOrderId/serverOrderId/종목/방향/모드/유형/가격/수량과 거래소 취소·체결량 0을 반복 대조합니다. 현재 포지션·활성 일반/조건부 주문은 모두 없어야 하고 Isolated 2x여야 합니다. 찾을 수 없는 원래 주문을 미체결로 간주하지 않습니다.

거래소 근거가 일치할 때 메모리의 분석용 사본에서만 그 취소 주문의 open_quantity를 제외해 기존 GET-only 이력 조회를 수행합니다. 분석용 사본은 주문 실행/잠금 해제/복구 적용에 전달하지 않습니다. 기존 flat-history 서비스의 원래 guard는 변경하지 않았습니다. 결과에서 모순된 기록 시각을 local_flat_anchor 대신 legacy_observation_anchor로 명시합니다. local_ledger_repaired=false, recovery_eligible=false입니다.

새 테스트 22개는 취소/거절/만료 주문에 대한 오배정 방지와 귀속 불명 포지션의 잠금 유지, GET-only/lease·쓰기 금지, 원본 DB·입력 객체·미확정 요청·손실 기준·잠금 불변, 키 미출력 및 환경 정리, 거래소 ID·방향·가격·수량·체결량·상태 불일치/주문 not-found/로컬 모양 불일치/현재 노출/스냅샷 변화/쓰기 활성 문맥 차단을 확인합니다.

변경 범위: services/execution/demo.py의 비상 귀속 guard, 해당 회귀 테스트, 새 조회 서비스·CLI·통합 테스트, 안내·검증 문서. MASTER_SPEC/AGENTS/의존성 lock/Compose/Isolated 설정/위험 기준/복구 적용 코드는 보존합니다. Compose 검증 결과 API는 PAPER/LIVE=false이고 기존 hybrid-windows_postgres_data 볼륨을 사용합니다.

제한: 실제 사용자의 과거 취소 주문은 새 도구 실행 결과로 확인해야 합니다. DB의 과거 수량 모순과 UNKNOWN/SUBMITTING은 이번에 복구하지 않습니다. BTCUSDT 체결 내역은 모든 종목의 과거 포지션/자정 평가액을 증명하지 않으며 일별 손실 기준의 복원 근거로 단독 사용하지 않습니다. main 변경, 실거래, Phase 5 시작 없음.
