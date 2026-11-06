# 보호 worker 준비 실패의 읽기 전용 진단

실제 Windows 결과:5bb5814c-55e0-4b7f-882c-a7a449ec2182 작업이 PROTECTED_READY_NOT_OBSERVED로 미수락됐다. child exit2, 강제 종료0, finally 기록 있음, parent 진입/종료/보호 POST0, 최종 VERIFIED_FLAT/leader released, EMERGENCY/paused다. parent POST0은 child의 주문 제출0을 뜻하지 않는다. 이번 도구는 그 child의 실제 원장/감사 기록을 읽어 확인한다.

ZIP을 새 폴더에 풀고 INSPECT-PROTECTED-READY.cmd가 있는 CMD에서 실행한다.

```bat
INSPECT-PROTECTED-READY.cmd
```

앱 배포·컨테이너 재시작·키/SMTP 설정·주문/취소/강제 종료·위험 상태 복구를 수행하지 않는다. PostgreSQL REPEATABLE READ, READ ONLY 거래 안에서 고정 계정/고정 작업 시작 이벤트부터 자식과 후속 감독자의 두 fence 구간에 한정한 감사 이벤트를 읽는다. statement timeout3초, 결과 상한200개와 truncation 표시를 사용한다. 다른 거래소/서명 API 요청은0이다. 계정 메타데이터/금융 행/알림 큐/감사 행을 변경하지 않는다.

화면에는 현재 로컬 주문 상태와 PRIVATE_SESSION_FAILED 이벤트를 요약한다. 전체 제한된 타임라인은 protected-ready-review.json에 저장한다. 원장 전체/거래소 원문/실제 주문ID/주소/키/토큰/오류 원문은 출력하지 않는다. 기존 파일이 있으면 덮어쓰지 않는다.

화면 결과를 공유하고 필요하면 protected-ready-review.json 파일도 첨부한다. session_failure.code와 stage, 최초 진입/SL 확인/종료 이벤트 순서를 확인해 추측 없이 원인을 좁힌다. 현재 버전 child는 예외 원문을 영속 기록하지 않았으므로 기록이 없으면 원인을 확정했다고 주장하지 않는다. 향후 진단 보강과 실제 재실행은 구분한다.

이 조회는 현재 거래소 상태나 전체 복구 적격성을 심사하는 것이 아니다. 과거 실행의 VERIFIED_FLAT과 로컬 상태 기록을 혼동하지 않는다. EMERGENCY/paused를 유지하고 RUN-ONE-PROTECTED-WORKER.cmd 재실행·로그 삭제·잠금 해제를 하지 않는다.
