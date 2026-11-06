# 최신 미체결 TP 검증 주문의 읽기 전용 시점 진단

대상은 이미 취소·종료된 작업 `31c953e8-72dc-422d-89f3-8bbe7cdb5c0c` 하나입니다. 해당 작업의 로컬 신호 만료와 취소 의도, 관찰 종료/일시정지 마커, 거래소 생성·마지막 갱신 시각을 대조합니다. 신규 주문, 재시도, 신호 만료 연장, 복구·진입 재개는 하지 않습니다.

바탕화면의 새 폴더에 압축을 푼 뒤 CMD에서 실행하세요. Windows 보안 설정은 유지하며 CMD 실행 파일은 없습니다.

```cmd
cd /d "%USERPROFILE%\Desktop\windows-demo-tp-unfilled-timing"
```

```cmd
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; if((Get-FileHash -Algorithm SHA256 -LiteralPath 'inspect_demo_tp_timing.py').Hash.ToLower() -ne '5b989b99ad980efbd315e186f1eaf58a0b708767c82b82fa9eab9880322c098e'){throw 'TIMING_READER_HASH_MISMATCH'}" && docker exec -i hybrid-windows-api-1 python - < inspect_demo_tp_timing.py
```

결과 화면 전체를 보내주세요. 키·비밀번호를 다시 입력하거나 대화에 붙여넣지 마세요. 등록된 RAM 키를 사용할 수 있으면 공개 시각 조회와 해당 정확한 주문의 서명 GET 1회를 수행합니다. 키를 사용할 수 없으면 로컬 증거만 표시하고 거래소 시각은 미검증으로 남깁니다. 조회 오류도 거래소 증거 검증 실패로 명확히 표시하며 재시도하지 않습니다.

## 확인하는 내용

- 로컬 주문이 이 작업의 BTCUSDT/LONG/DEMO_VALIDATION, CANCELED/CLOSED, 체결·잔량·보유 수량 0이어야 합니다.
- 거래소 응답의 client/server 주문 ID, BUY/LIMIT/CANCELED, 원래 수량과 체결 0을 확인한 뒤 생성·마지막 갱신 시각을 사용합니다.
- 실제 감사 이벤트는 최대 500개로 제한하고 대상 작업의 투영만 출력합니다. 계좌 원본이나 전체 이벤트 payload, 키·서명 URL은 출력하지 않습니다.
- 모든 SQL 트랜잭션이 읽기 전용이며 진단 전후 전체 계좌/owner/fence/lease가 같아야 합니다. lease 획득·DB 변경·API/DB 재시작·재빌드는 없습니다.

관찰 설정 300초가 미체결 진입 주문의 유효기간을 300초로 바꾸는 것은 아닙니다. 현재 prepare 코드는 유효한 신호를 만든 시점 기준 10초 만료를 사용합니다. 이번 진단은 그 만료와 실제 접수·취소 시점이 어떻게 겹쳤는지 확인하며 만료·호가 freshness·위험 기준을 변경하지 않습니다.

로컬 감사 시각은 행 잠금 후 변경 적용 전의 시각이며 거래소 응답 또는 커밋 시각이 아닙니다. 로컬과 거래소 벽시계를 뺀 수치를 단조 시계 지연 측정이나 SLA로 해석하지 않습니다. 동일 거래소 응답의 생성→마지막 갱신 간격도 최종 주문 갱신 간격이지 모든 HTTP 구간을 측정한 값이 아닙니다.

이 도구의 실행 완료만으로 거래소 증거까지 검증됐다고 판단하지 마세요. `remote_timing_status=SIGNED_CANCELED_ZERO_FILL_ORDER_VERIFIED`일 때만 해당 서명 응답의 식별/상태/시각 검사가 통과한 것입니다. TP 실제 체결·SL 정리나 수정본 부분 체결 보호의 성공을 주장하지 않습니다.
