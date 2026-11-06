# 수동 청산 이력의 반복 조회 — 읽기 전용 비교

수동 청산 채택이 끝난 기록은 애플리케이션의 exit_id가 없습니다. 배포된 operational_snapshot은 이 경우 기존 앱 청산 완료 이력처럼 생략하지 않고 진입 주문과 원래 SL/TP를 다시 조회합니다. 현재 준비 상태 확인의 반복 조회 중 이 부분을 분리해 검토합니다.

이 도구는 **설치나 주문 재개 없이 프로세스 안에서만 비교**합니다. 기존 방식 snapshot 두 번과 후보 입력 방식 snapshot 두 번을 차례로 조회합니다. 사용자의 준비 상태 확인에서 보였던28회를 기준으로 합성 재현에서는22회로 줄었습니다. 실제 계정의 같은 결과는 아직 확인되지 않았습니다.

## 실행

바탕화면 `windows-demo-tp-closed-history-read-probe` 폴더에 압축을 풀고 CMD에서 다음 두 줄을 실행합니다. 기존 임시 DEMO 키를 사용하며 새 키를 입력받지 않습니다. Docker 재빌드가 필요하지 않습니다.

```cmd
cd /d "%USERPROFILE%\Desktop\windows-demo-tp-closed-history-read-probe"
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; if((Get-FileHash -Algorithm SHA256 -LiteralPath 'compare_closed_history_reads.py').Hash.ToLower() -ne 'ff570d91454f3e36d514d20b0d9a40a71e174a9ecb0db4c6af27db53548ba268'){throw 'CLOSED_HISTORY_PROBE_HASH_MISMATCH'}" && docker exec -i hybrid-windows-api-1 python - < compare_closed_history_reads.py
```

성공 출력은 `closed_history_read_probe=READ_ONLY_PAIR_COMPARISON_FINISHED_NO_INSTALL`입니다. `original_pair_signed_reads`, `candidate_pair_signed_reads`, `signed_reads_removed`를 확인해 결과 JSON을 공유합니다. 차단 시 재실행하지 말고 stop_reason을 공유합니다.

## 비교의 조건

- 배포 소스29개 해시를 DB·키 접근 전에 검증합니다.
- DB는 읽기 전용 트랜잭션으로 읽습니다. NORMAL·일시정지·로컬 노출0·기간 일치·리더 없음과 기존 수동 청산 영수증·실패 이력을 요구합니다.
- 수동 청산 원본 order와 현재 order의 차이는 실제로 채택 시 변경한 노출·보호·포지션 상태4필드만 허용합니다. 나머지 주문/신호/ID가 같아야 합니다. 원본 상태와 영수증 출처·정확한 수수료 포함 순손익·시각·증거 해시 형식도 확인합니다.
- 기존 방식의 새 REST snapshot 두 번에서 진입 FILLED/수량/가격/ID와 원래 SL/TP의 CANCELED 또는 EXPIRED 및 보호 조건을 다시 검증합니다. NOT_FOUND만으로 생략을 승인하지 않습니다.
- 후보 단계에서는 장부를 깊게 복사해 비교 입력의 주문 맵에서 해당 CLOSED 기록 한 건만 제외합니다. DB에서 기록을 지우거나 exit/FILLED 상태를 만들어 넣지 않습니다. 원본 snapshot 파서 자체도 변경하지 않습니다.
- 후보에서도 전역 포지션·일반 주문·알고 주문·잔액·증거금·설정·income을 두 번 모두 조회합니다. 현재 노출/주문이나 원래 ID 재등장이 있으면 차단합니다.
- 각 방식의 두 snapshot이 서로 같아야 하며, 방식 간 금융·전역 현재 상태·transfer 필드도 같아야 합니다. 달라질 수 있는 원격 이력은 검증된 진입/SL/TP 세 건뿐입니다. 다른 미해결 기록은 원래 ID 조회를 유지합니다.
- 전체 비교 중 장부와 fence가 변하면 성공으로 보고하지 않습니다. 기존 전체 이력 snapshot과 flat 확인 코드는 그대로이며 원래 ID를 조회합니다.

## 조회 예산

원본과 후보를 함께 측정하므로 도구 전체 조회 수는 기존 준비 상태 확인 한 번보다 많습니다. 합성 대표 사례는 원본28 + 후보22에 시계/공개 한도 조회를 더해 총52회였습니다. 이 측정용 총량을 개선 후 단일 실행 비용으로 해석하지 않습니다.

최대80 HTTP요청, 시계 조회6회, 요청 시작75초 예산을 둡니다. 공개 분당 한도의60% 직전 여유와 응답 weight 헤더, SDK418/429 cooldown을 유지합니다. 예산 부족·헤더 누락·조회 오류에는 재시도하지 않습니다. 모든 허용 요청은 GET이며 주문/보호 변경, listen key 생성, 리더 획득은 허용하지 않습니다. 서명·키·본문·원시 오류를 출력하지 않습니다.

## 검증 및 한계

31개 테스트와 Ruff, 원본 guard AST 동일성, 배포29개 해시 검사를 통과했습니다. 격리 PostgreSQL과 합성 응답으로28→22회, 전역 조회 유지, 별도 미해결 이력 조회 유지, 전체 이력 원래 ID 조회, 잘못된 영수증·조회 중 상태 변화·한도·시계·정리 실패를 검증했습니다.

이번 결과는 **일시정지·비어 있는 계정의 읽기 전용 반복 조회**에 한정합니다. 활성 TP/부분체결 보호·청산 확인 비용이 줄었다는 증거가 아닙니다. 현재 TP 예산 문제가 해결되거나 실제 TP 테스트가 승인된 것은 아닙니다. 따라서 `runtime_installed=false`, `runtime_switch_approved=false`, `active_tp_cost_reduction_proven=false`를 출력합니다.

일간/주간 기준·고점·기존 평가 시각·수동 청산 및 일간 채택 영수증·미해결 요청·실패 이력을 포함한 DB 상태는 그대로 보존합니다. 주문과 LIVE는 계속 비활성화 상태입니다. 이번 단계의 실제 측정 결과를 확인한 뒤 적용 범위를 판단합니다.

출처는 SOURCE_REFERENCE.json, 테스트는 VALIDATION.json 및 test-results/에 있습니다. SHA256SUMS.txt와 TRANSFER_MANIFEST.json은 파일 무결성 확인용입니다.
