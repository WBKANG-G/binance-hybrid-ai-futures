# 빈 계정의 개인 WS 단절·재연결 검증

이번 단계는 새 진입 없이 개인 WebSocket을 의도적으로 한 번 닫고, 차단 상태를 확인한 뒤 한 번 재연결해 REST 재대조하는 검사입니다. 인터넷 전체 차단이나 주문이 열린 상태의 장애 검증과는 범위가 다릅니다. 기존 정상 연결20초 관찰·빈 API 재시작 성공 기록은 WINDOWS_DEMO_EMPTY_RESTART_AND_WS_20261031_KO.md에 보존했습니다.

기존 Galaxybook에서 ZIP을 새 windows-demo-disconnect-check 폴더에 풀고 그 폴더의 탐색기 주소창에 cmd를 입력하세요. 기존 hybrid-windows 데이터베이스와 볼륨을 유지합니다. 삭제·초기화하지 마세요.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait
```

컨테이너 교체로 메모리 키가 초기화되므로 한 번 등록합니다. 키는 숨김 입력에만 넣으세요. 이후 같은 컨테이너에서는 최대12시간 재사용합니다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -m scripts.demo_key_session --register
```

명시적으로 단절 주입을 실행합니다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -T api python -m scripts.check_demo_disconnect --enable-fault-injection
```

정상 흐름에서는 READY → BLOCKED → RECONNECTED 단계 출력 뒤 최종 EMPTY_SOCKET_LOSS_RECONNECT_VERIFIED가 나옵니다. 의도한 첫 단절의 SOCKET_CLOSED는 이 검사에서 확인하려는 증거이며, 최종 성공 판정과 함께 보세요. 자동 재시도 루프 없이 소켓 종료1회·재연결1회만 수행합니다. 단계별 연결/관찰 예산은45초이며 초기 조회와 종료 정리 시간이 더해질 수 있습니다. 터미널 입력창으로 돌아온 뒤 전체 결과를 공유하세요.

이 도구는 NORMAL·진입 정지·로컬 포지션0·모든 기존 포지션 CLOSED를 요구하고, 현재 거래소 포지션/활성 주문도 확인합니다. 조건이 맞지 않거나 시간 초과·재연결 실패·기록 변경이 생기면 성공으로 처리하지 않습니다. 주문·계정 변경 요청은 전송 전에 차단합니다. 허용되는 쓰기 요청은 기존 SDK를 통한 개인 세션 listenKey 생성/종료뿐입니다. 데이터베이스에는 임대·재대조·세션·차단 확인 감사 기록을 남기므로 DB 읽기 전용 검사는 아닙니다.

검사 중에도 진입 정지를 해제하거나 진입 신호를 공급하지 않습니다. 성공 후에는 정상적으로 세션을 닫아 stream_ready/reconciled=false, paused=true로 종료합니다. 마지막 소켓 실패 진단은 과거 증거로 남을 수 있습니다. 일일/주간 기준·고점·과거 주문과 미해결 요청은 보존합니다. LIVE/Phase5, 실제 부분 체결 보호, 열린 주문 중 재시작, 무인 장시간 운영 완료는 포함하지 않습니다.
