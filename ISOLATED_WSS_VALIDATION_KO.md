# 독립 WSS 검사 실행 파일 검증

현재 검증 이미지의 기존 Counts/check_scope를 재사용하는 별도 stdin 실행 파일이다. 앱/기존 검사/Compose/의존성/DB 모델·migration·기간 기준금·키/메일·원래 시각 수락/버퍼 상한을 변경하지 않는다. 선택한 검사5개 경로와 원래 수신기/시각/stream/가드 원본 등9개 파일 SHA256을 확인한다. 버전이 다르면 DB 검사 실행 전에 차단한다.

원래45개 검사: DEMO 누적 체결의 중복/과거 이벤트1개, EventInbox 신선도/역순/coalescing/잘못된 프레임/overflow/idle, 이벤트 시각 검증, 실제 로컬 WS와 PG의 느린 callback/REST 수신, 실패한 수신기와 DB worker 경합/취소 전파를 포함한다. 합성 데이터에 근거하며 실제 Binance 원문 캡처 재생이 아니다. 과거 실제 원문 저장은 기본 구현에 없으므로 감사 ledger payload를 원문으로 포장하지 않는다.

최종 기존 이미지+동일 전용 Compose 실행: 45개 통과(5.25초), collected/passed45, failed/skipped0, checked_source_files9, ISOLATED_SYNTHETIC_WSS_CHECK_PASSED. 소스 식별 추가 전 첫 실행은45개 통과(5.47초)였고, 식별 검사 추가 후 원래45개를 다시 실행해 확인했다. 실패/skip이 있거나 collected/passed가45가 아니면 수락하지 않는다. 원래 테스트/타임아웃을 변경하거나 제외하지 않았다.

Ruff/포맷 및 strict mypy 진단 소스1개 통과. 잘못된 외부 실행 컨텍스트는 DB 검사 실행 전에 고정 BLOCKED 응답과 종료2를 반환했다. 부모 이미지의 원래 범위 가드7개 검증도 보존한다. 전체 앱 회귀는 앱 변경이 없으므로 다시 실행하지 않았다. 테스트는 기존 이미지의 동일한 내부 네트워크·host/volume/key 마운트 없음·전용 tmpfs DB 구조를 재사용한다.

사용자 Windows45개 실행 결과는 아직 확인 전이다. 앞서 실제 Windows13개와 정리 성공은 별도 증거 문서에 기록했다. 클라우드에서 사용자 키·외부 거래소/메일을 사용하지 않았다. 실제 거래 수락·원문 재생·Phase4 전체 완료/main/LIVE/Phase5로 확대하지 않는다.
