# 주문 없는 개인 WSS 30분 연속 관찰과 listenKey 갱신

새 windows-demo-private-stability-check 폴더의 CMD 하나에서 실행한다. 기존 hybrid-windows DB/볼륨/기준금/주문 이력을 유지한다. 초기화하지 않는다. 이번에는 신규 주문이나 장애 주입이 없다.

## 순서

1. ZIP을 새 폴더에 풀고 해당 폴더에서 CMD를 열어 빌드가 Healthy까지 끝나기를 기다린다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait
```

2. 교체된 API 컨테이너의 private tmpfs에 DEMO 키를 등록한다. API Key와 Secret은 숨김 입력이며 채팅에 보내지 않는다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -m scripts.demo_key_session --register
```

3. 아래 명령을 한 번만 실행한다. 신규 주문은 제출하지 않는다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -T api python -m scripts.check_demo_private_stability --observe-30-minutes --operation-id 87723a08-552e-43aa-9d5c-062de037aec8
```

첫 OBSERVING 출력 이후30분 관찰한다. 최초 연결/대조와 마지막 세션 정리는 추가로 걸린다. 약1분마다 elapsed_seconds와 successful_key_renewals가 표시된다. 초반 갱신0은 정상이며, 기존 세션 코드가 listenKey 생성 후 약25분에 갱신한다. 진행 중 PC가 절전되지 않도록 하고 CMD를 닫거나 API/DB를 재시작하지 않는다. 다른 명령이나 수동 단절·주문을 동시에 실행하지 않는다.

최종 결과 전체를 공유한다. 오류/NOT_ACCEPTED가 나오면 재실행하지 않는다. 이 도구에는 관찰 시간을 줄이는 CLI 옵션이 없고, 이미 시작/결과 기록이 있는 UUID는 세션 생성 전에 차단한다.

## 성공 조건과 범위

EMPTY_PRIVATE_SESSION_STABILITY_RENEWAL_VERIFIED, target_seconds1800/observed_elapsed_ms1800000 이상, key_create_success_count1/성공 갱신1 이상/key_close_success_count1, failure 및 completion_failure NONE, 이력·기준 보존, 최종 NORMAL/paused/stream false/reconciled false를 요구한다. 실제 WSS session은 한 번이며 자동 재연결하지 않는다. 빈 계좌에서 수신 이벤트0도 가능하고, 그 경우 원래 ping/pong 및 REST 재대조로 감독한다.

모든 일반 SDK request의 GET 이외 메서드는 기존 ObservationClient에서 전송 전에 차단한다. listenKey POST/PUT/DELETE는 별도 기존 수명 관리 경로다. DB 리더/감사 기록은 변경하므로 DB 읽기 전용은 아니다. 계좌는 NORMAL·일시정지·ISOLATED BTCUSDT 2x·포지션/활성 주문0이어야 한다. 새 신호, reserve/submit/resume, 입출금, exports 생성은 없다. 기존 lease/시계/RTT/이벤트 시간/리스크 제한을 변경하지 않는다.

30분 경과 뒤 원래 세션의 pause/재대조/키 삭제를 완전히 drain하고 거래소가 여전히 비었는지 확인한다. 갱신·키 삭제·감사 기록·정리 실패는 통과가 아니다. 일반 세션 오류와 별도로 allowlist 진단을 출력하며 비밀 키·원문 이벤트·URL·예외를 출력하지 않는다.

이 검증은30분 단일 개인 세션과 listenKey 갱신 범위다.24시간 운용, 모든 원문 WSS의 영구 저장, 실제 부분체결 보호, 주문/포지션 보유 Worker 강제 종료, 호스트/DB 전체 중단 또는 Phase4 전체 완료를 뜻하지 않는다. LIVE/Phase5를 시작하지 않는다.
