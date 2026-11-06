# 거래소 시각에 맞춰 이벤트를 검사하기

직전 작업05ad78fc-e803-4092-be92-54cbe2d833bf는 진입0.0007·SL/TP·청산을 확인했지만 ALGO_UPDATE의 수신/처리 나이가-6ms로 기록돼 EVENT_TIME_INVALID로 실패했습니다. 이전30초 초과와 다른 경우입니다. 진입 준비는21.931초, 수신5건이었습니다. 이벤트 시각을 바꾸거나 단순히 미래 허용 오차를 추가하지 않습니다.

HTTP 서명에는 검증된 거래소 시각 보정값을 사용하지만 기존 이벤트는 PC 시각과 직접 비교하고 있었습니다. 이 버전은 같은 검증을 통과한 서버 시각 표본과 왕복 지연 구간을 이벤트에도 사용합니다. SDK가 기존에 허용하던 왕복1초·중간 보정값±1초·유효기간60초와30초 이벤트 신선도는 유지합니다. 실제 Windows에서 다시 검증해야 합니다.

## 1. 기존 Galaxybook의 새 폴더에 설치

ZIP을 windows-demo-event-clock-fix 폴더에 풀고 열어 주소창에 cmd를 입력합니다. CMD 경로가 새 폴더로 끝나는지 확인합니다.

```cmd
docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait
```

기존 hybrid-windows 프로젝트/DB/export 볼륨과 적용한 일별·주별 기준을 유지합니다. 초기화/down -v/export 재요청/기준 재적용을 하지 않습니다. API 컨테이너가 교체되면 메모리 키가 사라지므로 이번 설치 뒤 한 번 등록합니다.

```cmd
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -m scripts.demo_key_session --register
```

DEMO 키와 Secret은 숨김 입력합니다. REGISTERED_IN_PRIVATE_TMPFS 뒤에는 재입력이 없습니다.

## 2. 먼저 주문 없는 복구 검토

```cmd
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -T api python -m scripts.review_demo_recovery
```

결과 화면만 공유합니다. 이 기본 검토는 DB/거래소 조회만 하고 기준 적용·잠금 해제·주문을 하지 않습니다. 새로운 검토 토큰을 확인한 뒤 별도의 복구 승인과 새 최소 수량 DEMO 검증을 진행합니다. 실패한 기존 주문을 새 ID로 반복하거나 예전 검토 토큰을 사용하지 않습니다.

## 시각 검사 방식

기존 /fapi/v1/time 조회의 PC 송신 시각 b, 수신 시각 a, 서버 응답 시각 s를 그대로 추가 보관합니다. 원래 SDK 검증을 통과한 표본만 사용하고, 서명·주문 HTTP 계약/호스트/상한은 바꾸지 않습니다. 새 네트워크 조회도 추가하지 않습니다.

그 표본으로 가능한 서버 시각의 하한과 상한을 계산합니다. 이벤트 E가 확인된 상한보다 앞선 미래이면 즉시 거부합니다. 상한 기준으로30초보다 오래됐어도 즉시 거부합니다. E가 구간 안에 있지만 하한이 아직 E에 못 미치면 실제 시간이 E를 따라잡을 때까지 잠깐 기다리고 다시 검사합니다. 총 대기 요청/실제 대기 예산은 기존 왕복 지연 상한1초 이내, 시도는 최대3회입니다. 네트워크/주문 재전송은 없습니다.

검사가 끝날 때는 가능한 서버 시각 전체에 대해 E가 미래가 아니고 가장 불리한 나이도30초 이하여야 합니다. 단순히±몇 ms를 더해서 통과시키지 않습니다. 시계가 따라오지 않거나 표본이 만료/역행/신뢰 범위 밖이면 실패합니다. 원래 E와 원장 평가/기간 시각은 수정하지 않습니다. 검사를 끝내지 못한 수신 작업이 취소되면 정상 종료로 오인하지 않고 실패를 남깁니다.

## 진단 값

private_session_diagnostic에 다음 값이 추가됩니다.

- event_clock_offset_ms: 검증된 표본의 거래소-PC 중간 보정값
- event_clock_rtt_ms: 그 표본의 왕복 지연
- event_clock_wait_ms: 수신/처리에서 요청한 시각 대기 합계(실제 벽시계 경과 시간과는 다를 수 있음)
- event_local_age_ms: 보정하지 않은 PC 기준 나이

검사 시 event_age_ms는 가능한 거래소 시각 상한에서 계산한 보수적인 나이입니다. event_receive_age_ms는 수신 검사 통과 후의 나이이며, 수신 검사 실패 시 기존 사전 PC 표본이 남을 수 있으므로 오류 분류와 추가 시각 값을 함께 봅니다. 진단 값의 나이는 기존 제한 범위로 잘라 표시하며 실제 판정에는 자르지 않은 원래 계산값을 사용합니다. 원본 이벤트/키/URL/예외 본문을 새로 저장/출력하지 않습니다.

EVENT_CLOCK_UNTRUSTED는 표본/대기 검증 실패, EVENT_CLOCK_INCOMPLETE는 수신 시각 검사 중 취소를 뜻합니다. 확인된 범위를 벗어난 미래나 오래된 이벤트는 계속 EVENT_TIME_INVALID입니다. PC 시계 변경이나 별도 관리자 권한 작업을 자동 실행하지 않습니다.

기존 수신/REST 분리·유효 알림 통합·진입 직전 차단·worker 대기·키 재사용·기간 기준 수정은 포함합니다. 위험/수량/SL/TP·비상 청산·계정 스냅샷 대조·원장/기준/잠금·Docker/Compose·export는 바꾸지 않았습니다. 이번 설치나 검토 자체는 자동 매매를 시작하지 않으며 Phase4 전체 완료·LIVE/Phase5 전환도 아닙니다.
