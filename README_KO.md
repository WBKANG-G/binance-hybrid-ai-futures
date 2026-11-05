# 주문 없는 DEMO WSS 힌트 관찰·재생

이 도구는 기존 정지·빈 계정에서30초 private WSS 관찰을 수행한다. source5개 해시와 읽기 전용 준비 상태(NORMAL/paused, 기간 일치, 거래소 empty)를 먼저 검사한다. 새 주문·취소·계정 설정 변경은 ObservationClient가 차단한다. listen-key 생성/종료와 리스·조회·감사 메타데이터 갱신은 발생할 수 있다. 기존 API 키 캐시/소스/이미지는 바꾸지 않는다.

새 폴더에 압축을 풀고 CAPTURE-DEMO-WSS-HINTS.cmd가 보이는 곳에서 CMD를 열어 실행한다.

```bat
CAPTURE-DEMO-WSS-HINTS.cmd
```

약30초 관찰에 준비·정리 시간이 추가된다. 창을 닫거나 동시에 다른 거래/관찰 명령을 실행하지 않는다. 출력은 wss-hint-report.json에도 저장된다. 기존 결과 파일은 덮어쓰지 않는다. 출력 전체를 공유한다. 키 입력이나 재빌드는 없다. 세션 만료/버전 불일치/기준일 불일치면 중단하고 결과만 공유한다. 자동 재시도하지 않는다.

기록하는 필드는 알려진 이벤트 종류e, 원래 타임스탬프E, 수신 로컬 시각, 검증된 시계 probe의 before/after/server 시각뿐이다. 주문·잔고·계정 본문, listen-key/URL/API키/Secret은 기록하지 않는다. 원본 프레임은 메모리에서 기존 EventInbox 검증기에 그대로 전달하며 결과 파일에 보관하지 않는다. 최대128개 힌트/프레임1MiB 제한이다.

수신 힌트가 있으면 기록된 probe를 기준으로 기존 이벤트 시간 검증과 EventInbox admission/coalescing을 로컬 재실행한다. E를 바꾸지 않는다. 각 수신 시점의 시계 상태를 복원하는 검증이며 실제 이벤트 간 지연/체결 연산/REST 계정 상태를 재현하는 시험은 아니다. 전체 raw private frame 재생은 아니다.

NO_ORDER_OBSERVATION_FINISHED는 관찰 완료다. replay.hint_replay가 CAPTURED_HINT_ADMISSION_REPLAY_PASSED일 때만 실제 수신 힌트의 재생 범위를 확인한다. NO_EVENTS_NOT_ACCEPTED면 정상 연결에서 이벤트가 없었다는 뜻이며 재생 검증은 미완료다. 이벤트를 만들려고 새 주문을 내거나 반복하지 않는다. BLOCKED_OR_FAILED_NO_AUTOMATIC_RETRY면 관찰/보존/재생 중 해당 단계가 확인되지 않았으므로 출력 그대로 공유한다.

관찰 전후 일간/주간/고점/위험/정지/주문·요청·입출금/기준금 수락 이력·잔고 관련 금융 필드의 digest를 비교한다. digest와 원장 자체는 출력하지 않는다. 메타데이터와 value.as_of는 비교에서 제외한다. 변경 검출은 되돌림이 아니며, 다른 프로세스 동시 변경도 실패 원인이 될 수 있다. listen-key 종료 결과는 기존 루틴의 best-effort 정리이므로 관찰 완료만으로 종료 성공을 별도로 보장하지 않는다.

현재 Phase4 전체, 실제 TP·부분체결·포지션 보유 장애, 전체 private frame 재생의 완료를 선언하지 않는다. 새 LIVE/Phase5/정책 변경을 하지 않는다.
