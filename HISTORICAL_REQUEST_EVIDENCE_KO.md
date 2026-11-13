# 과거 요청 4건의 원래 ID 조회

13일 적용 후 V2 점검에서 확인한 진입 UNKNOWN 2건, 손절 UNKNOWN 1건, 종료 SUBMITTING 1건을 대상으로 합니다. 현재 포지션이 닫혔다는 이유만으로 과거 요청을 해소 처리하지 않습니다.

ZIP을 바탕화면에 압축 해제합니다. 아래 명령은 한 줄씩 실행하고 각각 완료된 뒤 다음 명령을 입력합니다. 키 입력 중에는 다른 명령을 붙여넣지 마세요.

```cmd
docker exec -it hybrid-windows-api-1 python -m scripts.demo_key_session --ensure
```

`AVAILABLE`을 확인한 다음:

```cmd
cd /d "%USERPROFILE%\Desktop\windows-demo-historical-request-evidence-20261113"
```

```cmd
RUN_HISTORICAL_REQUESTS.cmd
```

최대 6 GET: 시간 1회, 계정 모드 1회, 원래 요청 ID별 최대 4회입니다. 모두 기존 설치된 감사 전송으로 예약·완료를 기록합니다. 모두 성공하면 모델 비용 10, 감사 이벤트/체크포인트 각각 12개가 추가됩니다. 키는 기존 비공개 임시 세션만 사용합니다. 요청 원문 ID나 응답 원문은 화면에 출력하지 않습니다.

`400`과 주문 없음 코드가 나오면 기존 전송 정책대로 그 자리에서 중단합니다. 다른 ID를 이어서 조회하지 않습니다. `NOT_FOUND_UNRESOLVED`는 미해결로 남습니다. 418/429, 5xx, 네트워크 불명, 응답 불일치 등도 중단하고 자동 재시도하지 않습니다.

완료 표시:

```text
HISTORICAL_REQUEST_EVIDENCE_RECORDED_KEEP_PAUSED_NO_ORDERS
```

이는 관측 기록의 저장/대조가 끝났다는 뜻이며, 4건 모두 조회됨이나 원격 해소를 뜻하지 않습니다. `requests_observed`, `requests_unqueried`, `stopped_after_not_found`, 각 `observed_status`를 확인하세요.

원장·요청 상태·재무 이력·기존 영수증은 그대로 보존합니다. 자체 비공개 의도/결과 파일과 공유 요청 비용·감사 기록만 추가합니다. 원격 계정 식별, 주문 사용량 한도, 실제 IP 여유, 현재 비용 상한, 전체 재무 증명, 실행 준비를 검증하지 않습니다. 전체 역사 34건의 증명 모델이나 기존 용량 부족을 대체·해소하지 않습니다. 주문·LIVE·실행 승인도 계속 false입니다.

현재 시계가 바뀌었거나 날짜가 달라졌으면 조회 전에 차단합니다. 시계를 자동 복구하거나 날짜·기준액을 변경하지 않습니다. 초기화·재설치·임의 시계 조정 없이 결과 화면을 공유하세요.

의도가 만들어진 뒤에는 RUN_HISTORICAL_REQUESTS.cmd를 다시 실행하지 마세요. 이후 확인에는 다음 읽기 전용 명령만 사용합니다. 키나 HTTP를 사용하지 않으며 저장된 결과, 감사 경로와 보존 상태를 대조합니다. 미완료 의도면 재시도 없이 차단합니다.

```cmd
RUN_HISTORICAL_STATUS.cmd
```

응답 본문 해시는 비공개 결과 파일에 저장되지만 기존 감사 체인은 본문 내용을 직접 바인딩하지 않습니다. 따라서 관측을 독립적인 체결/원격 해소 증명으로 승격하지 않습니다.
