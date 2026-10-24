# Phase4 최초 체결 반영 지연 검증과 진단

ZIP을 새 windows-demo-initial-fill-fix 폴더에 풀고 INITIAL_FILL_START_HERE_KO.md부터 읽으세요. 기존 Galaxybook PC와 hybrid-windows DB/볼륨을 유지합니다. 설치 후 주문 없는 review_demo_recovery부터 실행합니다.

SL·TP 전에 SNAPSHOT_CHANGED로 청산된 사례의 최초 응답 차이는 보존되지 않아 원래 필드를 확정하지 못했습니다. 기존 코드에서 포지션/수수료 반영 지연을 재현하고, 한 번의 추가 조회와 signed 단일 체결·현금/수수료 증거가 모두 맞는 최초 DEMO 검증만 처리하도록 보완했습니다. 원래 coherent_snapshot과 위험·보호 기준은 그대로이며 세션 오류가 정리 중 취소에 가려져도 성공으로 표시하지 않습니다.

전체700개 테스트, mypy81개 소스, Ruff/format221개 파일 통과. 실제 사용자 새 검증 성공이나 Phase4 완료를 뜻하지 않습니다. 초기화·export 재요청·LIVE/Phase5·main 변경 없음. 이전 수정 포함.
