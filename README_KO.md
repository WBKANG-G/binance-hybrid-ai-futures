# 수동 청산 이후 일간 기준값 미리보기

사용자가 마지막 수동 청산 이후 추가 거래·입출금이 없다고 확인한 경우에만 사용합니다. 이번 대화의 “없음” 확인에 대응하는 명시적 실행 인자가 포함되어 있습니다.

기존 일간 미리보기는 애플리케이션 청산 주문을 요구합니다. 이번 사건은 외부 수동 청산을 장부에 채택했으므로 그 경로로 처리할 수 없습니다. 이 도구는 이미 채택된 영수증과 서명 조회를 결합해 새 일간 기준값 후보만 출력합니다. 적용 기능과 검토 토큰은 제공하지 않습니다.

## 실행

ZIP을 바탕화면의 `windows-demo-tp-manual-close-day-preview` 폴더에 풀고 CMD에서 아래 두 줄을 차례로 실행합니다. Python 파일만 컨테이너 표준 입력으로 전달합니다. 재빌드는 필요하지 않습니다.

```cmd
cd /d "%USERPROFILE%\Desktop\windows-demo-tp-manual-close-day-preview"
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; if((Get-FileHash -Algorithm SHA256 -LiteralPath 'preview_manual_close_day.py').Hash.ToLower() -ne '60831226a621d7445b11638c8d3adb5a120b62e05ce1dc3bd90ba72ace3a976b'){throw 'DAY_PREVIEW_HASH_MISMATCH'}" && docker exec -i hybrid-windows-api-1 python - --attest-no-activity-since-manual-close < preview_manual_close_day.py
```

성공 시 `manual_close_day_preview=OPERATOR_ATTESTED_PREVIEW_NO_DATABASE_CHANGE`와 `proposed_daily_base_usdt`, `old_daily_base_usdt`, `proposed_day_kst`가 출력됩니다. 결과 JSON을 공유해 실제 값 검토를 이어갑니다. 차단 결과가 나오면 반복 실행하지 않고 `stop_reason`을 공유합니다.

## 증거와 범위

- 배포 참조 e9327bd의 29개 소스를 먼저 검증합니다.
- DB는 읽기 전용 트랜잭션으로 조회합니다. 리더를 획득하지 않습니다.
- 사건 `041d1f35-870e-46cb-aad2-ed6059bc1095`의 수동 청산 영수증, CLOSED/수량0, NORMAL/일시정지 상태를 요구합니다.
- 진입 28668115919와 수동 청산 28668119491, 체결 554837511/554838531을 서명 조회로 다시 확인합니다. 청산 수수료는 .02119185 USDT이며 총 순손익은 -.07032487 USDT입니다.
- 채택 시 금융 스냅샷 시각(value.as_of)을 기준으로 삼습니다. 오래된 last_reconciled를 새로운 시각으로 바꾸지 않습니다. 영수증 기록 시각과 스냅샷 시각 간 간격도 검증합니다.
- 전역 현재 포지션·일반 주문·알고 주문이 비어 있고 USDT 잔액이 채택 시 장부 자산과 같아야 합니다.
- BTC 체결 내역은 원래 두 체결만 있어야 합니다. 금융 스냅샷 이후 전역 income 내역은 없어야 합니다. 두 차례 조회하며 꽉 찬 1000행은 불완전한 범위로 차단합니다.
- KST 일자가 넘어갔고 주간 기준은 같은 주여야 합니다. 기존 일간·주간 손실 및 최대 낙폭 기준부터 검사하므로 손실 한도를 초기화하는 수단으로 쓸 수 없습니다.
- 사용자 확인에 의존하는 후보입니다. 정확한 자정 평가액이나 계정 전체 거래 내역을 독립적으로 입증했다고 표시하지 않습니다. `exact_midnight_valuation_verified=false`, `account_wide_trade_export_verified=false`입니다.

DB·기준값·주간 기준·고점·기존 평가 시각·요청 이력·수동 청산 영수증을 변경하지 않습니다. 원래 미해결 요청도 보존됩니다. 주문을 만들거나 취소하지 않습니다. 주문과 LIVE는 계속 비활성화 상태입니다.

## 제한 및 검증

GET 허용 경로만 사용하며 75초 요청 시작 예산, HTTP 최대48회, 시계 조회 최대10회로 제한합니다. 현재 거래소 공개 분당 한도와 weight 헤더를 확인하고 60% 임계치 이전에 여유분을 확보합니다. 418/429, 헤더 누락, 노출·주문·추가 체결·income·잔액 변화는 재시도 없이 차단합니다. 키는 기존 임시 세션에서만 읽고 출력하거나 포함하지 않습니다.

클라우드에서는 합성 서명 응답과 격리 PostgreSQL로 42개 테스트 및 Ruff 검사를 통과했습니다. Windows 실제 결과는 아직 확인되지 않았습니다. 별도 테스트 파일은 개발 검증용이며 Windows 실행에 필요하지 않습니다.

소스 참조와 검증 내역은 SOURCE_REFERENCE.json, VALIDATION.json, test-results/pytest-day-preview.xml에 있습니다. SHA256SUMS.txt와 TRANSFER_MANIFEST.json은 파일 무결성 확인용입니다.
