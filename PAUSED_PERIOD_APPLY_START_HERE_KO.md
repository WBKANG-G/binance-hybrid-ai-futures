# 검토된 두 기준금 적용 — 적용 후에도 일시정지 유지

이번 명령은 로컬 일간·주간 기준금과 출처 영수증/감사 기록을 실제로 변경한다. 대상은 사용자에게 실제 제시된11월2일 후보다. NORMAL/paused·고점·손익/이전 평가 시각·주문/요청·미해결 기록은 보존한다. 거래소에는 읽기 전용 GET만 보내고 주문·취소·청산·진입 재개는 하지 않는다.

| 항목 | 적용 전 | 검토된 적용 후보 |
|---|---|---|
| 일간 기준일 | 2026-11-01 | 2026-11-02 |
| 주간 기준일 | 2026-10-26 | 2026-11-02 |
| 일간 기준금 | 4999.03492670 | 4998.99761493 USDT |
| 주간 기준금 | 4999.64804199 | 4998.99761493 USDT |

기준금의 출처는 OPERATOR_ATTESTED_FLAT_PERIODS_WITH_SIGNED_USDT_EVIDENCE이며 계좌 전체 export 검증이나 자정 직접 관측이 아님을 --accept-operator-evidence로 명시적으로 수락한다. 적용 직전 전체 서명 검토와 토큰 일치를 다시 확인한다.

## 1. 설치

ZIP을 새 windows-demo-paused-period-apply 폴더에 풀고 해당 폴더 탐색기 주소창에서 cmd를 실행한다. 같은 hybrid-windows 프로젝트와 DB/RAM 키·메일/export 볼륨을 유지한다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait
```

모든 컨테이너 Healthy 확인 후 아래 적용 명령 한 건을 실행한다. 기존 DEMO API 키 RAM 설정을 재사용하고 없으면 DEMO API Key/Secret을 숨김 입력한다. 네이버 앱 비밀번호가 아니며 키 값을 채팅에 보내지 않는다.

## 2. 검토 토큰과 새 작업으로 적용

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -m scripts.review_demo_paused_periods --attest-no-activity-since-anchor --accept-operator-evidence --apply-review bfcf38c1998320344e9c03411b83a9369b152bfca76f50fb23438fc89cdf7a1c --operation-id 7230ec92-b17e-4fa9-bfef-336801a2182e
```

정상 결과는 period_baselines OPERATOR_ATTESTED_PERIOD_BASELINES_ADOPTED_ENTRIES_STILL_PAUSED다. adopted/current day/week, baseline, source, risk_state NORMAL/paused true, high_water_preserved, old_valuation_timestamp_preserved, unresolved_requests_acknowledged false, orders_enabled/live_trading_enabled false, leader_released true를 포함한 전체 JSON을 공유한다. 적용 후에도 신규 진입을 자동 재개하지 않는다.

BLOCKED이면 새 토큰/작업 ID를 만들거나 기준금을 직접 고치지 말고 오류 JSON 그대로 공유한다. 날짜·잔고·이력·장부가 바뀌면 이전 토큰을 강제로 적용하지 않는다. 리더 만료나 감사 쓰기 실패도 두 기준금과 영수증을 함께 롤백한다.

이미 같은 작업/토큰의 영수증이 있으면 ALREADY_RECORDED_NO_ACTION, already_recorded_read_only true로 응답한다. 이 경우 키 입력/새 리더 획득/네트워크/DB 쓰기 없이 기록을 확인한다. adopted_*는 당시 적용한 영수증이고 current_*는 현재 장부라 이후 기간 변경과 구분한다. 정상 성공 후 명령을 반복할 필요는 없다.

이전 PAUSED_PERIOD_START_HERE_KO.md는 부모 미리보기 전용 버전의 당시 범위다. 이번 추가는 별도 명시적 적용 옵션이며 기본 실행은 계속 읽기 전용 미리보기다. main/LIVE/Phase5 및 Phase4 전체 완료는 변경하지 않는다.
