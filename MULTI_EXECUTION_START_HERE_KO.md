# 동일 가격의 초기 분할 체결 증거 보완

실제 작업0feb6a0e-21b2-4f8b-aad5-f101606dc4bb는0.0007 BTC 주문 중0.0003씩 두 번 체결돼 총0.0006이 됐습니다. 진입은 CANCELED, 최종 포지션은 CLOSED이며 후속 거래소 조회도 포지션0·활성 일반/조건부 주문0을 확인했습니다. SL/TP는 검증되지 않았으므로 부분 체결 수락은 실패입니다. 기존 코드는 단일 체결만 허용했습니다. 당시 최초 세 스냅샷 값이 보존돼 있지 않아 어떤 검사가 가장 먼저 실패했는지까지 단정하지 않습니다.

이번 보완은 동일 주문·동일 가격의 여러 체결 수량과 수수료를 정확히 합산합니다. 기존 초기 체결 경로의 자격과 안정된 마지막 두 스냅샷 요구는 유지합니다. 가격이 서로 다르거나 관찰 중 수량/담보가 계속 바뀌는 복잡한 진행은 여전히 차단할 수 있습니다. 임의 반올림이나 추가 허용 오차, 반복 주문 제출을 넣지 않았습니다.

기존 Galaxybook에서 ZIP을 새 windows-demo-multi-execution-fix 폴더에 풀고 그 폴더의 탐색기 주소창에 cmd를 입력하세요. hybrid-windows 데이터베이스와 볼륨은 유지합니다. 삭제·초기화하지 마세요.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait
```

컨테이너 교체로 메모리 키가 초기화되므로 한 번 등록합니다. 키는 숨김 입력에만 넣으세요. 이후 같은 컨테이너에서는 최대12시간 재사용합니다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -m scripts.demo_key_session --register
```

먼저 새 주문 없이 복구 검토만 실행하고 결과를 공유하세요.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -T api python -m scripts.review_demo_recovery
```

새 토큰을 확인하기 전 apply-review를 실행하거나 실패한 작업을 다시 제출하지 마세요. 이번 ZIP은 기존 실패 기록을 성공으로 변경하지 않습니다. 10월31일 일일 기준4999.35095801, 주간 기준4999.64804199, 고점5000과 미해결 이력은 보존합니다. 날짜가 다시 달라지면 기간 기준 검토가 별도로 필요할 수 있습니다. 복구 적용이 성공해도 진입 정지는 유지됩니다. 추가 실제 DEMO 검증은 복구 후 새 작업으로 진행해야 합니다. LIVE/Phase5와 전체 Phase4 완료는 포함하지 않습니다.
