# 손절 로컬 조립 실패와 원래 자동 비상 종료 검증

이번 단계는 Phase4의 손절 생성 실패 대응 중 로컬 조립 실패 경로를 검증합니다. 실제 거래소 거부 응답을 만들거나 보호되지 않은 포지션을 유지하는 시험이 아닙니다. 실제 거래소 Algo 거부·외부 긴급 알림 전달까지 통과했다고 주장하지 않습니다.

최소 cap/100 USDT 이내의 BTCUSDT LONG 지정가 진입1건을 DEMO에 실제 제출합니다. 현재 ask의 OPPOSITE_QUOTE LIMIT이며 시장가 신규 진입은 없습니다. 체결 뒤 같은 의도의 SL 조립에만 ValueError를1회 주입합니다. 그때 기존 coordinator가 NOT_SENT/LOCAL_VALIDATION_FAILED, EMERGENCY/paused를 기록하고 원래 reduce-only 비상 종료를 수행해야 통과입니다. 도구가 후속 정리로만 flat을 만든 경우에는 통과하지 않습니다.

## CMD 실행 순서

ZIP을 바탕 화면의 새 windows-demo-sl-fault-check 폴더에 풉니다. 기존 폴더에 덮어쓰지 않습니다. 새 폴더의 탐색기 주소창에 cmd를 입력하고 아래 명령을 한 줄씩 실행합니다. 같은 프로젝트 hybrid-windows 및 기존 볼륨을 유지합니다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait
```

DB/API/web Healthy 확인 뒤 키를 RAM 등록합니다. 입력은 숨겨집니다. 키/Secret을 채팅에 보내지 않습니다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -m scripts.demo_key_session --register
```

등록 성공 뒤 아래 명령을 한 번 실행합니다. 다른 수동 주문/봇/입출금은 하지 않습니다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -T api python -m scripts.check_demo_sl_fault --enable-demo --enable-sl-build-fault --operation-id 5c96345b-f0c6-42cc-8132-534b26546577
```

전체 JSON 결과를 캡처합니다. 정상 결과는 SL_BUILD_FAILURE_GATE_AUTO_FLAT_VERIFIED이고, flat_confirmation VERIFIED_FLAT, exit_state FILLED, automatic_exit_observed_before_cleanup true, fallback_operator_emergency_requested false입니다. 최종 EMERGENCY/paused는 유지합니다. 성공 뒤에도 신규 진입을 자동 재개하지 않습니다. 결과 확인 후 별도의 읽기 전용 복구 검토와 승인이 필요합니다.

NOT_ACCEPTED/BLOCKED/UNKNOWN이 나오거나 명령이 끊기면 새 UUID로 다시 주문하지 말고 결과를 공유합니다. 동일 의도 재실행은 새 진입·재주입 없이 정리만 하는 RECOVERY_ONLY 경로지만 임의 반복하지 않습니다. UNKNOWN 종료는 재POST하지 않습니다. DB/볼륨 삭제·기준금 재설정·exports 생성·과거 요청 삭제는 없습니다.

기존 SL/TP/주문/리스크/청산 로직을 바꾸지 않습니다. 별도 CLI 프로세스 안에서 own SL 조립 함수만 임시 교체하고 세션/DB 작업 정리 뒤 원상 복원합니다. own SL/TP의 뜻밖의 POST는 별도 전송 장벽이 차단하고 통과를 거부합니다. 장애 이후 보호되지 않은 노출을 추가로 기다리거나 재진입하지 않습니다. 종료 응답이 불명확하면 성공으로 표시하지 않습니다. 관찰90초는 연결·체결·결과 관찰의 상한이며 무보호 상태를90초 유지하도록 만드는 타이머가 아닙니다.
