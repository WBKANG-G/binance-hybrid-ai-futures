# 최초 DEMO 체결 반영 지연: 검증 기록

## 실제 캡처로 확인된 내용과 한계

작업 88fe67c5-b91d-4cd1-8b3b-3b5e183ea615는 진입 체결 후 SL/TP 요청 전에 SNAPSHOT_CHANGED로 차단됐습니다. 비상 reduce-only 종료 후 CLOSED/FINISHED_FLAT였지만 보호 확인은 false이고 세션 오류가 있어 자동 검증 성공이 아닙니다. 기존 worker 대기 수정 뒤 completion_failure와 completion_database_failure는 NONE이었습니다.

최초 두 REST 응답의 실제 필드 값은 이전 버전에 보존되지 않았습니다. 포지션 전파 지연과 수수료 반영 지연은 가능한 정상 변화를 재현한 것이며 실제 사용자 원래 원인으로 확정하지 않습니다. 실제 시세/네트워크/거래소에서 새 버전이 통과했음을 주장하지 않습니다.

## 원래 코드 재현과 수정 검증

동일한 신규 테스트를 부모 버전의 demo.py/demo_models.py/demo_validation.py로 되돌린 복사본에서 실행했습니다. 포지션 최초 반영 지연과 수수료 최초 반영 지연 2개 모두 기존 SNAPSHOT_CHANGED 정리 경로로 끝나 SL/TP 확인 실패: 2 failed, 1.31초. 코드 차이의 효과를 확인하는 합성 재현입니다.

수정판 신규 18개 테스트는 통과했습니다(집중 실행 6.43초). 실제 PostgreSQL, 로컬 WebSocket, 합성 DEMO 응답을 사용합니다. 정상 LONG 포지션 전파 지연·수수료 지연, 부분 체결 뒤 잔량 취소와 단일 reduce-only 종료, SHORT 방향을 확인합니다. 현금·입출금·레버리지·제3조회 불안정·다른 주문/보호·증거 불일치·수수료/실현손익/자산 오류·복수/누락/오래된 체결은 계속 차단합니다. 실패 시 보호 성공으로 처리하지 않고 잠금과 reduce-only 종료를 확인합니다.

세션 오류가 finally 정리 중 관찰 제한시간 취소와 겹치는 재현도 포함합니다. 보호가 확인되고 최종 포지션이 0이더라도 오류가 있었다면 session_error=true, 전체 검증 실패로 남깁니다.

## 변경 범위와 불변 조건

새 helper와 신규 통합 테스트를 추가하고 기존 실행·모델·세션·검증 출력 4개 파일을 수정했습니다. 모델 추가 필드는 기본값을 제공하며 DB 스키마 변경은 없습니다. 이전 로컬 데이터/요청/일별·주별 기준/고점은 재작성하지 않습니다.

원래 coherent_snapshot 파일, 거래소 SDK와 스냅샷 생성, account_risk, 저장소/fencing, run_demo CLI, 정책·설정·Docker/Compose·잠금 파일·export 관련 파일은 부모 ZIP과 바이트 단위로 동일합니다. 최초 체결 검증은 기존 비교에 실패한 경우에만 별도 제한된 경로로 실행하며 제3조회 한 번과 signed 단일 체결을 요구합니다. 일반 전략이나 기존 포지션 변경·복수 체결을 추정해 통과시키지 않습니다. 보수적인 평가액/증거금/청산가와 고점 관측을 유지합니다.

진단은 마지막 불일치의 필드 이름, 허용된 결과 분류, 현재 작업의 추가 검증 여부와 근거 해시만 보존합니다. 원본 응답/서명/키/개인 URL은 저장·출력하지 않습니다.

## 전체 검증 결과

- 전체 pytest: 700 passed in 144.23s (0:02:24), TEST_DATABASE_URL을 실제 로컬 PostgreSQL로 지정.
- Ruff check: All checks passed. Ruff format --check: 221 files already formatted.
- mypy: Success, no issues found in 81 source files.
- 부모 ZIP의 엄격한 대조/위험/저장소/설정 파일 바이트 동일 여부와 새 ZIP의 전 파일 SHA-256/CRC/누락 여부를 패키징 시 검사합니다.
- GitHub main은 64e9e7b3d833824c763a2a0f2df1493300744121 그대로이며 별도 전송 브랜치로 게시합니다.

클라우드에서 인증된 Binance 주문은 실행하지 않았습니다. ZIP 설치 후 실제 계정의 복구 검토 결과를 먼저 확인해야 합니다. 새 자동 진입·SL/TP·종료 성공, Phase4 전체 수락, LIVE/Phase5 승인을 대신하지 않습니다.
