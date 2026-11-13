# 합성 전송 감사 기록의 금융 DB·독립 체크포인트 결합

합성 전송 계약을 PostgreSQL 체크포인트와 두 비공개 파일에 연결하고, POST 준비 감사 표식을 금융 상태·이벤트·outbox와 **같은 PostgreSQL 트랜잭션**으로 커밋하는 후보 경로를 구현했다. 실제 주문·HTTP·Windows 배포는 수행하지 않았다.

## 변경 내용

`packages/persistence/demo.py`의 DemoStore.change에 트랜잭션 종료 직전 보호된 `_stage_change_audit` 확장 지점을 추가했다. 기본 구현은 아무 작업도 하지 않는다. 기존 리더 fencing·lease·정책 불변 검사·이벤트/outbox·알림 저장 순서는 유지한다. 확장 지점에서 예외가 발생하면 금융 변경 전체가 롤백된다. 이 확장 지점은 신뢰된 내부 코드용이며 네트워크 호출이나 추가 금융 변경을 수행하는 용도가 아니다.

`packages/persistence/demo_wire_checkpoint_candidate.py`의 CoupledDemoStore와 AnchoredSyntheticWireJournal만 명시적으로 이 확장 지점을 사용한다. 기존 DemoCoordinator나 실제 전송기에 연결하지 않는다. 앞 단계의 고정 SyntheticResponder만 허용하며 임의 전송 함수를 받지 않는다.

새 보조 테이블은 다음과 같다. 명시적인 provision(apply=True) 없이는 생성하지 않는다.

- wire_checkpoint_installation_v2: 설치 namespace 식별 UUID.
- wire_checkpoint_bindings_v2: scope·journal UUID·로컬 DEMO 계정·설치 UUID·계약 root 결합.
- wire_checkpoint_events_v2: 원래 합성 계약 행, 이전 체크포인트 해시, 금융 이벤트 FK, 금융 상태 전후 digest와 원래 의도 내용 참조.

세 테이블은 UPDATE/DELETE 거부 트리거를 적용하고 검토 때 트리거 정의·활성 상태를 검사한다. 금융 이벤트 FK와 별도로 DEMO_AUDIT outbox의 존재·내용도 검증한다. 기존 `demo_*` 금융 데이터와 구분한 자원 증거 namespace이며, 향후 설치 시 보조 테이블도 백업·복구·무결성 범위에 포함해야 한다.

## 금융 트랜잭션 결합

POST PREPARE는 현재 lease 소유자·fence·유효 기간·DEMO·paused=true를 요구한다. 원래 SUBMITTING 의도 이벤트의 내용 핀을 확인하고, 실제 전달된 서명 질의에서 캡처한 조건을 비교한다. 진입은 수량·가격·방향·GTC를, 손절은 방향·트리거 가격을 대조한다. 청산은 방향을 대조하고 실제 질의 수량을 보존하며, 과거 진입 수량으로 대체하지 않는다. 이는 원격 포지션이나 실제 주문 권한 검증을 대신하지 않는다.

금융 원장의 operations에 `SYNTHETIC_WIRE_V2:<operation UUID>` 감사 표식을 추가한다. 값은 scope/journal/준비 wire 행 해시다. 금융 상태에서 이 표식 하나를 제거한 내용이 변경 전 상태와 정확히 같아야 한다. 잔액·기준금·주문·요청 상태·리스크 정책·paused 값은 변경하지 않는다.

그 상태, DEMO_SYNTHETIC_WIRE_PREPARED 금융 이벤트, DEMO_AUDIT outbox, PREPARE 체크포인트를 같은 Connection/PG 트랜잭션에 저장한다. 원래 의도 행도 다시 잠그고 내용 핀이 바뀌지 않았는지 확인한다. source 내용 핀은 과거 제출의 진위나 최초 유일성을 소급 인증하지 않는다.

별도 wire-contract-v1.log에는 기존 계약 행을, wire-checkpoint-v2.log에는 금융 연결까지 포함한 체크포인트 해시를 추가하고 각각 fsync한다. 두 파일 저장 후 PG 커밋이 성공해야 다음 DISPATCH_BEGIN 단계로 진행한다. BEGIN의 PG·파일 커밋이 성공한 뒤에만 합성 responder를 호출한다. OBSERVED/UNKNOWN도 별도 PG·파일 체크포인트로 남긴다. READ는 같은 메모리 세션으로 체크포인트를 남기지만 금융 PREPARE 표식을 추가하지 않는다.

