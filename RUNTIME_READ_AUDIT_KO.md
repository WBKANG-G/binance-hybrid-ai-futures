# 조회 이력이 있는 공유 저널의 감사 검증

이전 전송 계층은 각 GET의 비용을 기존 공유 저널에 예약했지만, 저널에는 요청 경로가 없었다. 따라서 사용량이 있다는 이유만으로 정상 조회였다고 판단할 수 없었고, 기존 설치 도구는 계속 미사용 저널만 허용해야 했다.

이번 변경은 `ReadAuditStore`와 `AuditedDemoReadJournal`을 추가한다. 기존의 빈 저널 검증을 완화하거나 이미 사용된 출처 불명 저널을 채택하지 않는다. 미사용 저널을 명시적으로 등록한 시점부터 요청 경로·모델 비용·토큰·예약/완료 결과와 상태 해시를 append-only PostgreSQL 감사 이력으로 연결한다. 기존 `AccountedDemoReadTransport`에 감사 제공자를 넘기면 이 경로를 사용한다.

## 기록과 검증

보조 테이블 2개와 UPDATE/DELETE 거부 트리거는 `provision(apply=True)`를 통해 명시적으로만 생성한다. 자동 생성이나 기존 테이블 덮어쓰기는 하지 않는다. `bind_empty(..., apply=True)`는 현재 독립 체크포인트와 일치하며 누적 비용과 요청 행이 모두 0인 저널만 최초 등록한다. 등록 원본 상태, 체크포인트 revision/digest, PostgreSQL 설치 식별자를 연결한다. 기존 감사 등록이 있는 같은 저널은 검증만 하며 새 기준으로 덮어쓰지 않는다.

예약·완료·확실한 미전송·공유 차단을 기록할 때는 SQLite 상태 변경과 기존 PostgreSQL 체크포인트 추가에 대응하는 감사 이벤트를 **같은 PostgreSQL 트랜잭션**에 쓴다. SQLite가 먼저 커밋되므로 뒤따른 PostgreSQL 커밋이 실패하면 다음 접근을 차단한다. 감사 행 삽입이 실패하면 SQLite 변경도 커밋 전에 롤백된다. 전송 전에 예약 기록이 남고, 응답은 완료 기록과 체크포인트가 커밋된 뒤 반환된다. 네트워크 동안 PostgreSQL 범위 잠금을 보유하지 않는다.

`review()`는 등록 상태에서 이벤트 전체를 순서대로 검증한다. 이벤트 해시·이전 해시·토큰별 원래 경로/비용·각 revision의 독립 체크포인트와 SQLite 상태가 모두 일치해야 한다. 모델 비용 축소, pending 요청 삭제, 부팅/기준 시각/정책/식별자 변경, 잘못된 전이, 기록 없는 기존 제공자의 쓰기는 거부한다. 완료된 윈도 행이 기존 정책에 따라 만료·정리돼도 그 전 상태는 감사 이력에 남는다. 대기 중 요청은 시간이 지나도 완료된 것으로 만들지 않는다.

결과는 `AUDITED_READ_ACTIVITY_VERIFIED_KEEP_PAUSED`다. `local_read_activity_complete`는 대기 중 요청이 없다는 뜻이며 금융 증거나 거래 승인과 다르다. `successful_reads`, `failed_response_reads`, `known_unsent_requests`, `pending_requests`를 구분한다. 원본 HTTP 본문, URL/쿼리/서명, 키나 임의 예외 원문은 감사 테이블에 저장하지 않는다. 계정 주문 사용량 검증, 실제 IP 용량 예약, 금융 증거와 실행/주문/실거래 승인은 여전히 false다. 전송 계층의 `read_audit_attached`는 연결 여부만 나타내며 전체 이력 검증 결과는 `review()`에서 얻는다.

## 보존과 제한

재개방과 `view()`는 읽기 전용으로 검증하여 불필요한 시계 쓰기나 감사 이벤트를 만들지 않는다. 기존 계정 원장·리스·청산/실패 trial·기간/저장소 영수증과 이전 체크포인트를 바꾸지 않는다. 주문 writer session을 시작하지 않으며 주문·취소·listen-key 변경 요청 차단도 유지한다.

개발 단계의 감사 범위는 한 scope당 최대 1,024개 이벤트, 활성 상태 스냅샷 최대 256개 요청 행/64KiB다. 새 예약은 기존 pending 요청의 완료 기록 공간까지 남아 있을 때만 허용한다. 한도에 닿으면 새 요청을 거부하며 감사 기록을 자동 삭제·압축하거나 새 원본으로 초기화하지 않는다. 긴 운영 기간의 아카이브/세대 전환 정책은 포함하지 않는다.

이것은 새 개발 브랜치의 코드다. 사용자 Windows PC에 테이블을 만들거나 실제 조회를 실행하지 않았다. PC 배포에는 새 모듈의 소스 검증·명시적 보조 테이블 등록·현재 금융/저장소 검증 도구와의 연결이 필요하다. 사용 이력이 있는 저널의 부팅/시계 변경 복구는 [후속 구현](RUNTIME_READ_RECOVERY_KO.md)에 추가했다. 운영 배포 연결은 별도이며 실제 주문 차단은 유지한다. 기존 설치/기간 스크립트의 미사용 전제를 임의로 제거하지 않는다.

## 검증

격리 PostgreSQL·실제 SQLite·합성 HTTP에서 새 테스트 36개가 통과했다. 같은 인스턴스의 동시 reader와 독립 프로세스 2개의 예약/완료 연결, 원장 불변, 읽기 전용 재개방, 기록 없는 사용 거부, 체인/전이 조작, 보조 테이블 append-only, 감사/체크포인트 커밋 장애, pending 보존, 기록 공간의 완료 여유, 부팅 변경 거부, 미전송과 성공 응답 구분을 확인했다. 기존 조회 전송·체크포인트·요청/비용 계약 등을 포함한 관련 회귀 260개가 통과했으며(신규 36개 포함), Ruff와 변경 모듈 strict mypy 검사도 통과했다.

```sh
PYTHONPATH="$PWD" TEST_DATABASE_URL=postgresql+psycopg://dev:dev@127.0.0.1:55435/hybrid pytest -q tests/integration/test_demo_read_audit.py
mypy packages/persistence/demo_read_audit.py services/execution/demo_runtime_read_transport.py --follow-imports=silent
```
