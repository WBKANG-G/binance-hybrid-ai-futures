# income 시각 정밀도 차이 수정 검증

전체 650개 테스트 통과(113.04초). 이전 관련44개+새22개=66개 통합 테스트 통과(26.42초). 새 테스트 fixture의 수수료를 실제 캡처 값으로 맞춘 뒤 전체650개를 다시 통과했습니다. mypy80개 소스 및 전체 Ruff lint/format 통과. 실제PostgreSQL, 합성 거래소와 localhost WebSocket을 사용했습니다. 실제 사용자 DB나 DEMO 계정에 접근하거나 주문을 내지 않았습니다.

부모 ZIP: binance-hybrid-ai-futures-windows-exit-day-review.zip
SHA256: 1440202e275d9a4519fda050afe17ea8a1ccc7d066cb63559c4e926654b74f46

관측 근거: 사용자 signed userTrades 기록의 청산 시각은 2026-10-23T14:58:56.723Z입니다. 별도 GET income 조회에서 수수료 -0.02288622와 실현손익 -0.06111000 USDT가 같은 초의 시작 2026-10-23T14:58:56.000Z로 반환됐습니다. 이전 구현은 tail을 정확한 체결 밀리초 이후로만 잡아 해당 정상 기록을 누락하고 EXIT_INCOME_DOES_NOT_MATCH_FILLS를 반환했습니다. 모든 Binance 응답이 항상 초 단위라고 단정하지 않습니다. 관측된 이 표현과 기존 정밀 시각 표현을 모두 지원합니다.

재현 검증: 부모 ZIP에서 서비스/모델 두 파일을 바이트 그대로 복원한 별도 임시 복사본에서, .723 체결과 .000 income 및 동일 실제 수수료·손익 형태를 실행했습니다. 이전 구현은 사용자의 오류와 같은 EXIT_INCOME_DOES_NOT_MATCH_FILLS로 실패했습니다(1 failed,1.15초). 수정판에서는 같은 회귀 테스트를 통과합니다.

새 대조: 마지막 청산의 정수 밀리초를 1000으로 나누어 정확한 초 시작을 계산합니다. 같은 초의 검증된 청산 주문 외 BTC 체결은 차단합니다. income은 그 초의 시작 시각 또는 그 초에 속하는 검증된 청산 체결의 실제 시각만 허용합니다. +/-1초나 임의 시간 오차를 허용하지 않습니다. 같은 초의 검증된 청산 체결에 대한 수수료·실현손익을 모두 포함하여 개별 금액/건수/총합을 대조합니다. 실제 개별 체결과 맞지 않는 임의 집계는 허용하지 않습니다. 다른 income 종류/심볼/자산, 추가0·상쇄·누락·불일치와 청산 이후 기록은 계속 차단합니다.

정확한 known_exit_utc와 원래 observation_utc는 바꾸지 않습니다. preview에 income_time_basis=SECOND_BUCKET_OR_EXACT_FILL_TIMESTAMP 및 exit_income_second_start_utc를 명시하고 새 채택 receipt에도 대조 방식을 기록합니다. 구버전 receipt에 새 필드가 없으면 EXACT_FILL_TIMESTAMP를 기본으로 해 과거 출처를 새 방식으로 바꾸지 않습니다. 대조 방식은 토큰의 근거에도 포함됩니다. 원래 사용자 확인 기반 source와 account_wide_exchange_export_verified=false는 보존합니다.

새22개 테스트: 초 단위 기록+밀리초 체결의 실제 응답 형태와 GET-only/DB 불변, 실제 적용 시 정밀 청산·관측 시각과 잠금/원장·요청 보존 및 receipt 방식 저장/이전 receipt 읽기, 허용하지 않는 인접시각4가지, 같은 초 또는 이후의 다른 체결7가지, 금액/손익/추가0/상쇄/다른심볼/입출금/누락7가지, 정확한 시각과 초 시작의 혼합 표현, 동일 초의 복수 체결 각각의 수수료·손익 확인 및 임의 집계 차단. 기존628개 테스트는 삭제하거나 완화하지 않았습니다.

변경 범위: 기존 demo_exit_day_review 서비스의 income 시각 대조 및 근거 출력/receipt 속성, ExitAnchoredDayBaseline의 기본값 있는 대조 방식 필드, 새 통합 테스트와 시작·검증 문서. 기존 CLI, DemoCoordinator 및 자동 청산 확인, 위험 규칙/account_risk/정확한 자정 guard, 원장·이전 복구·기준·정정/export 로직, SDK 전송·설정·Dockerfile/Compose/DB 테이블·영속화·의존성·AGENTS/MASTER_SPEC는 부모 ZIP과 동일합니다. DB/볼륨을 초기화하지 않습니다. main/실거래/Phase5 변경 없음.

다음 Windows 실행은 기존과 같은 미리보기입니다. 이 단계에 주문·취소·lease 획득·기준 채택·잠금 해제는 없습니다. 모든 최신 조건 통과 후에만 후보를 제안하며 실제 저장에는 별도 수용·토큰·고정 작업 ID가 필요합니다. 이전 실패는 기준을 변경하지 않았습니다. 이후에도 사용자 확인과 제한된 서명 근거를 결합하는 수동 출처 방식이며 계정 전체 과거 거래소 원본 증명은 아닙니다. 수정된 자동 종료의 실제 DEMO 재검증도 아직 별도 항목입니다.
