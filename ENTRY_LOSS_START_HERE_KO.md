# 진입 주문 응답 유실과 동일 ID 복구 검증

이번 시험은 Phase4 T-01 중 로컬 응답 유실 에뮬레이션입니다. 실제 SDK가 성공 응답을 받은 뒤 coordinator에 전달할 ACK만 한 번 버립니다. 실제 네트워크 timeout/응답 패킷 유실을 발생시키는 시험은 아닙니다. 기존 주문/리스크/보호/청산 코드는 변경하지 않습니다.

최소 cap/100 USDT 이내 BTCUSDT LONG OPPOSITE_QUOTE LIMIT1건을 DEMO에 실제 제출합니다. 미체결이어도 응답 유실·UNKNOWN 기록·같은 ID 복구를 검증할 수 있습니다. 체결되면 원래 SL/TP 보호와 비상 정리 경로를 사용합니다. 추가 진입·수량 증가·시장가 신규 진입은 없습니다. 이전 SL 실패 시험을 반복하거나 손절 실패를 주입하지 않습니다.

## CMD 순서

ZIP을 바탕 화면의 새 windows-demo-entry-response-loss 폴더에 풉니다. 기존 폴더에 덮어쓰지 않습니다. 새 폴더의 탐색기 주소창에 cmd를 입력하고 아래 명령을 한 줄씩 실행합니다. 기존 hybrid-windows 프로젝트/볼륨을 유지합니다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait
```

모든 컨테이너 Healthy 확인 뒤 숨김 입력으로 DEMO 키를 RAM에 등록합니다. 키/Secret을 채팅에 보내지 않습니다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -m scripts.demo_key_session --register
```

등록 성공 뒤 아래 명령을 한 번만 실행합니다. 시험 중 수동 주문/다른 봇/입출금은 하지 않습니다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -T api python -m scripts.check_demo_entry_response_loss --enable-demo --enable-entry-response-loss --operation-id 9be80b67-25d4-43d6-b18b-7d12fdd9cd0e
```

전체 JSON을 캡처합니다. 정상 결과는 ENTRY_ACK_LOSS_SAME_ID_NO_DUPLICATE_FLAT_VERIFIED입니다. response_drop_count1, unknown_intent_persisted true, entry_gate_blocked_while_unknown true, same_id_signed_query_verified true, local_order_recovered_before_cleanup true, 신규 진입 POST/시도 각각1, flat_confirmation VERIFIED_FLAT가 필요합니다.

최종 신규 진입은 paused입니다. 체결 후 비상 정리를 사용했으면 EMERGENCY를 유지하고 결과 확인 뒤 별도 복구 검토로 진행합니다. 미체결·flat이면 NORMAL일 수 있으며 이를 비상 상태로 만들지 않습니다. 이 결과를 손절 실패나 실제 부분체결 보호의 Windows 통과로 분류하지 않습니다.

NOT_ACCEPTED/BLOCKED/UNKNOWN 또는 명령 중단 시 새 UUID로 재주문하지 말고 결과를 공유합니다. 원래 이력의 UNKNOWN을 지우거나 ACKNOWLEDGED로 바꾸지 않습니다. 같은 의도의 재실행은 새 진입·재주입 없이 정리만 하는 경로지만 임의로 반복하지 않습니다. 종료 UNKNOWN도 재POST하지 않습니다. down -v, DB 초기화, 기준금/고점 재설정, exports 재요청은 없습니다.
