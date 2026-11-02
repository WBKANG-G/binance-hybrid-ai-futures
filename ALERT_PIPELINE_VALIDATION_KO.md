# TEST 큐→자동 worker→이메일 검증

부모 ZIP은 windows-demo-alert-ready-fix / SHA256 a5d1637dec31fdea19cd0208bb5afaa8a2d310d6ca1d63ed30633178c9b32138이다. 거래 실행/리스크/금융 저장소/DB migration/readiness/Compose/키·SMTP 인증·12시간 설정 보관·claim/재시도 알고리즘은 변경하지 않았다. 변경은 TEST 모델/메일 제목·내용 추가, 전달 로그 ID와 채널 표시 및 별도 등록·조회 도구다.

AlertEnvelope는 DELIVERY_PIPELINE_TEST와 severity TEST의 조합만 허용한다. 실제 긴급 코드를 TEST로 낮추거나 테스트 코드를 CRITICAL로 표시하거나 TEST에 주문 UUID를 넣으면 검증을 거부한다. 원래 위험 전이 selector는 TEST를 자동 생성하지 않는다. 기존 CRITICAL 경고의 기본값과 내용은 유지한다.

등록 도구는 명시적 --enqueue-self-test와 UUID4, 유효한 이메일 RAM 설정을 요구하고 LIVE/활성 로컬 계좌를 거부한다. 거래소 키나 signed query를 사용하지 않는다. 짧은 DB 트랜잭션에서 해당 계좌 행 잠금으로 동일 등록을 직렬화하며 금융 계좌 행 자체를 UPDATE하지 않는다. 기존 감사 event_id는 FK 연결 참조로만 사용한다. 새 금융 사건/감사/outbox를 만들지 않기 때문에 기간/평가 앵커를 오염시키지 않는다.

알림 ID는 새 작업 UUID4와 같고 반복 호출은 원래 행을 유지한다. 현재 source event/code 한 건 제약도 유지한다. 등록 command는 Dispatcher나 SMTP send를 호출하지 않고 DB 상태만 최대60초까지 조회할 수 있다. 완료 조회는 해당 행의 계좌/코드/유형/소스 ID 및 계좌 해시, delivered_at과 마지막 ACK 시도/번호/시각·claim 부재의 일관성을 확인한다. 단순 delivered_at 삽입만으로 성공하지 않는다.

DB의 ACK만으로 Naver SMTP 수락 또는 사람의 수신을 단정하지 않는다. 결과 이름은 QUEUE_DELIVERY_ACKNOWLEDGED로 제한하고 inbox_confirmation은 REQUIRES_USER_CONFIRMATION이다. 자동 worker 로그의 component/channel/alert_id와 실제 메일 ID까지 함께 대조한다.

실제 격리 PG, 원래 worker.main 루프를 실행하는 별도 thread 및 로컬 검증 TLS SMTP로 전체 흐름을 시험했다. fixture는 연결 factory와 수신 설정을 로컬 합성 값으로만 주입했고 Dispatcher/큐/SMTP AUTH·DATA/ACK 저장은 실제 코드를 사용했다. 등록 전후 및 자동 전송 뒤 demo_accounts/demo_events/demo_outbox 행 전체가 동일함을 확인했다. 같은 ID 중복·동시 등록·다른 ID를 통한 같은 source 반복·다른 계좌 재사용·잘못된 맥락·위조 완료/소스·유형 혼합·CLI opt-in 및 메일 미설정을 검사했다.

검증 결과:

- 집중 검증67개 통과(14.72초). 새 큐/CLI19개와 기존 큐/이메일 회귀 검사를 포함한다.
- Ruff 검사 통과, 포맷427개 유지, mypy122개 소스 통과.
- 첫 단일 전체 실행은1362개 통과/2개 실패(416.02초)였다. 기존 host handoff 및 SL fallback 관찰 검사가 실패했다. 조건 변경 없는 개별 재실행에서2개 통과(1.22초), 이전 버전의 같은 두 검사도2개 통과(1.39초)했다.
- 두 번째 단일 전체 실행은1361개 통과/3개 실패(442.68초)였다. 위 두 검사와 private stability 결과 감사 실패 주입 검사가 실패했다. 따라서 단일 전체 실행 통과로 기록하지 않는다.
- 별도 새 PG18 컨테이너의 새 DB를 묶음마다 생성해, 원래 수집 순서의 전체1364개를100개씩(마지막64개)14개 묶음으로 나눠 각1회 실행했다. 누락/중복 없이1364개 모두 통과(459.25초)했다. 기존 실패3개도 포함한다. 테스트 파일·assertion·관찰 timeout·수락 조건을 변경하거나 제외하지 않았다.
- 반복 사용 DB의 시스템 카탈로그에 삭제 잔여 행이 많이 관측됐으나, 앞선 실패의 원인을 확정하지는 못했다. 분리 실행 통과가 단일 전체 실행의 실패를 없애거나 운영 타이밍의 신뢰성을 입증하는 것은 아니다. 단일 프로세스 전체 회귀의 간헐 실패는 후속 조사 대상으로 남긴다.

실제 네이버 직접 self-test c493b5c2는 한국시간2026-11-02 15:25 수신 화면과 SMTP 수락 JSON으로 별도 보존했다. 새 TEST 큐→실제 Windows 자동 worker→네이버 실제 도착은 아직 확인 전이다. 클라우드에서는 실제 사용자 키/메일 인증 정보/외부 수신자 전송을 사용하지 않았다.

테스트 알림은 실제 긴급 사건이 아니며 실제 부분체결 보호/TP 트리거/시장 장애를 수락하지 않는다. SMTP는 재시도 시 중복 수신될 수 있으며 정확히 한 번 전달을 보장하지 않는다. main/LIVE/Phase5 및 Phase4 전체 완료 판정은 변경하지 않는다.
