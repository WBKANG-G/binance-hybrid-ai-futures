# 종료된 DEMO TP 주문 시간 진단

대상은 6703f8c0-0c8a-4a2b-84d5-37e39693dbba 한 건이다. Windows 결과는 미체결 취소, TP_FILL_NOT_OBSERVED/NOT_ACCEPTED, VERIFIED_FLAT, NORMAL/paused다. TP 성공으로 수락하지 않는다.

ZIP을 풀고 inspect_demo_tp_timing.py가 보이는 폴더에서 CMD를 연 뒤 한 번 실행한다. 기존 hybrid-windows-api-1 컨테이너가 실행 중이어야 한다.

```bat
docker exec -i hybrid-windows-api-1 python - < inspect_demo_tp_timing.py
```

출력 JSON 전체를 공유한다. 설치·빌드·Compose 경로 변경은 필요 없다. 키 입력을 요청하지 않는다.

SQL은 READ ONLY 트랜잭션이다. 캐시된 키가 유효하면 시계 조회 후 기존 주문 한 건을 서명 GET으로 조회한다. 캐시 키가 없거나 검증에 실패하면 로컬 기록만 출력한다. 새 주문·취소·리스 획득·기준금 변경을 하지 않는다. 원본 키·응답·전체 원장은 출력하지 않는다.

기존 코드의 신호 유효기간은 생성 시점부터 10초다. 300초는 전체 관찰 상한이며 주문 유지 보장이 아니다. 원격 time/updateTime과 저장된 신호 만료 시각을 비교한다. updateTime은 최신 갱신 시각으로, 취소 명령 송신 시각 자체는 아니다. 로컬 감사 시각은 트랜잭션 시작 시각이고 거래소 처리·커밋 시각이 아니다. 원장 스냅샷에 대상 주문이 존재한다고 모든 감사 이벤트가 그 주문에서 발생했다고 단정할 수 없다. 시간 근접성만으로 취소 원인을 확정하지 않는다.

NOT_CHECKED_CACHED_KEY_UNAVAILABLE 또는 REMOTE_QUERY_OR_IDENTITY_OR_TIMESTAMPS_NOT_VERIFIED는 원격 시간 검증 실패를 뜻한다. BLOCKED_NO_CHANGE면 반복 거래 없이 출력 그대로 공유한다. 타 계정/체결/미종료 주문은 범위 밖이다.

실제 TP·부분체결·보호 포지션 장애 및 Phase4 전체 완료를 증명하지 않는다. 정책·TTL·수량·가격은 변경하지 않는다.
