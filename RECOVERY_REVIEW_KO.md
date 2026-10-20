# Phase 4 미확정 요청 복구 검토

현재 Windows는 거래소 flat, 일반/조건부 주문 0, Isolated 2x, EMERGENCY/paused 잠금 상태입니다. 실제 조회 결과 DE 취소 1건, DE 체결 1건, DX 체결 1건, DS UNKNOWN 및 DX SUBMITTING은 NOT_FOUND_UNRESOLVED입니다.

## 소스 확인

종료 요청 의도 저장 후 positionRisk를 다시 조회하여 amount=0 또는 반대 방향이면 POST 없이 반환합니다. 이전 코드는 exit_state만 UNKNOWN으로 바꿔 requests의 SUBMITTING이 남을 수 있었습니다. 로컬 보호주문 구성의 ValueError도 전송 후 통신 실패와 같이 UNKNOWN으로 처리했습니다. 둘 다 과거 실제 요청이 해당 경로였다는 확정 증거는 아닙니다.

## 수정

향후 POST 시도 전 로컬 보호주문 검증 실패는 requests=NOT_SENT 및 LOCAL_VALIDATION_FAILED 증거를 같은 fenced transaction에 기록합니다. 종료 직전 이미 flat 또는 방향 불일치일 때도 NOT_SENT와 POSITION_ALREADY_FLAT/POSITION_DIRECTION_MISMATCH를 기록합니다. 전송 시도 후 오류는 여전히 UNKNOWN이며 NOT_SENT로 표시하지 않습니다.

기존 UNKNOWN/SUBMITTING은 변경하지 않고 과거 NOT_FOUND를 성공·실패·미전송으로 간주하지 않습니다. 새 증거 필드는 기본 빈 dict이므로 과거 기록과 호환됩니다. exit_state=UNKNOWN 및 기존 안전 잠금은 유지되며 같은 ID의 불명확한 주문을 반복 제출하지 않습니다. 일중/주간/낙폭 잠금도 해제하지 않습니다.

## 이번 배포 범위

소스 패치와 조사 문서를 제공하는 검토용 브랜치입니다. 기존 Windows ZIP과 설치본은 바꾸지 않습니다. 현재 과거 두 요청을 자동 해결하거나 새 주문을 가능하게 하는 복구 도구가 아닙니다. 사용자가 추가 명령을 실행할 필요는 없습니다.

잠금을 해제하려면 향후 별도 감사 가능한 운영자 복구 설계가 필요합니다. 거래소 flat과 미체결 0은 필수이나 과거 전송 결과를 증명하지는 않습니다. 수량/계좌/손실 잠금 검증과 기존 UNKNOWN 기록 보존 없이 DB 직접 수정, ledger 교체 또는 새 계정 ID로 우회하면 안 됩니다. 실거래와 Phase 5는 시작하지 않습니다.

검증: 전체 Python 테스트 392개 통과(69.65초), mypy 64개 소스 통과, Ruff 전체 검사 및 162개 파일 포맷 통과. 로컬 PostgreSQL·합성 거래소·로컬 WS 사용. 실제 Binance 인증/주문 호출 없음.
