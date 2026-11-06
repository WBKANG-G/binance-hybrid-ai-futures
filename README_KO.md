#11월6일 수정 적용 후 EMERGENCY 복구 — 진입 정지 유지

Windows 수정397228b 적용 후 POSTCHECKED_KEEP_PAUSED/remote_empty_verified=true, 미해결4건, EMERGENCY/paused가 확인됐다. 새 주문이나 복구는 아직 하지 않았다. 사용자가 이전에 확인·승인한 과거4건과 동일한 요청만 다시 확인해 NORMAL/paused로 복구한다. 불명확한 요청을 해결/체결로 바꾸거나 새 거래를 실행하는 절차가 아니다.

ZIP을 새 폴더에 풀고 RECOVER-NOV6-KEEP-PAUSED.cmd가 있는 CMD에서 실행한다.

```bat
RECOVER-NOV6-KEEP-PAUSED.cmd
```

기존 Desktop/windows-demo-recovery-unresolved-details/unresolved-review.json을 자동으로 읽는다. 이 파일은11월5일 과거4건을 검토할 때 저장한 원본이며 당시 요청 수는78이었다. 찾지 못하면 원본 unresolved-review.json을 새 CMD 옆에 복사한다. 새로 만든6일 진단/리뷰 파일로 원본을 대체하지 않는다. 원본은 보존한다.

복구 작업ID는0f72dd2e-9c5d-4567-aa46-9f3772f79ea7이다. 기존 보호 worker 시험5bb5814c-55e0-4b7f-882c-a7a449ec2182와 별개다. 실제 API의 수정된 worker 코드 SHA9c264588... 및 원래 canonical 복구 소스4개를 확인한다. 수정 미적용/다른 버전이면 적용하지 않는다. 앱/이미지/키 설정을 바꾸거나 재시작하지 않는다.

새 서명 심사에서 다음을 모두 확인한다.

- 과거10월20일 STOP UNKNOWN/EXIT SUBMITTING2건과10월31일 STOP/TARGET NOT_SENT2건의 요청 ref·역할·원본 증거·종료 상태·digest가 원본과 같고, 추가 미해결이 없다.
- 전체 심사 요청 수가 원본보다 정확히4개 늘었으며, 최신 거래의 진입/SL/TP/종료4개가 모두 식별되고 알려진 상태다. 최신0.0007 전량 체결/종료 FILLED/CLOSED·잔량/노출0·보호 확인2개·첫 종료 세대만 허용한다.
- 이번 실패의 finally/finish-exit 표식이 있고 ready 표식은 없으며, 기록된 EVENT/ALGO_UPDATE/DB_LOCK_TIMEOUT 사건과 일치한다.
- 기존 risk/기간/설정/잔고/원격 빈 계정 조건이 canonical 심사를 통과한다.

새 detail 심사의 token은 메모리에서만 기존 acknowledge_flat_emergency에 전달한다. 이 기존 명령은 리스/펜스 아래 전체 심사와 token을 다시 검증한다. 각 심사의 기존40초/600서명 조회/6시계/128이력 bound는 유지한다. 과거 원본 검토 이후 이번 진입/SL/TP/종료만 추가된 것을 허용하며, 그 외 변화는 중단한다.

성공 후 requests/orders/평가값/일간·주간 기준/고점/이전 영수증 등 전체 모델 보존을 확인한다. DB에는 새 복구 확인 영수증·작업·감사·리스 메타데이터와 NORMAL/paused/재대조필요 상태만 반영한다. 기존 미해결4건의 UNKNOWN/SUBMITTING/NOT_SENT 값과 원래 이력은 지우지 않는다. 리스를 반환한 뒤 기존 읽기 전용 준비 명령으로 날짜 일치/원격 빈 계정/진입 정지를 확인한다.

성공은 EMERGENCY_ACKNOWLEDGED_READY_ENTRIES_STILL_PAUSED다. 이미 같은 영수증으로 처리돼 있으면 읽기 전용 준비 확인만 한다. BLOCKED_KEEP_PAUSED_NO_AUTOMATIC_RETRY/BLOCKED_BEFORE_APPLY이면 출력 그대로 공유한다. application_may_have_completed=true라면 적용 후 준비 검사만 실패했을 수도 있어 재실행/잠금 해제를 하지 않는다.

키/Secret을 출력하거나 호스트 파일에 저장하지 않는다. 새 review token은 화면이나 임시 파일로 복사하지 않고 메모리에서 전달하며, 기존 canonical 복구 영수증의 DB 감사 기록은 그대로 유지한다. 원본과 검증한 보조 코드만 새 작업 전용/tmp0700 디렉터리/0600 파일로 잠깐 올리고 끝나면 정리한다. 결과는 nov6-flat-recovery-result.json에 저장하고 기존 파일이 있으면 덮어쓰거나 반복하지 않는다. 조회에 시간이 걸릴 수 있으니 창을 유지하고 다른 거래/복구를 동시에 실행하지 않는다.

이 절차는 새로운 주문/프로세스 종료/거래 재개/LIVE/Phase5가 아니다. 기존5bb 시험은 미수락 기록 그대로 남는다. 다음 실제 시험은 별도로 새 작업과 실행 범위를 정한 뒤 진행한다.
