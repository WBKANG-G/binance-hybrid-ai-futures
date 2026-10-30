# 누적 요청을 보존하는 복구 검토

기존 Galaxybook에서 ZIP을 새 windows-demo-recovery-batch-fix 폴더에 풀고, 그 폴더의 탐색기 주소창에 cmd를 입력하세요. 기존 hybrid-windows 데이터베이스와 볼륨을 유지합니다. 볼륨 삭제나 초기화를 하지 마세요.

실제 DEMO 검증 한 건은 체결·SL/TP 조회 확인·청산까지 통과했습니다. 후속 로컬 조회는 포지션0, 청산 FILLED, EMERGENCY/일시 정지를 확인했습니다. 누적 요청31건이 이전 복구 검토 한도30건을 넘어 이번 보완이 필요합니다.

다음 명령을 한 줄씩 실행합니다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait
```

컨테이너 교체로 메모리 키가 지워지므로 한 번 등록합니다. 키는 터미널의 숨김 입력에만 넣으세요. 이후 같은 컨테이너에서는 최대12시간 재사용합니다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -m scripts.demo_key_session --register
```

주문 없는 검토를 실행하고 결과를 공유하세요.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -T api python -m scripts.review_demo_recovery
```

상태가 그대로라면 audited_request_count=31, audit_batch_count=4, unresolved_count=2를 예상합니다. 실제 결과로 판단합니다. 이 단계는 데이터베이스를 변경하거나 주문을 제출하지 않습니다. 과거 토큰을 재사용하거나 새 거래·잠금 해제·기간 기준 재적용을 하지 마세요. 자원 한도나 거래소 조회 실패로 차단되면 잠금을 유지한 채 결과를 공유하세요.

이후 별도의 최신 검토 토큰을 사용한 명시적 적용이 성공해야 비상 상태를 확인 처리할 수 있습니다. 그 경우에도 진입 일시 정지는 유지됩니다. LIVE나 Phase5 승인은 포함하지 않습니다.
