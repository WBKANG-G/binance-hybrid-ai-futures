# 11월3일 운영 복구 및 일간 후보 검토 근거

근거는 사용자 CMD 출력과 이번 직접 답변이다. 앱/위험 정책/기준금/키를 변경하는 구현 작업이 아니라 운영 상태와 다음 읽기 전용 단계의 기록이다.

## 실제 운영 컨테이너 상태

처음 docker exec는 hybrid-windows-api-1이 존재하지만 실행 중이 아니어서 조회를 시작하지 못했다. 후속 docker ps에서 alerts/api/key-session/web/db5개가 모두 Exited(255), 약20분 전에 종료된 것으로 보였다. 같은 시점 종료만으로 Docker/WSL/호스트 중단 원인을 확정하지 않는다.

사용자는 기존 Compose로 start --wait를 실행했고 key-session/db/api/alerts/web5개 모두 Healthy가 됐다. 재빌드/컨테이너 삭제/복구 승인 또는 신규 거래 명령을 사용하지 않았다. 이 운영 재기동을 보호 포지션에서의 실제 장애 수락으로 확대하지 않는다.

## 실제 로컬 기준일 조회

- recovery LOCAL_PERIOD_CHECK_NO_DATABASE_CHANGE.
- period_keys_match false.
- stored_day_kst2026-11-02 / current_day_kst2026-11-03.
- stored_week_kst2026-11-02 / current_week_kst2026-11-02.
- risk_state NORMAL / paused true.
- exchange_checked/orders_enabled/live_trading_enabled false.

이는 API 컨테이너 현재 시각과 저장 정책으로 계산한 로컬 결과다. 거래소 현재 잔고/flat 상태나 새 일간 후보를 조회한 결과가 아니며, 새로운 잔고·기준금 숫자를 추정하지 않는다. 어제 두 기간 적용은 이력으로 보존하고 이번 주간 기준금과 고점을 변경하지 않는다.

## 이번 새 활동 확인

질문은11월2일 기준금 적용 이후 안내한 명령 외 수동 거래·다른 봇/종목 거래·입출금·이체·보너스 수령 여부였다. 사용자 답변은 '없음'이다. 이전 대화의 답변을 새 기간에 자동 재사용한 것이 아니다. 계좌 전체 거래소 export 검증으로 확대하지 않고 실제 signed USDT/현재 flat/알려진 종료·체결·소득 검사를 별도로 요구한다.

## 다음 명령과 구현 확인

기존 NORMAL/paused 전용 일간 CLI의 미리보기만 사용한다. 현재 주간 기준일이 일치해야 하고 기존 last_reconciled/평가 앵커를 임의로 갱신하지 않는다. 이전 일간 키가 현재보다 과거이고 앵커가 자정 경계 이전이면 후보 검토를 할 수 있으며, 앵커의 날짜와 이전 일간 키를 억지로 같게 바꾸지 않는다. 어제의 기간 영수증도 그대로 둔다.

```bat
docker exec -it hybrid-windows-api-1 python -m scripts.review_demo_paused_day --attest-no-other-trades-since-exit
```

유효한 DEMO API 키 RAM 설정은 재사용한다. 없거나 만료됐으면 숨김 입력하며 네이버 비밀번호가 아니다. 후보 조회는 GET뿐이며40초/600 signed GET/6 clock probe 예산과 원래 서명·이력·손실 한도를 적용한다. 장부/리더/펜스/기준금/금융 감사 기록을 변경하지 않고 조회 전후 장부가 같은지 확인한다. source는 OPERATOR_ATTESTED_FLAT_WITH_SIGNED_USDT_EVIDENCE다.

정상은 PAUSED_NORMAL_DAY_PREVIEW_NO_DATABASE_CHANGE이며 전체 후보 JSON을 확인한다. 정확한 검토 토큰을 사용하기 위해 가능하면 캡처 대신 JSON 텍스트를 공유한다. --apply-review/--accept-operator-evidence/--operation-id는 이번 단계에 지정하지 않는다. BLOCKED는 그대로 공유하고 반복/수동 초기화/이전 토큰 적용을 하지 않는다.

현재 새 일간 후보와 실제 적용은 아직 확인 전이다. 이 기록 작업에서 앱을 수정하거나 새 테스트를 실행하지 않았다. 기존 일간 검토/기간 검증 결과와 제한 사항은 보존한다. 신규 진입/LIVE/Phase5 및 Phase4 전체 완료를 승인하지 않는다.
