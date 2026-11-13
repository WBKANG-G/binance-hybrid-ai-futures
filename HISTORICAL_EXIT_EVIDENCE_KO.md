# 미조회 종료 요청 1건의 원래 ID 조회

이전 조회에서 진입 2건은 FILLED, 손절 1건은 400 / NOT_FOUND_UNRESOLVED로 중단됐고 종료 요청 1건이 미조회로 남았을 때만 사용합니다. 이전 실행 자체는 다시 실행하지 않습니다.

이 패키지는 이전 의도·완료 파일, 고정 소스 참조, 원장/파일 해시, 감사 경로와 체크포인트를 읽기 전용으로 검증합니다. 이전 GET 5회/모델 비용 9/누적 모델 비용 277과 정확한 ENTRY FILLED 2건 → STOP_LOSS NOT_FOUND_UNRESOLVED 순서를 요구합니다. 그 결과와 현재 원장의 요청 ID 연결이 일치하는 미조회 EXIT 1건만 새 작업 ID로 조회합니다. 이전 진입·손절 ID를 다시 요청하지 않습니다.

바탕화면에 압축 해제한 후 CMD에서 한 줄씩 실행합니다. 각 명령이 끝난 후 다음 명령을 입력하세요.

```cmd
docker exec -it hybrid-windows-api-1 python -m scripts.demo_key_session --ensure
```

`AVAILABLE`을 확인한 다음:

```cmd
cd /d "%USERPROFILE%\Desktop\windows-demo-historical-exit-evidence-20261113"
```

```cmd
RUN_HISTORICAL_EXIT.cmd
```

최대 GET 3회: 시간, 계정 모드, 종료 요청의 origClientOrderId 조회입니다. 기존 설치된 감사 전송만 사용하며 모두 응답하면 모델 비용 7, 감사 이벤트/체크포인트 각 6개가 추가되고 누적 모델 비용은 284가 됩니다. 주문 없음 400 응답도 그 자리에서 중단하고 NOT_FOUND_UNRESOLVED로 유지합니다. 네트워크 불명·429·불일치 등에는 완료를 추정하거나 재시도하지 않습니다.

완료 표시:

```text
HISTORICAL_EXIT_EVIDENCE_RECORDED_KEEP_PAUSED_NO_ORDERS
```

이는 관측과 보존 검사 완료를 뜻하며 원격 해소나 실행 승인이 아닙니다. 요청 상태와 재무 원장, 기존 영수증/감사 이력은 그대로 두고 새 의도·결과 파일과 감사 비용 기록만 추가합니다. 조회 응답의 본문 해시는 감사 체인 자체에 바인딩되지 않으며 별도의 체결/해소 증명으로 간주하지 않습니다. 역사 34건의 전체 증명, 용량 부족, 주문 한도/IP 용량 등 남은 차단 항목은 해소하지 않습니다. 주문·LIVE·실행 승인도 false입니다.

시계나 기간이 달라졌으면 조회 전에 차단하며 자동 시계 복구나 날짜·기준액 변경을 하지 않습니다. 키 세션 오류도 조회 전에 중단합니다. 결과나 오류를 공유하고 RUN_HISTORICAL_EXIT.cmd를 다시 실행하지 마세요.

이번 종료 조회 이후에는 이 폴더의 아래 명령만 사용합니다. 이전 패키지의 상태 명령은 추가된 감사 기록을 예상하지 않으므로 재사용하지 않습니다.

```cmd
RUN_HISTORICAL_EXIT_STATUS.cmd
```

이 상태 확인은 키·HTTP·비용 추가 없이 새 작업의 증거와 이전 파일 보존을 대조합니다. 최초 실행 수치는 original_attempt_summary로 구분합니다. 미완료 의도가 있으면 재시도 없이 차단합니다.
