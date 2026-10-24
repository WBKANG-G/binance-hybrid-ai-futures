# DEMO 운영 대조·준비 조회 지연 수정 검증

전체667개 테스트 통과(116.24초). 새17개를 포함하며 이전650개 테스트를 삭제하거나 완화하지 않았습니다. mypy80개 소스, 전체Ruff lint/format 통과. 실제PostgreSQL, 합성 거래소와 localhost WS를 사용했습니다. 클라우드에서 실제 DEMO 인증/주문/사용자 DB 갱신은 하지 않았습니다.

부모 ZIP: binance-hybrid-ai-futures-windows-income-time-fix.zip
SHA256: fd012f83e272b0d658cd4d495c25432d900d6e0d1e54207a521c966ca07b8367

사용자 기록: 작업3f197232-fd91-4201-83d0-e3539ea633cf는 NOT_CREATED/entry_filled=false/session_error=true, PAUSE_ENTRIES에서 DEMO_QUERY_OR_CLEANUP_FAILED로 종료했습니다. 추가 로컬 읽기에서 order_created=false, NORMAL/paused=true, OTHER_DEMO_FAILURE였습니다. UTC02:10:25 진입 재개 후 13초 뒤 stream untrusted가 됐고, 그 뒤 여러 과거 CLOSED 주문에 대한 VERIFIED_FLAT_CLEANUP이 각각 약3초 간격으로 반복됐습니다. 최종 조회도 실패했습니다. 기존 출력은 정확한 최초 예외를 보존하지 않아 그 예외의 원문을 복원했다고 주장하지 않습니다.

확인한 지연 경로: snapshot이 매번 모든 과거 진입/종료/SL/TP ID를 개별 조회했고, 완료된 CLOSED 주문마다 정리 과정에서 다시 전체 조회했습니다. prepare의 readonly preview는 시세 수집 뒤 최종 계정 확인 중 시세가5초보다 오래되면 차단하는 것이 올바른데, 불필요한 역사 조회가 그 시간을 넘길 수 있습니다. 스트림의 on_ready 준비 동안에는 정기 시각/lease 갱신 루프가 시작되기 전이라는 점도 보완했습니다. 제한 자체는 늘리지 않았습니다.

재현: 부모 ZIP의 실행 파일5개를 바이트 그대로 복원한 별도 임시 복사본에서 완료 기록4개와 GET당350ms 합성 지연을 넣었습니다. 이전 prepare는 Preview quotes expired during final account verification으로 실패했습니다(1 failed,1.06초). 수정판의 동일 테스트는 quote/계정 제약을 유지하며 통과합니다. 이는 재현된 지연 경로의 증거이며 사용자 최초 예외가 반드시 이것 하나였다고 단정하지 않습니다.

운영 스냅샷: 기본 snapshot은 include_closed_history=true로 유지합니다. 새 operational_snapshot은 현재 전체 포지션, 계정/마진/레버리지/단일 자산 설정, 일반·조건부 미체결 및 income을 그대로 조회합니다. 개별 역사 조회 생략은 로컬 원장이 TERMINAL+CLOSED, 잔여/노출/실제위험/보호0이고 (양의 체결+FILLED 종료 또는 미체결+exit_id 없음/NONE), 관련 ID가 현재 두 미체결 목록에도 없을 때만 합니다. 부분 체결, UNKNOWN/SUBMITTING, 보호 중, 잔여, 미확정 종료, 원격 재등장 ID는 생략하지 않습니다. 알 수 없는 원격 포지션도 계속 감지합니다. 원장 주문·요청을 삭제하거나 상태를 임의 성공 처리하지 않습니다.

운영 coordinator와 readonly preview는 이 현재 상태 투영을 사용합니다. 복구/일별 기준/체결 근거 서비스는 기본 전체역사 snapshot을 그대로 사용하며, 최종 confirm_flat도 기본 전체역사 조회로 알 수 없는 과거 보호 상태를 놓치지 않습니다. 운영·역사 투영의 fingerprint를 서로 섞어 성공으로 판단하지 않으며 각 단계는 같은 투영으로 반복 대조합니다.

