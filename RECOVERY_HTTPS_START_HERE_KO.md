# 복구 검토 HTTPS 연결 재사용 수정본

기존 TP 시험을 반복하지 않습니다. 현재 EMERGENCY/paused 잠금을 유지한 채 새 읽기 전용 검토를 한 번 실행합니다. 신규 주문·청산·exports 생성 명령은 없습니다.

1. ZIP을 바탕 화면의 새 windows-demo-recovery-https-reuse 폴더로 풉니다. 기존 폴더에 덮어쓰지 않습니다.
2. 해당 폴더의 탐색기 주소창에 cmd를 입력합니다. 아래 명령을 순서대로 한 줄씩 실행합니다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait
```

모든 컨테이너 Healthy 확인 뒤 DEMO 키를 다시 RAM 등록합니다. 재빌드로 이전 RAM 등록이 사라질 수 있습니다. 숨김 입력값을 채팅에 보내지 않습니다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -m scripts.demo_key_session --register
```

등록 성공 뒤 검토만 실행합니다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -T api python -m scripts.review_demo_recovery
```

전체 JSON 결과를 캡처해 공유합니다. REVIEW_ONLY_NO_DATABASE_CHANGE여도 아직 잠금 해제가 아닙니다. BLOCKED_KEEP_LOCK이면 반복·해제하지 않습니다. 이 문서에는 apply 명령이 없습니다. 새 검토 결과를 확인한 뒤에만 별도 복구 절차로 진행합니다. 이전 토큰을 재사용하지 않습니다.

기존 PostgreSQL 볼륨·이력·일간/주간 기준금·고점은 유지합니다. down -v, DB 초기화, 기준금 재설정, exports 재요청을 실행하지 않습니다. 프로젝트 이름 hybrid-windows를 그대로 사용합니다.

검토 한 번 안에서만 GET용 HTTPS 연결을 재사용합니다. 인증서/호스트 검증, 기존 urllib 프록시 설정, 리다이렉트 거부는 유지합니다. 서버가 연결을 닫으면 다음 새 요청은 새 연결을 열지만 실패한 요청은 자동 재전송하지 않습니다. 40초 제한과 전체 과거 요청 검증을 유지하므로 실제 응답이 계속 느리면 다시 차단될 수 있습니다. 진단 숫자로 연결 생성·재사용·서버 종료를 구분합니다.
