# 기존 종료 확인 지연 수정 검증

전체1206개 테스트 통과(305.82초), 집중25개 통과(9.56초). Ruff check/format 및 mypy108개 소스 통과. 기존1195개 시험을 변경하지 않았습니다.

## 확인한 문제와 제한

실제 Windows 응답 유실 시험에서 동일 ID 조회 복구와 중복 진입 방지는 확인됐지만 종료가 ACKNOWLEDGED인 상태에서 FLAT_NOT_CONFIRMED 및 DEMO_SESSION_OR_QUERY_FAILED로 NOT_ACCEPTED였습니다. 별도 confirm-flat도 일반 문구로 차단됐습니다. 이후 signed 포지션 조회에 BTCUSDT가 없고 장부가 CLOSED/exit FILLED/open_quantity0인 것을 확인했습니다. 전체 복구 검토60건/264GET/활성주문0/리스크PASS를 별도로 통과하고 명시적 승인25cd5a54를 적용해 현재 NORMAL/paused입니다. 최초 NOT_ACCEPTED는 그대로 보존합니다.

Windows 기록의 DEMO_FLAT_CONFIRMATION_QUERY부터 리더 반환까지60.228초가 걸렸습니다. 집계 출력만으로 당시의 정확한 예외는 확정할 수 없습니다. 다만 원래 확인 함수는 과거 주문까지 포함한 snapshot을 첫 조회2회와 최종 검증3회에 사용하며, 원래 전송은 요청마다 HTTPS 연결을 새로 만들고 함수 안에서는 시계를 갱신하지 않습니다. 시계·리스60초를 소모할 수 있는 구조를 재현했습니다.

이전 ZIP의 정확한 confirm_flat 함수를 대조 시험에 그대로 넣고 실제 로컬 검증 TLS/PostgreSQL에 연결마다0.8초/요청마다0.001초를 결정론적 시계로 반영했습니다. 이전 함수는60.075초에 Clock stale or cooldown active로 중단했습니다. 이 시간은 시험의 합성 지연이며 사용자망 측정이 아닙니다. 같은 전체 증거를 읽는 수정본은 연결1개를 재사용해 VERIFIED_FLAT를 완료합니다. 느린 개별 응답 조건에서는 새40초 예산이 기존60초 리스보다 먼저 차단하고 실패한 fenced 효과는 롤백됩니다. 앞선 효과에서 성공적으로 기록한 조회 증거까지 명령 전체가 롤백되는 구조는 아니며, 원래 함수도 동일합니다.

## 변경

DemoCoordinator의 confirm_flat을 제한된 클라이언트 문맥으로 감싸고, 원래 본문은 _confirm_flat_checked로 이동했습니다. 단계 라벨 대입을 제외한 본문의 AST가 원본과 같고 다른 coordinator 메서드는 모두 동일합니다. 실행/보호/청산/진입 게이트/손익/기준금·주문 상태 판정은 변경하지 않았습니다.

별도 ConfirmationClient가 원래 SDK/사용자 지정 클라이언트의 request로 위임하므로 기존 transport 테스트와 별도 진입 제한을 유지합니다. 기본 SDK 전송을 쓸 때만 GET에 기존 검증된 RecoveryTransport를 사용합니다. 고정 DEMO 호스트, 허용된 GET 경로, TLS/프록시/리다이렉트 거부는 그대로입니다. 원래 cleanup이 flat 확인 뒤 요청하는 DELETE /algoOrder와 단일 clientAlgoId만 허용하고 원래 전송으로 보냅니다. 진입/청산 POST, 다른 DELETE/PUT, exports 생성은 차단합니다. 문맥 종료/오류 뒤 원래 클라이언트·전송을 복원하고 연결을 닫습니다.

한 번의 확인 전체에 기존 RecoveryBudget의40초/서명 요청600회/시계6회 한도를 적용합니다. DELETE도 서명 요청 예산에 포함됩니다.20초 이상 지난 시계는 원래 SDK 검증을 거쳐 동기화합니다. 시계 수명60초·리스60초·post-effect 펜스·잠금·타임아웃은 늘리지 않습니다. 중간 리스 갱신이나 HTTP 실패 재시도는 없습니다. 기존 confirm_exit의 최대4회 관찰은 그대로이며 신규 종료 POST는 없습니다.

전체 과거 **주문에 연결된 ID**의 원래 조회를 유지합니다. flat 확인은 본래 모든 독립 request를 감사하는 복구 검토와 다릅니다. 주문에 연결되지 않은 과거 모호한 요청도 지우지 않고 그대로 보존하며, 별도 review_demo_recovery의 독립 전체 감사는 변경하지 않습니다.

CLI confirm-flat 결과/실패에는 단계와 숫자/분류 진단을 추가합니다. 원시 예외/키/서명/URL을 출력하지 않습니다. 예산 오류는 허용된 고정 코드만 출력하며 SQL 오류도 기존 분류에 단계와 진단을 더합니다. 다른 CLI 흐름의 일반 오류 처리는 유지합니다.

## 시험 범위

원래 flat 확인14개 시험을 그대로 유지합니다. 새 시험에서 정확한 부모 함수의 시계 만료, 같은 조건의 전체 조회/연결 재사용 성공,40초 차단과 해당 트랜잭션 롤백/리스크·이력·기준금 보존,20초 경과 시 verified 시계 갱신, POST/다른 DELETE/PUT/exports 거부, readonly 보호 DELETE 거부, 사용자 지정 전송 보존, GET 풀과 원래 DELETE 전송 분리 및 오류 복원을 확인합니다.

실제 격리 PostgreSQL/로컬 TLS/WSS와 합성 SDK로 검사했으며 클라우드 인증 거래소 요청/주문은 없습니다. 새 Windows confirm-flat 통과는 아직 확인 전입니다. 이번 변경은 앞선 응답 유실 시험이나 SL 실패/부분체결 시험을 통과로 바꾸지 않습니다. main/LIVE/Phase5 및 Phase4 전체 완료 판정 변경은 없습니다.