writer의 잠금 순서는 금융 계정 행 → 필요한 원래 의도 행 → scope advisory lock → 파일이다. 각 쓰기 전후에 lease·fence·paused 상태를 재검사한다. 생성 시 lease 값을 복사하고 store/engine/lease 구성이 바뀌면 거부한다. scope를 다른 디렉터리에 다시 생성해 과거 이력을 채우거나 writer를 재개할 수 없다.

## 실패·되돌림 검증

PG 내부의 금융 상태·이벤트·outbox·체크포인트는 원자적이다. PG와 두 파일 전체가 하나의 원자적 커밋이라는 보장은 하지 않는다. 파일이 앞선 상태에서 PG 커밋이나 lease 재검사가 실패할 수 있으며, 다음 검토는 이 간격을 거부한다. 파일 부분 쓰기·fsync 오류·기록 실패 이후 writer를 차단하고 자동 재시도·환불·파일 잘라내기·증거 재생성은 하지 않는다.

파일만 과거 prefix로 되돌리면 PG 행과 길이/내용이 달라 거부한다. PG 체크포인트만 되돌려도 두 파일 및 금융 표식과의 불일치로 거부한다. 권한 있는 DB 변경자가 설치 식별과 PG 해시 체인을 전부 재계산해도 별도 파일 증인이 다르면 거부한다. 금융 이벤트 본문·outbox·원래 의도 내용·현재 원장 표식 변경 역시 거부한다. 원장 표식과 체크포인트의 전체 대응 관계를 확인해 감사 hook 누락으로 생긴 고아 표식도 숨기지 않는다.

각 검토는 새 READ ONLY/REPEATABLE READ 트랜잭션을 사용한다. 테스트에서 별도 Python 프로세스가 정상 이력을 읽고, 파일 prefix를 되돌린 경우 차단하는 것까지 확인했다. 검토 성공은 writer 재시작·세션 승계를 허용하지 않는다. 동시 writer와 겹친 읽기 스냅샷이나 lock_timeout은 보수적으로 거부될 수 있다.

원래 wire 계약의 한도는 256개 기록·512 KiB다. 원래 의도·금융 이벤트는 선택한 UUID 한 행만 읽고 1 MiB 이하인지 먼저 확인한다. 전체 수천 개 이벤트 본문을 가져오지 않는다. 현재 금융 원장의 operations 항목은 8,192개 한도를 검사하며, 임의 삭제나 정리 정책은 추가하지 않았다. SQL statement/lock timeout은 각각 2초다.

## 보장과 제한

체크포인트 검토는 PG·wire 파일·독립 증인·금융 기록의 일치를 검사한다. 금융 PREPARE가 있을 때만 financial_prepare_transaction_coupled=true를 반환한다. 합성 응답과 금융 감사 표식을 연결했다는 뜻이며, 실제 주문 전송 성공·청산·요청 결과 확정·이력 보관 권한은 아니다.

전체 DB(금융 상태 포함)와 두 파일을 같은 과거 상태로 동시에 되돌리는 공격은 이 저장소들만으로 독립 증명할 수 없다. whole_system_rollback_protection_verified는 false다. DB와 모든 파일을 함께 수정할 수 있는 실행 환경에 대한 외부 신뢰 앵커도 제공하지 않는다.

원격 계정 신원·원래 과거 제출 진위·실제 네트워크 전송·실행 권한·프로세스 재시작 후 전송 재개는 여전히 검증하지 않는다. 앞 단계의 로컬 자격 증명 세션 UUID는 같은 프로세스 안의 합성 소유자 관계다. 모든 주문·실거래·실행·이력 보관 승인은 false다.

Windows의 기존 네 미해결 요청·34개 주문 이력·금융 기준금·용량 부족을 변경하거나 해결하지 않았다. 설치 패키지를 제공하지 않으며 기존 Windows 명령을 재실행하도록 안내하지 않는다. 실제 적용 전에 저장소 복구·체크포인트 보존 정책, 원격 계정 신원 출처, 금융/리더/호출 한도 admission, 신뢰된 시계와 설치 검토가 필요하다.

검증은 실제 로컬 PG/파일과 합성 키·응답만 사용했다. 커밋 중단, SQL 실패, 부분 파일, lease 만료·fencing, 기록 변경·되돌림, 별도 프로세스 검토와 기존 DemoStore/이전 후보 경로를 검증했다. 결과는 ANCHORED_WIRE_COUPLING_CANDIDATE_VALIDATION.json에 기록한다.
