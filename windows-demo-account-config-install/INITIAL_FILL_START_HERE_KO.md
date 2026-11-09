# 최초 체결 반영 지연의 제한된 검증과 불일치 진단

직전 실행은 FINISHED_FLAT/CLOSED로 종료됐지만 SL·TP 확인은 false였고 세션 오류가 있었습니다. 감사 기록은 보호 요청 전에 SNAPSHOT_CHANGED가 발생해 비상 청산했음을 보여줍니다. 최초 두 응답의 구체적인 필드 값은 이전 버전에 보존되지 않았으므로 실제 원래 차이를 확정한 것은 아닙니다. 이 버전은 정상 최초 체결 지연의 제한된 검증과 불일치 필드 이름 진단을 추가합니다.

## 1. 기존 Galaxybook PC의 새 폴더에 설치

ZIP을 풀고 windows-demo-initial-fill-fix 폴더를 열어 주소창에 cmd를 입력하세요. CMD 경로가 새 폴더로 끝나는지 확인한 뒤 실행합니다.

```cmd
docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait
```

프로젝트명 hybrid-windows와 기존 PostgreSQL/demo_export_data 볼륨을 유지합니다. 초기화, down -v, export 재요청, 과거 일별 기준 재적용은 하지 않습니다. 이전 worker 대기·운영 조회·시각/기준 수정은 모두 포함합니다.

## 2. 주문 없는 복구 검토

```cmd
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -m scripts.review_demo_recovery
```

DEMO 키는 숨김 입력하고 결과만 공유하세요. 키/Secret을 채팅에 보내지 않습니다. 이 명령은 기본 조회 전용이며 기준 변경·복구 승인·신규 주문을 하지 않습니다. 결과 확인 뒤 필요하면 별도 토큰으로 복구를 승인하고 새 DEMO 검증을 진행합니다. 이 ZIP 적용 자체는 잠금을 해제하지 않습니다.

## 제한된 최초 체결 처리

원래 coherent_snapshot의 엄격한 두 응답 비교는 그대로입니다. 그 비교가 달랐을 때만, 쓰기 활성인 ISOLATED BTCUSDT의 최소 수량 DEMO_VALIDATION 주문 한 건에 대해 제한된 경로를 사용할 수 있습니다. 로컬은 체결0/ENTRY_PENDING이며 다른 노출·활성 의도가 없어야 하고, 기존 수량 cap/명목금액100 USDT 이하여야 합니다. 일반 전략, 이미 열린 포지션의 변경, 다중·불명확한 체결에는 적용하지 않습니다.

추가 스냅샷은 딱1번입니다. 두 번째·세 번째 응답은 원래 coherent_snapshot을 통과해야 합니다. 원래 주문 ID의 signed userTrades 한 건을 조회해 서버 주문ID·방향·수량·가격·USDT 수수료·최근 시각·실현손익0을 대조합니다. 현재 현금은 진입 전 평가액에서 해당 수수료를 뺀 금액과 정확히 같아야 합니다. 수수료도 기존 정책의 예상 상한 이내여야 합니다. 동일 주문의 실제 초기 체결/수수료 변화가 관측되어야 하며 단순 해시 변화는 허용하지 않습니다.

다른 주문·활성 보호·입출금·설정/레버리지 변화나 양의 포지션이 유지되는 동안의 별도 격리 담보 변화는 차단합니다. 처음 체결에 따른 포지션0→예약 수량 이내 포지션과 최초 담보 배정만 구분합니다. 다른 반영 지연/복수 체결은 이 경로에서 임의로 추정하지 않고 차단합니다. 최초·후속 조회의 가장 불리한 평가/가용잔액/증거금/청산가와 고점 관측을 보존하고 원래 위험·보호 검사로 이어갑니다. 새 진입이나 주문 재전송은 하지 않습니다. 근거가 부족하면 기존 비상 청산을 유지합니다.

## 감사/출력

initial_fill_settlements에는 해당 작업의 근거 해시가 기록됩니다. initial_fill_settled는 현재 작업에서 그 검증이 있었는지 표시합니다. last_reconciliation_mismatch_fields는 마지막 불일치의 필드 이름만, reconciliation_settle_reason은 마지막 추가 검증의 허용된 결과 분류만 표시합니다. 과거 필드 이름이 남아 있을 수 있으므로 현재 위험 상태와 현재 작업 플래그를 함께 봅니다. 원본 응답/키/서명/URL은 출력하지 않습니다.

세션 오류가 finally 정리 중 취소에 가려져도 전체 검증을 성공으로 처리하지 않도록 실패 여부를 보존합니다. 정상적인 관찰 종료 취소와 오류 종료를 구분합니다.

정책·시세/계정 신선도·원래 두 조회 비교·lease/fencing·주문 수량과 가격·SL/TP 규칙은 그대로입니다. 이 단계는 실제 새 자동 진입·보호·청산 성공 확인이나 Phase4 전체 완료를 뜻하지 않으며, LIVE/Phase5를 시작하지 않습니다.
