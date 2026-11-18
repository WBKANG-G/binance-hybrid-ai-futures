# 기간 기준 변경의 원자적 저장 절차 검토안

## 확인된 출발점

사용자 Windows 화면에서 기존 원장·변경 후보의 DemoLedger 검증과 별도 미승인 감사
모델 검증이 모두 성공했다. 감사 초안을 완료 기록으로 해석하는 경로는 거부됐다.
89개 설치 파일, 기존 원장과 요청의 보존, 사본 정상 종료가 보고됐다.
이는 화면 관측이며 사용자 원문 파일을 클라우드에서 직접 읽은 결과는 아니다.

이번 단계는 실제 적용을 준비하는 **합성 트랜잭션 실험**이다. 사용자 원본이나
백업 사본에 적용하지 않는다. 새 Windows 명령 실행도 필요하지 않다.

## 검토할 실제 변경 범위

| 항목 | 저장 전 | 검토 후보 |
|---|---|---|
| 일자(KST) | 2026-11-13 | 2026-11-18 |
| 주 시작일(KST) | 2026-11-09 | 2026-11-16 |
| 일/주 기준 USDT | 4998.74183608 | 4998.74183608 |
| fencing 버전 | 110 | 111 |
| paused | true | true |
| reconciled / stream_ready | 기존 값 | false / false |
| 감사 기록 | 기존 기록 | 기존 기록 보존 + 별도의 새 완료 기록(설계 대상) |

고점 5000, 누적 평가잔액 차이 −1.25816392, 나머지 평가 구성요소, 평가 시각,
34개 주문, 전체 요청 및 미확정 4건은 그대로 보존한다. 기존 감사 초안은 DB에
저장된 적이 없다. 이를 완료 기록으로 변경하거나 감사 이력을 덮어쓰지 않는다.
실제 적용용 완료 기록의 형식과 원장 연결은 아직 구현하지 않았다.

완료 기록의 검토용 필드 계약은 다음과 같다. 이는 스키마 구현이나 실제 완료 기록이 아니다.

| 필드 | 제안 의미 |
|---|---|
| policy | PAUSED_SAVED_WALLET_PERIOD_ADOPTION_V1 (새 검토안 이름) |
| operation_id / event_id | 같은 불변 UUID로 재실행과 이벤트를 연결 |
| source_kind | SAVED_WALLET_CANDIDATE_WITH_EXPLICIT_OPERATOR_ACCEPTANCE |
| source_acceptance_receipt_sha256 | 금액·기간·한계를 수락한 별도 기록의 지문. 현재 기록 없음 |
| prior_review_id / evidence_sha256 | 검토 초안과 저장 증거 묶음의 지문 |
| before_state_sha256 | 잠근 행의 PostgreSQL JSONB text 지문 |
| candidate_financial_state_sha256 | 새 완료 감사 기록을 넣기 전 후보 전체 지문 |
| expected_fence / committed_fence | 검토한 버전과 실제 저장 버전 |
| old_fields / committed_fields | 이전/이후 일·주 키와 기준금액, 정합성/스트림 상태 |
| committed_at_utc | 트랜잭션 내부 UTC 기록 시각. 외부 성공 응답은 COMMIT 성공 후에만 허용 |
| applied / source_accepted | 실제 수락과 커밋이 성립하는 새 절차에서만 true 사용 |
| execution_approved / midnight_equity_independently_verified | false 유지 |
| account_wide_history_verified / initial_funding_verified | false 유지 |

새 기록은 미승인 초안의 policy를 바꾸는 방식으로 만들지 않는다. 기존 완료 형식에
강제로 끼워 넣지도 않는다. 향후 전용 모델이 원장의 operations 문자열과 이벤트를
동시에 검증해야 한다. applied=true 기록은 트랜잭션 안에서 작성될 수 있지만 커밋 실패면
함께 취소되어야 하며, 파일/화면 성공 영수증을 커밋 전에 발행해서는 안 된다.

기준금액 후보는 저장된 지갑 관측값이다. 정확한 자정 평가액이나 계정 전체 과거
체결·최초 자금의 독립 증명으로 부르지 않는다. 사용자에게 받은 거래 활동 진술을
새로운 출처 수락으로 확대하지 않는다. 날짜가 바뀌거나 원장 버전/내용이 달라지면
이 표는 그대로 적용할 수 없으며 새로운 검토가 필요하다.

