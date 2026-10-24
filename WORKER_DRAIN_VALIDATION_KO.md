# DB 작업 취소 후 스레드 완료 대기 및 기존 청산 확인 검증

전체682개 테스트 통과(134.32초). 새15개 테스트 통과(11.61초), 기존667개를 삭제하거나 완화하지 않았습니다. mypy80개 소스와 전체Ruff lint/format 통과. 실제PostgreSQL 및 localhost WebSocket/합성 거래소를 사용했습니다. 클라우드에서 실제 DEMO 인증·주문·사용자 DB 갱신은 하지 않았습니다.

부모 ZIP: binance-hybrid-ai-futures-windows-runtime-fix.zip
SHA256: 9ca1f039dab9ab87202064a21a9859d2fb5ef9ccc2947fe16eb982e233e44daf

사용자 관측: 작업3f197232-fd91-4201-83d0-e3539ea633cf의0.0007 BTC 진입/SL·TP 확인은 성공했지만, OBSERVATION/session_failure=NONE/session_error=false 이후 PAUSE_ENTRIES에서 DATABASE_ACCESS_FAILED로 종료했습니다. 별도 동일 정리 작업ID d925cbab-7f96-5fda-b5bd-2e7c5002a9b9로 청산을 요청한 뒤, signed 조회에서 거래소 포지션/일반·조건부 미체결0 및 해당 종료 FILLED/0.0007을 확인했습니다. 로컬은 EXIT_PENDING/ACKNOWLEDGED에 남아 있습니다. 실제 최초 SQLSTATE는 당시 출력에 없으므로 특정 코드를 수집했다고 주장하지 않습니다.

재현: 부모 ZIP의 session/validation/CLI 파일을 바이트 그대로 복원한 임시 복사본에서 실제PG 행 잠금을 잡은 event 처리 중 비동기 세션을 취소했습니다. 기존 코드에서 SQLSTATE55P03 행 잠금 시간 초과와 일시정지 실패가 발생했습니다. 전체 validation 경로에서도 사용자의 출력과 동일한 PROTECTED/stop_verified=true/target_verified=true/session_error=false/completion_stage=PAUSE_ENTRIES/completion_failure=DATABASE_ACCESS_FAILED를 재현했습니다(2 failed,10.47초). 수정판에서는 같은 두 테스트가 통과합니다. 이는 해당 경합 경로의 직접 재현이며 실제 사용자 DB의 원래 SQLSTATE를 확정하는 것은 아닙니다.

원인/수정: asyncio.to_thread를 기다리는 작업만 취소해도 실행 중인 동기 스레드는 계속됩니다. 세션 finally의 일시정지·disconnect와 바깥 validation의 정리가 아직 행 잠금을 잡은 worker와 충돌할 수 있었습니다. settled_call은 별도 worker task를 shield하고 취소를 받으면 완료/실패를 먼저 기다린 다음 취소를 다시 전달합니다. 반복 취소도 worker를 남기지 않습니다. worker 예외는 비밀정보를 출력하지 않고 소비하며, 정상 실행의 반환값과 예외는 보존합니다. 세션의 모든 기존 to_thread 경로에 적용했습니다. 동기 session 생성은 원래대로입니다.

시간 제한/안전: 새 주문이나 POST 재전송 경로를 추가하지 않았습니다. 행 잠금2초, lease/fencing, 네트워크 제한, clock/RTT/시세/계정·스트림 신선도·위험 한도는 그대로입니다. 취소 시 스레드를 중간에 죽이거나 row-lock 보호를 해제하고 다음 작업을 강행하지 않습니다. 진행 중인 bounded 동기 작업을 먼저 마치므로 사용자에게 보이는 종료 대기는 작업이 실제로 끝날 때까지 길어질 수 있습니다. 만료된 lease를 되살리지 않습니다.

DB 분류: SQLAlchemy 예외의 SQLSTATE를 허용 목록으로만 해석해 DATABASE_LOCK_TIMEOUT/DEADLOCK/CONNECTION_FAILED/OTHER_DATABASE_FAILURE를 출력합니다. 원래 completion_failure=DATABASE_ACCESS_FAILED와 별도로 completion_database_failure를 추가했습니다. CLI의 DB 오류도 같은 허용 분류만 출력합니다. SQL·파라미터·키·서명·URL·원래 SQLSTATE는 출력하지 않습니다.

기존 종료 확인 utility: --confirm-flat은 명시적 --enable-demo와 ISOLATED BTCUSDT 범위를 요구하고 다른 실행/초기화/시그널/작업ID 옵션과 혼합할 수 없습니다. 초기 대조는 쓰기 비활성 client로 수행해 보호/청산 POST가 나가지 않게 하고, 이후 기존 confirm_exit/confirm_flat의 전체 원래 ID 조회와 평탄 확인 및 필요한 잔여 보호 DELETE만 수행합니다. 진입이나 청산 POST는 없습니다. 포지션이 남으면 UNKNOWN이며 새 청산을 생성하지 않습니다. DB의 종료 상태/대조 감사는 갱신하지만 EMERGENCY/paused를 해제하지 않습니다. 이 명령을 새로운 자동 진입·청산 검증 성공이라고 처리하지 않습니다.

새15개 테스트: 실제PG lock 중 취소 후 정리 순서,7초 진행 중인 event worker 취소 시 전체validation 단일 진입·청산 완료 및 잠금 오류 없음, 반복 취소·worker 성공/실패2종의 완료 대기, 취소 없는 결과/예외 보존, SQLSTATE4종의 안전한 분류, CLI 기존 ACK를 체결로 갱신(새 주문 없음), 포지션이 남은 CLI 확인에서 추가 청산 불가, 명시적 활성화·단일 동작 옵션 강제3종, CLI DB 오류의 허용 분류 및 SQL/키 미노출. 기존 원장·위험·일별/복구·초 정밀도·시세/지연·미확정 보호/주문·부분 체결·중복 방지 검증도 유지했습니다.

변경 범위: demo_stream의 settled_call과 세션 호출 대기, demo_validation의 DB 오류 분류·추가 출력, run_demo의 확인 전용 옵션과 DB 오류 분류, 새 통합 테스트 및 시작/검증 문서. DemoCoordinator/운영·전체snapshot/주문 구성·수량/신선도/시각/위험 규칙, DB 모델·테이블·영속화, 일별 기준·복구·정정·export 서비스, SDK·호스트·allowlist, Dockerfile/Compose/설정/의존성 및 AGENTS/MASTER_SPEC는 부모 ZIP과 동일합니다.

다음 Windows 동작은 새 이미지 적용과 기존 종료 확인입니다. 원래 DB·일별/주별 기준·고점·이전 복구/정정/기준 receipt·export intent를 유지하며 새 진입, 기준 재채택, 잠금 해제, export 재요청이나 LIVE/Phase5 진행을 하지 않습니다. 수정판의 새로운 실제 DEMO 자동 진입·종료 검증은 그 이후 별도입니다. main 변경 없음.
