# 원본 작업ID 오타 수정 — 과거4건 보존/진입 정지 복구

사용자가 공유한 원본에서10월31일 STOP 작업ID는77db044e-1bcd-4358-80e6-df9730630213이다. 이전 도구에77db044e-11cd-...로 옮기는 오타가 있어 OPERATOR_SCOPE 검사가 중단됐다. 해당 결과는 application_may_have_completed=false였고 실제 리스 획득/복구 적용은 시작되지 않았다. leader_released=false는 당시 구현의 기본 출력값이었으며 리스를 보유한다는 증거가 아니었다.

정확한 원본ID만 허용하도록 수정했다. 비교를 느슨하게 하거나 과거4건 상태를 바꾸지 않았다. 실제 원본의 관련 필드를 독립 JSON 회귀 fixture로 추가해 원본1bcd는 통과하고 잘못된11cd는 계속 거부하는 것을 검증했다. fixture는 테스트 전용이며 실제 입력 파일로 자동 선택하지 않는다.

## 실행

이 ZIP은 새 폴더에 풀고 RECOVER-NOV6-KEEP-PAUSED.cmd가 있는 CMD에서 실행한다.

```bat
RECOVER-NOV6-KEEP-PAUSED.cmd
```

사용자가 이미 원본을 복사한 Desktop/windows-demo-nov6-flat-recovery/unresolved-review.json을 자동으로 찾는다. 현재 새 CMD 옆의 unresolved-review.json이 있으면 그 원본을 우선 사용한다. 이전 기본 경로도 보조로 확인한다. 기존 실패 결과 nov6-flat-recovery-result.json은 삭제하거나 덮어쓰지 않는다. 수정본은 별도의 nov6-flat-recovery-fixed-result.json에 결과를 저장하며, 이 파일이 있으면 반복을 차단한다.

복구 작업ID0f72dd2e-9c5d-4567-aa46-9f3772f79ea7은 그대로다. 이전 시도는 적용 전 차단됐으므로 같은 감사 작업으로 이어가며 중복 영수증을 만들지 않는다. 이미 같은 영수증이 있으면 새 적용 없이 읽기 전용 준비 확인만 한다. 새 주문/시험ID를 생성하지 않는다.

## 유지되는 검증

실제 API의 worker 수정9c264588...와 원래 canonical 복구 소스4개를 확인한다. 새 canonical token-bound 상세 심사에서 원본과 동일한 과거4건의 요청 ref/digest/전체 링크·상태를 재검증하고, 전체 요청 수는 원본78에서 이번 종료된 진입/SL/TP/종료4개만 증가한 경우만 허용한다. 최신5bb5814c 작업의 전량0.0007/종료 FILLED/CLOSED/노출0/보호2개 VERIFIED/첫 종료 세대/finally·finish-exit·EVENT/ALGO_UPDATE/DB_LOCK_TIMEOUT 표식과 ready 부재도 확인한다.

기존 acknowledge_flat_emergency가 리스/펜스 아래 전체 서명 심사와 메모리에서 받은 새 token을 다시 확인한다. 각 심사의 기존40초/600서명 조회/6시계/128이력 bound는 유지한다. 기존 요청/주문/금융 평가/기준금/고점/이전 영수증은 그대로 보존하고, 새 확인 영수증·작업·감사·리스 및 NORMAL/paused 상태만 기록한다. UNKNOWN/SUBMITTING/NOT_SENT를 해결/체결로 바꾸지 않는다. API나 컨테이너를 재시작하지 않는다.

성공은 EMERGENCY_ACKNOWLEDGED_READY_ENTRIES_STILL_PAUSED다. 진입 정지/주문·LIVE 비활성 및 원격 빈 계정을 다시 확인하고 리스를 반환한다. 거래 재개나 새 보호 worker 시험은 실행하지 않는다. 이전 실패한5bb 시험도 미수락 기록 그대로다.

오류 시 새 error_code에 어떤 ID/요청 수 검사가 달랐는지 가능한 범위에서 표시한다. 리스를 획득하지 않았다면 lease_acquired=false, leader_released=null로 구분한다. BLOCKED이면 결과를 공유하고 로그를 삭제해 재시도하지 않는다. application_may_have_completed=true이면 DB 적용 후 검사만 실패했을 수 있다. 다른 거래/복구를 동시에 실행하지 않는다.

키/Secret/token은 출력하거나 호스트 임시 파일로 옮기지 않는다. 기존 canonical DB 복구 영수증 감사 기록은 유지한다. 원본·검증한 상세 코드만 작업 전용/tmp0700/파일0600에 올렸다가 정리한다. 사용자 원본과 이전 실패 로그는 보존한다. LIVE/Phase5/Phase4 전체 수락은 포함하지 않는다.
