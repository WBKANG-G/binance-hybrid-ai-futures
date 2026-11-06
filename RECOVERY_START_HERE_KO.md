# Phase 4 복구 사전 검토 — 이번에는 검토만

현재 과거 DS/DX 요청 두 건은 미확정이며 기록을 유지합니다. 이 묶음은 복구 검토와 명시적 운영자 확인을 별도 단계로 분리합니다. 설치와 기본 검토 명령은 EMERGENCY 및 paused 잠금을 해제하지 않습니다. 기존 주문 실행 명령을 재실행하지 마세요.

1. ZIP을 모두 추출하고 windows-demo-recovery 폴더에서 infra가 보이는 위치로 이동합니다.
2. Docker Desktop을 켜고 탐색기 주소창에 cmd를 입력한 뒤 Enter를 누릅니다.
3. 아래 설치 명령을 실행합니다. 기존 프로젝트 이름과 DB 볼륨을 유지합니다.

    docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait

4. 모두 Healthy면 아래 기본 검토 명령만 실행합니다.

    docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -m scripts.review_demo_recovery

5. DEMO API Key와 DEMO Secret을 차례로 붙여넣고 Enter를 누릅니다. 화면에 입력값이 보이지 않는 것이 정상입니다. 키를 채팅에 보내지 마세요. 숨김 입력을 사용할 수 없으면 입력 전에 중단합니다.
6. 마지막 결과를 대화에 보내주세요. 정상 검토 결과는 REVIEW_ONLY_NO_DATABASE_CHANGE입니다. review_token은 계정/요청 검토 상태의 해시이며 API 키가 아닙니다. 오류면 재실행·잠금 해제·새 주문 없이 그 상태를 알려주세요.

이번 안내에는 --apply-review 또는 --acknowledge-unresolved-history를 사용하지 않습니다. 기본 검토는 lease도 획득하지 않고 DB를 변경하지 않습니다. 거래소 GET 조회만 수행합니다. 과거 UNKNOWN/SUBMITTING 요청을 성공이나 실패로 바꾸지 않습니다.

추후 명시적 적용 기능은 검토 토큰과 별도 작업 ID, 미확정 이력을 인지한다는 운영자 확인을 모두 요구합니다. 적용 시에도 새 주문은 없고 paused=true, reconciled=false, stream_ready=false를 유지합니다. 과거 주문/요청/이벤트/손실 기준/최고 평가액을 보존하고 감사 이벤트를 추가합니다. 손실/낙폭/주간 잠금, 날짜 경계 미검증, 잔여 포지션/주문, 조회 실패나 상태 변화가 있으면 거부합니다. 실제 적용은 이번 단계에 포함하지 않습니다.

API/web PAPER, LIVE_TRADING_ENABLED=false 유지. 실거래 금지, Phase 5 미시작. DB volume 삭제, down -v, 새 ledger로 잠금 우회 금지. 실제 .env와 키는 ZIP에 포함하지 않습니다.
