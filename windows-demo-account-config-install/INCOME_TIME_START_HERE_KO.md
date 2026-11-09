# 체결·income 시각 정밀도 차이 수정

이번 캡처의 체결 시각은 2026-10-23T14:58:56.723Z인데 income 수수료·실현손익은 2026-10-23T14:58:56.000Z였습니다. 금액은 수수료 -0.02288622 USDT, 실현손익 -0.06111000 USDT로 일치합니다. 이전 버전이 밀리초 동등성을 요구해 차단한 경우를 보완했습니다.

## 1. 기존 Galaxybook PC의 새 폴더에 설치

ZIP을 풀고 windows-demo-income-time-fix 폴더를 엽니다. 탐색기 주소창에 cmd를 입력하고 경로 끝이 새 폴더인지 확인하세요.

```cmd
docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait
```

기존 프로젝트명 hybrid-windows와 PostgreSQL/demo_export_data 볼륨을 보존합니다. 초기화, down -v, export 재요청은 하지 않습니다. 이전 자동 청산 확인 수정과 일별 검토 절차도 포함합니다.

## 2. 기존과 같은 미리보기 명령

해당 청산 이후 별도 수동/다른 봇 거래·입출금이 없다는 확인이 계속 맞을 때 실행하세요.

```cmd
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -m scripts.review_demo_exit_day --attest-no-activity-since-exit
```

DEMO 키는 숨김 입력하고 결과만 공유해 주세요. 키나 Secret을 채팅에 보내지 않습니다. 성공은 EXIT_ANCHORED_DAY_PREVIEW_NO_DATABASE_CHANGE입니다. income_time_basis=SECOND_BUCKET_OR_EXACT_FILL_TIMESTAMP와 exit_income_second_start_utc가 출력됩니다. 미리보기에는 주문 제출/취소, lease 획득, 기준 저장이나 잠금 해제가 없습니다.

## 대조 방식

마지막 실제 청산 체결이 포함된 초를 정수 연산으로 구합니다. income 시각은 그 초의 정확한 시작(예: .000) 또는 해당 검증된 청산 체결의 실제 밀리초(예: .723)만 허용합니다. +/-1초 같은 임의의 시간 허용 오차는 없습니다. 같은 초의 검증된 청산 주문 외 BTC 체결이나 그 이후 체결이 있으면 차단합니다. 같은 초의 income도 해당 청산 수수료·실현손익과 개별 금액 및 건수가 맞아야 합니다. 다른 종류/자산/심볼, 추가 금액0 기록, 상쇄 기록, 누락 기록이나 지원하지 않는 집계 형태는 계속 차단합니다.

정밀한 실제 청산 시각과 자정 이후 원래 관측 시각을 그대로 유지합니다. 적용 receipt에는 대조 방식도 남깁니다. 구버전 receipt에 이 필드가 없으면 EXACT_FILL_TIMESTAMP로 읽어 기존 출처를 바꾸지 않습니다.

## 기준 저장은 결과 확인 뒤 별도

이 경로는 사용자 확인과 서명된 BTC 체결·USDT income·현재 자산을 결합한 수동 출처 방식입니다. 계정 전체 거래소 원본 검증은 false입니다. 모든 조건을 통과했을 때만 일별 후보를 제안합니다. 실제 저장은 별도 사용자 근거 수용 옵션·토큰·고정 작업 ID를 필요로 하며, 저장 후에도 주별 기준/고점/원래 관측 시각/과거 요청·주문·receipt/EMERGENCY/paused를 보존합니다. 이전 미리보기 실패로 DB 기준은 변경되지 않았습니다.

실제 DEMO 추가 주문이나 LIVE/Phase5 진행은 하지 않습니다.
