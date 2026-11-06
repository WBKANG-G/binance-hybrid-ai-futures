# 기존 TP 이력의 남은 오래된 4건 조회

앞선 실제 결과는 조회 대상 16건 중 최신 12건을 확인했고 TP 실제 체결 증거 0건, 오래된 4건은 미조회였습니다. 이 도구는 그 페이지의 마지막 작업 참조 `1178ceaaba787357` 다음부터 읽습니다.

현재 전체 대상이 정확히 16건, 해당 작업까지 정확히 12건이어야 진행합니다. 대상 수나 기준 작업의 위치가 달라졌거나 기준 작업이 없거나 모호하면 거래소 조회 전에 차단하며 자동으로 첫 페이지나 다른 범위를 조회하지 않습니다. 맞는 경우 오래된 4건만 조회합니다.

## 실행

Windows 보안 설정은 그대로 두세요. `.cmd`, `.bat`, `.ps1`, `.exe` 파일이 없습니다. 바탕화면의 새 폴더에 압축을 풀고 CMD에서 실행합니다.

```cmd
cd /d "%USERPROFILE%\Desktop\windows-demo-tp-history-tail-review"
```

```cmd
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; if((Get-FileHash -Algorithm SHA256 -LiteralPath 'review_tp_history.py').Hash.ToLower() -ne '1c049fb1015fec69a4feb93b7eedca0d0ec450da3b78623588131768b9088953'){throw 'TP_HISTORY_READER_HASH_MISMATCH'}" && docker exec -i hybrid-windows-api-1 python - --review-only --after-operation-ref 1178ceaaba787357 --expected-total 16 --expected-skipped 12 < review_tp_history.py
```

완료 화면 전체를 보내주세요. 오류나 차단이면 재실행하지 마세요.

## 유지되는 제한과 해석

한 번에 최대 12건·서명 GET 최대 36회, 기존 40초 조회 예산과 시각 탐색 제한, TLS/프록시/리다이렉트 금지 정책을 유지합니다. 이번 조건에 맞는 4건은 각 TP/SL 2회와 TP FINISHED 시 실제 체결 주문 추가 1회로 최대 12개 서명 조회 대상입니다. 초기 준비와 진행 중인 제한된 SDK 호출 시간은 추가될 수 있습니다.

등록된 비공개 RAM DEMO 키를 내부에서 읽어 서명 조회에 사용하지만 키·비밀번호·서명 URL·실제 서버 주문 ID는 출력하지 않습니다. 새 키를 대화에 입력하지 마세요. 로컬 상태는 SELECT 전용 트랜잭션으로 읽고 NORMAL/일시정지·리더/잔여 노출 없음 및 조회 전후 전체 계좌 상태·owner/fence/lease 동일함을 확인합니다. 신규/취소/청산/보호 주문, 복구, lease 획득, DB 변경, 메일, 컨테이너 재시작·재빌드는 없습니다.

`eligible_owned_orders=16`, `skipped_orders=12`, `selected_orders=4`, `remaining_after_page=0`이 예상됩니다. `covers_all_eligible_in_this_run=false`는 이번 실행이 오래된 4건만 조회했다는 뜻입니다. `coverage_truncated=false`는 그 다음으로 더 오래된 대상이 없다는 뜻이며 앞선 12건까지 이번 실행에서 다시 조회했다는 의미가 아닙니다.

원래 TP/SL 정확한 ID/보호 설정, TP FINISHED와 양수 actualOrderId, 일치하는 실제 FILLED/reduceOnly 체결 주문·수량·가격을 확인합니다. NOT_FOUND는 미전송·미체결 증거가 아닙니다. 현재 SL 상태만으로 과거 TP 체결 후 SL 삭제 순서를 증명하지 않으며 `t06_ordering_verified=false`를 유지합니다. 새 거래소 전체 빈 계좌 검사는 하지 않습니다.

오류나 차단이면 재실행하지 않고 결과를 보존하세요. 이 보고서로 주문·복구·진입 재개를 실행하지 않습니다. 기존 첫 페이지 결과와 성공한 SIGKILL/복구 기록은 그대로 보관하세요.
