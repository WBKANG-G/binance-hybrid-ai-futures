# 자정 전 청산·자정 후 대조에 대한 일별 기준 검토

이 절차는 기존 사용자 확인(해당 청산 이후 별도 수동/봇 거래·입출금 없음)과 서명된 조회를 결합합니다. 거래소 계정 전체 원본을 검증한 것으로 표시하지 않습니다. 미리보기만으로 DB의 기준이나 잠금을 바꾸지 않습니다.

## 1. 같은 Galaxybook PC의 새 폴더에 설치

ZIP을 풀고 windows-demo-exit-day-review 폴더를 엽니다. 주소창에 cmd를 입력하세요. CMD 경로가 새 폴더로 끝나는지 확인한 뒤 실행합니다.

```cmd
docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait
```

프로젝트명 hybrid-windows와 기존 PostgreSQL/demo_export_data 볼륨을 유지합니다. 초기화, down -v, 새 계정 생성, export 생성 재시도는 하지 않습니다. 이번 수정은 이전 청산 확인 수정도 포함합니다.

## 2. DB 무변경 미리보기

청산 이후 별도 거래나 입출금이 없다는 확인이 여전히 맞을 때만 아래 명령을 실행합니다. 추가 활동이 있었다면 그대로 알려주세요.

```cmd
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -m scripts.review_demo_exit_day --attest-no-activity-since-exit
```

DEMO 키와 Secret은 숨김 입력합니다. 채팅에 보내지 않습니다. 결과를 공유해 주세요. 성공 표시는 EXIT_ANCHORED_DAY_PREVIEW_NO_DATABASE_CHANGE입니다. 이 단계는 lease 획득이나 DB 변경 없이 GET만 수행합니다.

출력의 source는 OPERATOR_ATTESTED_PRE_MIDNIGHT_EXIT_WITH_SIGNED_USDT_EVIDENCE이며 account_wide_exchange_export_verified=false입니다. 현재 일자의 자정 이전에 실제 청산이 끝났는지, 원래 진입·청산 수량과 서명된 체결 기록이 맞는지, 청산의 마지막 밀리초에 해당 수수료·실현손익만 있는지 확인합니다. 그 이후 BTC 거래와 USDT income이 하나라도 있으면 차단합니다(합계0인 추가 기록도 차단). 현재 전체 포지션/활성 주문0, USDT 자산·계정 총액·저장된 관측 평가액 일치, 미실현손익0, 단일 자산/격리 BTCUSDT 2배 및 기존 손실·낙폭 한도도 확인합니다. 다른 자산은 더하지 않습니다.

## 3. 기준 저장은 미리보기 확인 후 별도

미리보기는 제안일자·금액·청산 시각·자정·실제 관측 시각·근거 해시·토큰을 출력합니다. 실제 저장에는 --accept-operator-evidence, --apply-review 토큰, 고정 --operation-id와 명시적 활동 없음 확인이 함께 필요합니다. 값을 임의로 만들거나 예전 토큰을 쓰지 않습니다.

적용 시에도 원래 일별 일자/기준과 근거 출처를 별도 receipt에 저장합니다. daily_key/daily_base만 새 기준으로 채택하고 주별 기준·고점·원래 value.as_of/last_reconciled·과거 주문/요청·이전 정정/복구/기준 receipt·EMERGENCY/paused를 보존합니다. 실제 관측 시각을 자정이나 청산 시각으로 바꾸지 않습니다. 신규 진입, 복구 승인이나 잠금 해제는 수행하지 않습니다.

## 이번 사용자 결과에 대한 범위

서명된 청산 체결은 2026-10-23T14:58:56.723Z(한국시간 23:58:56.723)이며 실제 로컬 완료 대조는 2026-10-23T15:01:54.714751Z(다음날 00:01:54.714751)입니다. 직전 로컬 EXIT_PENDING 표시는 실제 자정 포지션 존재의 증거가 아닙니다. 저장된 평가액은 4999.74584261 USDT이지만, 이를 새 기준으로 사용할 수 있는지는 이번 미리보기의 최신 서명 조회와 사용자 확인 조건을 모두 통과해야 정해집니다.

이 경로는 자정 후 실제 청산, 추가 활동, 입출금/주간 기준/고점 별도 정리, 현재 위험 기준 위반, 7일을 넘는 BTC 체결 조회 범위 등을 지원하지 않고 차단합니다. 사용자 확인으로 알 수 없는 다른 계정 활동을 거래소 원본으로 배제하지 못합니다. LIVE/Phase5 진행 및 수정된 자동 청산의 실제 DEMO 재검증은 별도 단계입니다.
