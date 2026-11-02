# 다음 독립 검사: 합성 WSS 중복·역순·수신 실패 차단

기존 검증 이미지를 그대로 사용한다. 새 앱 설치/빌드/거래/API 키 입력은 없다. 원문 수집 기능을 추가하는 작업도 아니다. 기존 EventInbox는 원문을 지속 저장하지 않고 알려진 이벤트의 종류/시각 힌트만 메모리에서 묶는다. demo_events는 장부 감사 payload라 과거 실제 WSS 원문으로 간주할 수 없다. 외부에서 별도 수집한 사용자 파일의 존재 여부를 확인한 것은 아니다.

이번45개는 기존 수신기·누적 체결 대조·이벤트 시각·동시 장애 경로를 합성 이벤트와 로컬 WS/전용 PG로 검사한다. 실제 캡처 프레임을 재생한 수락은 아니며 결과의 captured_real_wss_frames_replayed도 false다.

## 1. 파일 복사

작은 ZIP 안의 run_isolated_wss_check.py 파일만 기존 windows-demo-isolated-fault-check 폴더로 복사한다. 그 폴더의 CMD를 사용한다. 기존 이미지 hybrid-phase4-fault-check:local은 이전13개 성공 검사에서 빌드한 것이다.

## 2. 테스트 DB 기동

```bat
docker compose -p hybrid-phase4-fault-check -f infra/docker-compose.phase4-fault-check.yml up -d --wait fault-db
```

## 3. 같은 이미지로 검사 실행

```bat
docker compose -p hybrid-phase4-fault-check -f infra/docker-compose.phase4-fault-check.yml run --rm --no-deps -T --entrypoint python checks - < run_isolated_wss_check.py
```

전용 환경/DB/키 미마운트 확인과 관련 원본9개 파일 SHA256 일치를 검사한 뒤 원래 검사45개를 실행한다. 운영 DB·키 RAM·export 볼륨은 연결하지 않는다. stdin 진단 파일은 메모리에서 실행하고 기존 앱 파일은 바꾸지 않는다.

정상은45 passed 및 ISOLATED_SYNTHETIC_WSS_CHECK_PASSED, failed/skipped0이다. 마지막 출력 화면을 공유한다. 오류/NOT_ACCEPTED/BLOCKED이면 그대로 공유하고 실제 계좌 주문이나 잠금 해제·복구 명령을 실행하지 않는다. 키 입력을 요청하지 않으므로 키 값을 붙여넣지 않는다.

## 결과 보존 뒤 정리

검사 결과를 보존한 후 전용 DB와 네트워크를 정리할 수 있다.

```bat
docker compose -p hybrid-phase4-fault-check -f infra/docker-compose.phase4-fault-check.yml down
```

scope는 SYNTHETIC_EVENTS_AND_LOOPBACK_WS_WITH_DEDICATED_PG_ONLY다. 실제 WSS 원문 기록·실제 체결 이벤트 재생·부분체결 보호·TP 실제 체결/SL 정리 또는 Phase4 전체 완료를 이 결과로 대체하지 않는다. 현재 실제 계좌의 신규 진입/LIVE 활성화는 없다.
