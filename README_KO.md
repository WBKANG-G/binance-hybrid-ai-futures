# 수동 청산 이후 검토한 일간 기준값 채택

앞선 실제 미리보기 결과와 사용자의 추가 거래·입출금 없음 확인에만 대응하는 일회성 도구입니다. 사용자 요청 “다음 단계 진행해”에 따라 준비했습니다. 이번 단계는 같은 DEMO 검증 작업의 일간 기준 반영이며, 주문 재개나 LIVE 전환이 아닙니다.

## 고정된 적용값

| 항목 | 값 |
|---|---:|
| 이전 일간 날짜 | 2026-11-06 KST |
| 적용할 날짜 | 2026-11-07 KST |
| 이전 일간 기준 | 4998.95579884 USDT |
| 적용할 일간 기준 | 4998.74183608 USDT |
| 유지할 주간 날짜 | 2026-11-02 KST |
| 유지할 주간 기준 | 4998.99761493 USDT |
| 유지할 고점 | 5000 USDT |
| 수동 청산 채택 스냅샷 | 2026-11-06T07:25:25.109073+00:00 |

날짜·금액·스냅샷 시각이 다르면 적용하지 않습니다. 다음 날이나 주간 변경에는 사용할 수 없습니다.

## 실행

바탕화면 `windows-demo-tp-manual-close-day-apply` 폴더에 ZIP을 풀고 CMD에서 아래 두 줄을 실행합니다. 기존 컨테이너에 Python 소스만 전달하므로 재빌드가 필요하지 않습니다.

```cmd
cd /d "%USERPROFILE%\Desktop\windows-demo-tp-manual-close-day-apply"
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; if((Get-FileHash -Algorithm SHA256 -LiteralPath 'adopt_manual_close_day.py').Hash.ToLower() -ne 'a65368a2740268f363914e394e19ad774c6b57a6bdb5239074d7c99ecee01698'){throw 'DAY_ADOPTION_HASH_MISMATCH'}" && docker exec -i hybrid-windows-api-1 python - --apply --attest-no-activity-since-manual-close < adopt_manual_close_day.py
```

성공 출력은 `manual_close_day_adoption=OPERATOR_ATTESTED_DAY_ADOPTED_ENTRIES_STILL_PAUSED`입니다. `day_kst=2026-11-07`, `daily_base_usdt=4998.74183608`, `leader_released=true`, `paused=true`, `order_writes=0`을 함께 확인합니다. 이미 동일한 영수증으로 완료된 경우 `ALREADY_RECORDED_NO_ACTION`으로 변경 없이 종료합니다.

차단이나 오류가 나오면 자동 재시도하지 말고 결과 JSON을 공유합니다. `application_may_have_completed=true`는 커밋 여부가 불확실할 수 있다는 보수적 표시이며 성공 판정이 아닙니다. 적용 이후 출력/정리에 실패한 경우에도 기존 영수증을 조회해 중복 적용을 막습니다.

## 반영 전 검증과 원자성

- 배포 e9327bd의 소스 29개를 검증하고, 기존 임시 DEMO 키만 읽습니다. 키와 원시 오류는 출력하지 않습니다.
- 전역 현재 포지션·일반 주문·알고 주문, 계정 모드, USDT 잔액을 두 차례 확인합니다.
- 원래 진입·수동 청산 주문과 체결 두 건을 다시 조회합니다. 스냅샷 이후 전역 income이 없어야 하고, 체결·income 결과가 1000행이면 불완전한 범위로 차단합니다.
- 기존 일간·주간 손실과 최대 낙폭 한도를 먼저 검사합니다. 기존 위험 상태가 NORMAL이고 일시정지되어 있어야 합니다. 잠금을 해제하는 경로는 없습니다.
- 기존 DemoStore의 리더 획득·fence·만료 검사를 사용합니다. 증거 조회부터 일간 기준·영수증·감사 이벤트 커밋까지 계정 행 잠금을 유지합니다. 다른 리더는 그 사이 장부를 변경할 수 없습니다.
- 일간 날짜/기준, 형식화된 사용자 확인 영수증, 외부 수동 청산 출처와 증거 해시를 한 트랜잭션에 반영합니다. 실패 시 그 트랜잭션은 함께 롤백합니다.
- 리더 획득·해제는 fence와 감사 이력을 추가하고 reconciled/stream_ready를 false로 유지합니다. 이 변경은 적용 실패 시에도 남을 수 있습니다. 그 외 기존 장부 필드는 보존 해시로 대조합니다.
- 주간 기준, 고점, 원래 평가 시각, last_reconciled, 요청·실패 이력, 기존 미해결 요청, 주문·포지션 기록, 수동 청산 영수증은 변경하지 않습니다.

사용자 확인을 포함한 증거를 채택합니다. 정확한 자정 평가값이나 계정 전체 거래 내역을 독립적으로 입증했다고 표시하지 않습니다. 기존 위험 정책과 account_risk는 변경하지 않습니다.

거래소에는 GET만 허용합니다. 조회 한도·시간 예산·weight 여유 검사와 418/429 중단을 유지합니다. POST/DELETE, 신규 주문, 청산·보호 주문 변경, listen key 생성은 허용하지 않습니다. 주문과 LIVE는 계속 비활성화 상태입니다.

## 검증 범위

격리 PostgreSQL과 합성 거래소 응답에서 65개 테스트 및 Ruff 검사를 통과했습니다. 적용 성공, 부수 필드 보존, 트랜잭션 롤백, 경쟁 리더 차단, 오래된 증거·만료 리더 차단, 조회 제한, 영수증 충돌, 커밋 후 후검사 실패, 중복 실행의 무변경 종료를 검증했습니다. 현재 Windows DB에는 아직 적용되지 않았습니다.

SOURCE_REFERENCE.json과 VALIDATION.json, test-results/pytest-day-apply.xml에 출처 및 검사 결과가 있습니다. 파일 검증용 SHA256SUMS.txt와 TRANSFER_MANIFEST.json을 포함합니다. 개발용 테스트 파일은 Windows 실행에 필요하지 않습니다.
