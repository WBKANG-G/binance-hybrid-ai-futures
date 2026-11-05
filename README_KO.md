# 확인된 과거4건을 보존하는 EMERGENCY 복구

사용자는10월20일 손절/종료의 UNKNOWN·SUBMITTING2건(미전송 증거 없음)과10월31일 손절/목표 NOT_SENT2건(LOCAL_VALIDATION_FAILED 증거 있음)을 확인했다. 이 불확실한 이력을 삭제/해결 처리하지 않고 보존하며 EMERGENCY를 NORMAL/paused로 복구하는 제안에 "다음작업 진행해"로 진행을 지시했다. 이번 단일 복구 범위를 자동 처리한다.

ZIP을 새 폴더에 풀고 RECOVER-KEEP-PAUSED.cmd가 보이는 위치에서 CMD를 열어 실행한다.

```bat
RECOVER-KEEP-PAUSED.cmd
```

기존 Desktop/windows-demo-recovery-unresolved-details/unresolved-review.json을 자동으로 읽는다. 찾지 못하면 이전 unresolved-review.json을 새 폴더의 CMD 옆에 복사하고 실행한다. 원본은 보관한다. 토큰을 복사하거나 키를 다시 입력하지 않는다. API 소스/이미지/설정을 갱신하거나 재시작하지 않는다.

원본 보고서의4건이 승인된 날짜·역할·로컬 증거·종료 상태인지 확인하고, 새 canonical 읽기 전용 심사를 수행해 미해결 집합digest/세부목록/전체요청수가 이전 보고서와 정확히 같을 때만 적용한다. 그 뒤 기존 acknowledge_flat_emergency가 리스/펜스 아래 전체 심사와 토큰 검증을 다시 수행한다. 원래40초/600쿼리/6시계/128이력 bound는 각 심사에서 유지한다. 전체 절차에는 이전 약20초 심사가2회와 마지막 준비 검사가 필요할 수 있다. 창을 유지하고 다른 거래/관찰/복구를 동시에 실행하지 않는다.

DB에는 새 확인 영수증·작업/감사 이력·리스 메타데이터와 NORMAL/paused/재조회필요 상태만 반영한다. 과거 requests/orders/회계/일간·주간 기준금/고점/실패·미해결 상태는 그대로 보존하는지 전체 모델 비교로 확인한다. 미해결 건을 FILLED/해결로 바꾸지 않는다. 거래소에는 GET 조회만 하며 주문/취소/설정변경/export 생성은 없다. 거래 정지와 주문/LIVE 비활성을 유지한다.

고정 복구 작업ID는 e0e27c9a-8616-48bb-aab7-5f061bff908b다. 기존 미해결집합이 달라지거나 최신243598bb 작업의 안전 종료 조건/위험 조건/기준일이 맞지 않으면 중단한다. 성공 후 리더 반환과 읽기 전용 준비 상태를 확인한다. 재실행 상황에서 이미 같은 영수증으로 NORMAL/paused이면 새 적용 없이 준비 확인만 한다. 새 거래를 실행하지 않는다.

입력/보조 코드는 해당 작업 전용 /tmp0700 디렉터리에만 넣고 helper SHA를 import 전에 검증한다. 파일은0600이고 종료 후 정리한다. 키/Secret/토큰은 출력하거나 이 임시 파일에 저장하지 않는다. 원래 승인 보고서에는 review_token이 없으며 새 토큰은 메모리에서만 사용한다. 결과는 flat-recovery-result.json에 저장되고 기존 파일이 있으면 덮어쓰거나 자동 반복하지 않는다.

성공은 EMERGENCY_ACKNOWLEDGED_READY_ENTRIES_STILL_PAUSED 또는 같은 작업의 ALREADY_ACKNOWLEDGED_READY_ENTRIES_STILL_PAUSED다. BLOCKED_KEEP_PAUSED_NO_AUTOMATIC_RETRY가 나오면 application_may_have_completed를 확인하고 출력 그대로 공유한다. 적용 후 준비 검사만 실패했을 수도 있으므로 같은 명령이나 잠금 해제를 반복하지 않는다. 프로그램이 금융 변경을 되돌리거나 과거 이력을 지우지는 않는다.

이 조치는 거래 재개/새 DEMO 주문/LIVE/Phase5/main 병합 또는 Phase4 전체 수락이 아니다. 실제TP/부분체결 등 미완료 범위는 그대로 남는다.
