# 취소 주문 조회의 연결 방식별 작은 지연 측정

대상은 이미 취소·미체결·종료된 작업 `31c953e8-72dc-422d-89f3-8bbe7cdb5c0c`입니다. 같은 주문을 기존 SDK 방식으로 2회, 기존 조회 전용 HTTPS 연결 재사용 방식으로 2회 확인합니다. 새 주문이나 주문 재시도가 아닙니다.

## 실행

Windows 보안 설정은 유지합니다. CMD 실행 파일은 없습니다. 새 폴더에 압축을 풀고 CMD에서 실행하세요.

```cmd
cd /d "%USERPROFILE%\Desktop\windows-demo-tp-read-transport-probe"
```

```cmd
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; if((Get-FileHash -Algorithm SHA256 -LiteralPath 'inspect_demo_tp_timing.py').Hash.ToLower() -ne 'fa9fff493c9b0bd5b117422423763b7693747db80bdcb256fe98475f6e7c0bea'){throw 'READ_PROBE_HASH_MISMATCH'}" && docker exec -i hybrid-windows-api-1 python - --compare-read-transports < inspect_demo_tp_timing.py
```

결과 화면을 보내주세요. 키가 만료되거나 조회가 차단되면 재실행하지 마세요. 현재 등록된 RAM 키를 내부에서 사용하며 키·비밀번호·서명 URL·주문 ID 원본은 출력하지 않습니다. 새 키를 대화에 입력하지 마세요.

## 범위와 안전 조건

- 서명 주문 GET 총 4회, 공개 시각 동기화 2회입니다. 별도 주문·취소·청산·보호 주문·private WSS·메일·복구·진입 재개는 없습니다.
- 모든 주문 응답이 해당 정확한 client/server 주문 ID, BTCUSDT/BUY/LIMIT/CANCELED, 원래 수량과 체결 0, 정상 생성·갱신 시각을 가져야 합니다. 네 조회의 거래소 생성·갱신 시각도 같아야 합니다.
- DB는 읽기 전용 트랜잭션으로만 접근합니다. 각 표본 내부와 전체 비교 전후의 전체 계좌·owner/fence/lease를 비교합니다. lease를 획득하거나 DB를 수정하지 않습니다.
- 기존 SDK·조회 전용 `recovery_connection`과 검증된 TLS/프록시/리다이렉트 금지 정책을 사용합니다. 임시 조회 클라이언트의 연결 방식은 종료/오류 시 복원·정리합니다. API 서버의 실행 전송 경로나 설치 소스, 신호 만료, 호가 freshness, 가격/수량/위험 기준은 변경하지 않습니다. 재빌드·재시작도 없습니다.
- 전체 표본 처리의 목표 예산은 30초이며 초기/최종 DB 읽기와 진행 중인 제한된 SDK 호출의 시간은 추가될 수 있습니다. 응답 실패·시간 초과·계좌 변경·비교할 수 없는 사용자 지정 transport면 중단하고 비정상 종료 코드를 반환합니다. 자동 재시도나 다른 주문으로의 대체는 없습니다.

`original_sdk_request_ms`와 `pooled_get_request_ms`가 각각 2개 표본입니다. SDK 요청 전후를 단조 시계로 측정하며 DB 감사·분석 시간은 제외합니다. 각 방식의 시각 동기화 준비 시간은 요청 시간에서 제외되고, 연결 재사용 쪽은 시각 조회로 열린 연결을 사용합니다. 기존 방식 후 연결 재사용 방식이라는 고정 순서입니다.

이 네 표본은 통계적 성능 보장·SLA·인과 실험이 아닙니다. RTT 변화와 연결 준비 효과가 섞일 수 있습니다. **미체결 원인은 여전히 미입증**이며 빠르게 조회됐다고 체결이 보장되거나 TP 검증이 완료되는 것은 아닙니다. 실제 주문 경로에 연결 재사용을 적용하는 작업도 이 도구에 포함되지 않습니다. 출력은 측정 검토용이고 주문/정책 변경의 지시가 아닙니다.