반복 정리 제거: 최신 대조 스냅샷에 해당 CLOSED 주문의 NEW 보호가 실제 존재할 때만 후속 _cleanup을 수행합니다. 포지션0과 종료체결 근거의 기존 선행 조건 및 취소 후 전체역사 확인은 유지합니다. 매번 이미 정리된 모든 과거 CLOSED 주문에 대해 추가 전체 대조를 하지 않습니다.

시각/잠금: 대조의 lease/fence 검사를 통과한 query callback 안에서 먼저 sync_clock을 수행합니다. session on_ready 전 같은 lease 갱신·시각 동기화를 하고, 준비/최종 정리·청산 확인 단계에도 시각을 동기화합니다. DemoClient의60초 서명 시각/RTT·오차/cooldown 가드, lease60초/만료/fencing, 시세5초 및 신호10초·정책 계정/스트림 나이 제한은 동일합니다. 만료된 lease로 대조하면 전송 전에 차단되는 기존 테스트도 그대로 통과합니다. POST 재시도나 시간 제한 확대는 없습니다.

실패 분류: validation_stage는 SESSION_CONNECT/RESUME_ENTRIES/PREPARE_ENTRY/RESERVE_ENTRY/SUBMIT_ENTRY/OBSERVATION 중 중단 단계를 표시합니다. session_failure에는 QUOTE_EXPIRED_DURING_ACCOUNT_CHECK/CLOCK_STALE_OR_COOLDOWN/DEMO_RATE_LIMITED/LEASE_OR_RISK_GATE_REJECTED/DB 또는 일반 DEMO 실패 같은 허용 분류만 표시합니다. 예외 원문, 키/서명/WS URL은 출력하지 않습니다. 기존 completion_stage/completion_failure도 유지합니다.

새17개 검증: GET당350ms+과거4건의 준비 시세 신선도 통과, 기본 전체역사 조회의 모든 원래 ID 유지, 현재 미체결 재등장/UNKNOWN 의도 조회 유지, 완료 조건7종을 벗어나면 조회 생략 금지, stale clock 직접 요청 차단과 대조 시 안전한 재동기화, 0체결 취소 주문의 UNKNOWN 종료 조회 보존, 소유하지 않은 포지션 감지, 검증된 평탄 상태에서만 잔여 보호 취소, GET당250ms+과거4건으로 실제PG/로컬WS 단일 진입·단일 청산 완료, 비밀정보 포함 예외의 안전한 분류, 준비 실패 단계/분류 및 새 주문 없음. 기존 시세5초 초과, 잠금 만료, 미확정 보호, 추가 주문 방지, 실패 후 잠금, 부분체결·기준·복구·수수료 정밀도 테스트도 그대로 통과했습니다.

변경 범위: demo_snapshot의 운영 투영 옵션/래퍼, coordinator 운영 조회/정리 조건과 시각 동기화, preview의 스냅샷 import만, private session의 갱신 지점, validation의 시각 동기화/단계·허용 분류 출력, 새 통합 테스트와 두 문서. preview 본문의 계산/5초 제한은 바이트 동일합니다. DemoClient/전송 allowlist/실거래 차단, reserve/submit/size/보호·청산 주문 구성, 위험 정책/account_risk, DB 모델·테이블·영속화, 기존 일별/복구/정정/export 구현, Dockerfile/Compose/설정/의존성 및 AGENTS/MASTER_SPEC는 부모와 동일합니다. coordinator 메서드가 사용하는 스냅샷 투영은 위와 같이 바뀌지만 기존 진입·수량·위험 검사 본문은 바꾸지 않았습니다.

다음 단계: 기존 DB와 기준24일/주별 기준/고점/복구 기록 및 일시정지를 유지한 채 새 이미지 적용 후 동일 작업ID의 DEMO 최소수량 검증을 수행합니다. 설치 자체는 신규 주문을 내지 않지만 안내의 두 번째 명령은 명시적으로 DEMO LONG LIMIT 주문을 냅니다. 이전 실패에는 해당 주문이 생성되지 않았으므로 같은ID로 이어갑니다. 실제 현재 위험/설정/시세/연결 조건이 맞지 않으면 계속 차단합니다. 수정판 자동 종료의 실제 DEMO 성공 실적은 Windows 결과 전에는 주장하지 않습니다. main/실거래/Phase5 변경 없음.