## 기존 저장 함수를 그대로 사용하지 않는 이유

`DemoStore.acquire()`는 별도 트랜잭션에서 fence 증가, 모델 직렬화, 리더 이벤트와
outbox 저장을 먼저 완료한다. `DemoStore.change()`는 이후 별도 트랜잭션에서 원장을
다시 모델 직렬화한다. 따라서 이를 조합하면 검토한 원문 JSONB 그대로의 단일
110→111 변경이 되지 않는다. 기존 함수를 수정하거나 7일 제한을 해제하지 않는다.

향후 전용 절차는 원본 서비스와 모든 소비자의 정지를 다시 확인한 뒤 한 DB 트랜잭션에서
계정 행을 잠그고, writer owner/lease, 원문 JSONB 지문, fence를 검사해야 한다.
그 안에서 허용 필드와 새 감사 기록만 구성하고, 조건부 계정 갱신·이벤트·DEMO_AUDIT
outbox를 함께 저장해야 한다. 커밋 실패 시 어느 한 부분도 남아서는 안 된다.
owner가 남아 있거나 lease가 유효하면 자동으로 빼앗지 않고 중단한다.

이벤트와 outbox는 감사용이며 주문 실행 작업을 만들지 않는다. API/키 세션/알림
소비자 시작도 포함하지 않는다. 실제 원장 전체를 이벤트에 넣는다면 기존 DB의
비공개 보관 경계를 유지하고 화면·공개 파일에는 출력하지 않아야 한다.

## 이번에 실행하는 실험

`tools/windows_demo_atomic_rehearsal/transaction.sql`은 `pg_temp` 테이블과 함수만
만든다. 합성 계정명은 `DEMO-SYNTHETIC-PERIOD-TRANSACTION`으로 고정하고, 입력
원장도 테스트용 합성 데이터다. 영구 demo_accounts/events/outbox를 갱신할 경로가 없다.
원장·이벤트·outbox 세 부분 저장, 오류 시 전체 취소, 같은 operation의 중복 방지,
변경된 원장/버전/증거/활성 writer의 거부를 실제 PostgreSQL에서 확인한다.

재실행은 operation 키만 확인해서 성공 처리하지 않는다. 기록된 전체 입력, 최종
원장과 버전, 이벤트 payload, outbox topic까지 일치해야 기존 성공으로 인정한다.
감사 기록에는 원문 JSONB의 PostgreSQL text 지문을 쓴다. Python 정규화 JSON 지문과
같은 값으로 취급하지 않는다. 자기 자신의 해시를 포함시키는 순환을 피하기 위해
감사 추가 전 후보 지문을 기록하고 최종 이벤트에는 완성 원장을 저장한다.

실험 감사의 policy는 `SIMULATED_PERIOD_TRANSACTION_V1`이다.
simulation_only=true, applied=false, source_accepted=false, execution_approved=false다.
실험 성공이 실제 원장 채택 성공으로 읽힐 수 없도록 별도 종류를 사용한다.
이 SQL은 배포용 migration이나 실제 적용 도구가 아니다.

## 실제 적용 전 남은 작업

1. 저장 지갑 후보의 한계를 명시한 출처 수락 문구와 별도 완료 감사 모델을 검토한다.
   기존 AttestedPeriodBaseline의 증거 조건을 충족했다고 표시하지 않는다.
2. 그 형식을 DemoLedger와 연결하고 실제 백업에서 만든 폐기 가능한 사본에서
   원문 보존·완료 감사·트랜잭션·반복 실행 검증을 수행한다. 이번 임시 테이블 실험은
   실제 스키마, 트리거, 제약, 동시 세션 경합 또는 Windows 전체 절차의 검증이 아니다.
3. 신선한 계정 관측, 현재 원본 지문/버전, 이미지·마운트·writer 정지 상태를 다시
   묶어 실제 적용 직전의 검토 결과를 만든다. 변경되면 자동 재시도하지 않는다.
4. 그 구체적 결과에 대한 수락 후에만 원본 적용을 다룬다. 현재 출처 수락, 적용 토큰,
   실제 commit payload 검증, 원본 적용, 거래 재개 승인은 모두 없다.

이번 검토는 MASTER_SPEC의 손실 계산/KST 경계와 기존 보호 조건을 바꾸지 않는다.
기간 변경이 완료되더라도 과거 요청 해소나 거래 재개 조건 충족을 의미하지 않는다.
