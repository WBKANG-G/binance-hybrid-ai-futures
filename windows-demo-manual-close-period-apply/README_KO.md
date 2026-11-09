# 검토된 일자·주간 기준값 적용

2026-11-09 미리보기에서 확인한 값에만 적용되는 단일 작업입니다. READONLY 미리보기와 현재 운영자 무활동 진술을 검토한 뒤 명시적인 --apply 및 --accept-operator-evidence로 실행합니다. 거래소 주문을 만들거나 거래를 재개하지 않습니다.

## 고정된 검토 값

일자2026-11-07→2026-11-09, 주간2026-11-02→2026-11-09. 일자 기준값4998.74183608→동일, 주간 기준값4998.99761493→4998.74183608. high-water5000.00000000 보존. 두 경계는2026-11-08T15:00:00+00:00이며, 기존 평가 anchor는2026-11-06T07:25:25.109073+00:00입니다. 전체 검토 값은 REVIEWED_VALUES.json에 있습니다. 실제 서명 값이 다르면 적용하지 않습니다.

이는 정확한 자정 평가액을 거래소에서 복원한 것이 아니라, 운영자 무활동 진술과 서명된 BTC 체결·수입·USDT·현재 flat 증거를 결합한 대체 근거입니다. --accept-operator-evidence는 그 근거를 명시적으로 채택하는 옵션입니다. account-wide export 및 두 정확한 자정 값의 검증 flag는 false로 남습니다.

## 실행

압축을 바탕화면 windows-demo-manual-close-period-apply 폴더로 풀고 RUN_COMMAND.txt의 두 줄을 CMD에 입력합니다. manifest로 모든 자료를 대조할 수 있으며 실행 명령은 apply helper의 SHA256을 고정 검사합니다. 설치/재빌드는 필요 없습니다. 날짜 플래그는 현재 확인한2026-11-09 KST에 묶이며 새 적용 날짜가 다르면 중단합니다.

1. 현재 설치 소스45개와 NORMAL/paused/owner 없음/수동청산 및11월7일 채택 기록을 확인합니다. 검토 금액·날짜가 다르면 lease나 네트워크 호출 전에 중단합니다.
2.60초 lease와 증가된 fence를 획득하고 row lock이 있는 DemoStore.change 트랜잭션 안에서 새 서명 증거를 확인합니다. 모든 거래소 요청은 GET입니다. 원래 미리보기와 같은 체결·빈 수입·현재 flat·잔고·설정·기존 손실 한도 검사를 수행합니다.45초 monotonic 요청 예산, 마지막 current-state proof의 최대30초 신선도, 실제 commit 시점의 lease 유효성을 확인합니다.
3. 일자·주간 key와 base, 신규 AttestedPeriodBaseline receipt와 operation을 기록합니다. 기존 request·entry/exit·실패 trial·수동청산·11월7일 receipt·정책·risk state·pause·high-water·원래 가치평가 시각은 보존 해시로 확인합니다. lease/fence와 감사 이벤트가 기록되고, 새 reconciliation이 필요하도록 reconciled와 stream_ready는 false로 둡니다.
4. lease를 해제하고 기록을 검증합니다. 정상 결과는 OPERATOR_ATTESTED_DAY_WEEK_ADOPTED_ENTRIES_STILL_PAUSED입니다. database_changed=true는 이번 승인된 기준값·receipt·lease 감사 변경을 뜻합니다. orders_enabled/live_trading_enabled는 false입니다.

동일 작업이 이미 정확히 기록돼 있으면 ALREADY_RECORDED_NO_ACTION으로 종료합니다. 이 경로는 키나 네트워크, 추가 lease/감사 이벤트를 만들지 않습니다. 기록 일부가 없거나 원장이 바뀌었다면 충돌로 중단하며, 과거 결과를 덮어쓰지 않습니다. 새 기간을 다시 자동 채택하는 기능은 없습니다. 과거11월7일 채택 기록의 원래 해시는 이력으로 그대로 보존합니다.

## 실패 처리와 검증

BLOCKED_KEEP_PAUSED_NO_AUTOMATIC_RETRY이면 출력 결과를 보관하고 자동으로 반복 실행하지 마세요. lease_change_possible=true면 lease/fence와 안전 flag가 바뀌었을 수 있습니다. application_may_have_completed=true면 응답/cleanup 실패에도 commit이 완료됐을 가능성이 있으므로 rollback을 가정하면 안 됩니다. 재실행 인식 경로는 이 모호한 결과를 안전하게 판별하기 위해 검증했지만, 운영 단계에서는 우선 결과를 검토해야 합니다. 중단 뒤에도 거래 일시정지를 유지합니다.

적용50개와 기존 미리보기69개, 총119개가 합성 PostgreSQL/HTTPS 검사에서 통과했습니다. DB의 실제 트랜잭션/row lock/fence를 사용했고 사용자 DB/API에는 접속하지 않았습니다. 컨테이너 smoke는 network none/read-only root에서 소스45개와 모듈을 확인했으며 DB apply를 실행하지 않았습니다. JUnit, 구조 대조, 적용 범위는 test-results와 VALIDATION.json/STRUCTURE_PROOF.json에 있습니다. 생산 런타임 파일과 모델, 기존 recovery/apply 규칙은 변경하지 않았습니다.

자격 증명은 기존 private tmpfs 세션에서 읽고 새로 영구 저장하지 않습니다. 키·서명 URL·사적 원문은 출력하지 않습니다.418/429, 불완전한 이력, 현재 노출·추가 거래/수입·잔고/설정 변경·기간 변경·source mismatch는 재시도 없이 중단합니다. 마지막 요청은 timeout5초만큼45초 시작 기한 뒤 종료될 수 있으나60초 lease와 commit 검사를 통과해야 합니다.

이 적용은 TP 성공을 의미하지 않고 활성 TP 가중치 예산이나 거래 재개를 승인하지 않습니다. 적용 이후 별도의 읽기 전용 readiness 검사가 필요합니다.
