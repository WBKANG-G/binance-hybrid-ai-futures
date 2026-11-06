# 기존 종료 확인 경로 수정본

추가 진입·청산 시험이 아닙니다. 이미 체결된 종료와 현재 flat을 다시 확인하는 명령만 실행합니다. 현재 NORMAL/paused 상태를 유지하며 새 UUID나 거래 주문은 없습니다.

## CMD 순서

ZIP을 바탕 화면의 새 windows-demo-flat-confirmation-fix 폴더에 풉니다. 기존 폴더에 덮어쓰지 않습니다. 해당 폴더의 탐색기 주소창에 cmd를 입력하고 아래 명령을 한 줄씩 실행합니다. 기존 hybrid-windows 프로젝트와 볼륨을 유지합니다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait
```

모든 컨테이너 Healthy 확인 뒤 DEMO 키를 RAM 등록합니다. 숨김 입력이며 키/Secret을 채팅에 보내지 않습니다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -m scripts.demo_key_session --register
```

기존 종료 확인을 한 번 실행합니다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -T api python -m scripts.run_demo --config configs/demo-isolated.example.yaml --account DEMO-WINDOWS-ISOLATED-BTC-V1 --enable-demo --confirm-flat
```

정상 결과는 completion_confirmation VERIFIED_FLAT입니다. confirmation_stage와 confirmation_diagnostic도 포함한 전체 결과를 캡처합니다. 이 명령은 신규 진입/종료 POST를 허용하지 않습니다. flat과 종료 체결 증거를 확인한 뒤 자기 이력의 잔여 보호 Algo가 있을 때만 기존 DELETE 정리를 허용합니다. 이미 정리됐으면 DELETE도0회입니다. DB의 최신 조회 증거/감사 이벤트는 기록하지만 기준금/과거 요청/UNKNOWN 증거를 고치지 않습니다.

BLOCKED_KEEP_LOCK 또는 UNKNOWN이면 반복하지 말고 전체 결과를 공유합니다. 40초 등 예산 초과도 상한을 늘리거나 잠금을 풀지 않습니다. 현재 일시정지를 유지합니다. down -v, 기준금/고점 초기화, exports 재요청, 과거 요청 삭제를 실행하지 않습니다. 손절 실패·진입 응답 유실 시험을 재실행하지 않습니다.

## 이번 수정

기존 종료 확인의 과거 주문 조회와 독립 스냅샷 최대5회, 정합성/종료 체결/flat/잔여 보호 검증은 그대로입니다. 한 번의 확인 안에서 GET 연결을 재사용하고, 필요한 시계 동기화를 제한된 횟수로 갱신합니다. 인증서·호스트·프록시·리다이렉트 거부는 이전 검증된 HTTPS 전송과 동일합니다. 보호 DELETE는 원래 SDK 전송으로 처리하며 자동 재전송하지 않습니다.

QUERY는 첫 flat 증거 조회, CLEANUP은 입증 후 보호 정리, VERIFY는 최종 재검증입니다. PREFLIGHT 또는 TRANSPORT_SETUP에서 막혔다면 본 확인 전입니다. 시계 수명60초·리스60초·펜스 및 사후 리스 검사를 그대로 유지하며 중간 리스 갱신/조회 생략/캐시/병렬 조회를 추가하지 않았습니다.
