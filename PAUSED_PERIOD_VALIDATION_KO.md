# NORMAL/paused 두 기간 미리보기 검증

부모는 windows-demo-alert-pipeline-check ZIP / SHA256 6e48b22ba23bead2cc90834f7bc25d32c640a0c58090a5ce9e9ef911201a6231이다.

기존 파일 변경은 services/execution/demo_period_review.py 하나다. 기존 public review_periods는 EMERGENCY를 고정해 공통 내부 검사를 호출하며 원래 apply_periods도 그 public 경로를 사용한다. 공통 검사 본문은 요구 위험 상태 비교만 인자로 분리했다. 기존 조건/수락값/토큰/receipt/적용 코드는 바꾸지 않았다. NORMAL을 EMERGENCY로 복사하거나 위험 잠금을 초기화하지 않는다. 새 NORMAL 전용 호출부는 NORMAL/paused/읽기 전용 클라이언트와 명시적 활동 확인을 요구한다.

새 CLI는 후보 조회만 지원하며 리더 획득/반납 또는 store.change를 호출하지 않는다. 기존 장부를 조회하고 서명 검토 후 장부 동일성을 재확인한다. 새 미리보기의 토큰은 기존 위험 상태를 포함한 장부에 묶여 있으므로 EMERGENCY 적용 우회에 쓰지 않는다. 새 적용 함수/플래그는 없다. 금융 상태·주문·리스크 한도·모델/migration·Compose·API readiness·키/메일 설정·export 저장소·MASTER_SPEC는 부모 그대로다.

새 검증은 실제 격리 PostgreSQL 및 합성 서명 응답으로11월1일 앵커→11월2일 월요일의 일간·주간 동시 경계를 확인했다. 금융 accounts/events/outbox 전체 행과 account의 owner/fence/lease까지 동일함을 비교했고 SQL이 SELECT뿐인지와 원격 요청이 GET뿐인지 확인했다. 이전 public EMERGENCY 경로가 NORMAL을 여전히 거부하고, 새 경로는 모든 비NORMAL 위험 상태/비일시정지/쓰기 클라이언트/확인 없음부터 거부한다. 서명 체결·소득(0값 포함)·잔고 불일치·기존 손실 한도·앵커/평가 시각·기간 불일치·시간 예산 초과를 차단했다. 새 CLI가 lease를 획득하지 않는지, 적용 플래그와 LIVE/확인 없음이 키/DB보다 먼저 거부되는지도 확인했다.

신규 및 기존 기간/일간 회귀103개 통과(46.01초). 수정된 테스트의 가독성 정리 후 시간 예산 검사와 기존 예산/HTTPS 회귀: 29개 통과(2.81초). Ruff 검사/포맷434개, mypy124개 소스 통과. 패키징 시 이전 검사 본문과 새 내부 본문의 AST가 위험 상태 인자 치환 외 동일한지 검증한다.

이번 변경에서 전체 회귀1364개를 다시 실행하지는 않았다. 부모 당시 전체 분리 실행 통과/단일 전체 실행 실패 기록은 ALERT_PIPELINE_VALIDATION_KO.md를 보존한다. 위 숫자는 실제 실행한 관련 검사 결과이며 새로운 단일 전체 실행 성공으로 확대하지 않는다. 사용자 키나 외부 거래소/메일 전송은 클라우드에서 사용하지 않았다.

실제 Windows에서 signed 후보 조회는 아직 확인 전이고, 기준금 적용/자동 재개/잠금 해제는 이번 범위 밖이다. 이 미리보기 결과로 실제 부분체결 보호·TP 체결·보호 중 장애·Phase4 전체 완료/LIVE/Phase5를 수락하지 않는다.
