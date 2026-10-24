# Phase4: 종료 시 진행 중인 DB 처리 완료 대기

ZIP을 새 windows-demo-worker-drain-fix 폴더에 풀고 WORKER_DRAIN_START_HERE_KO.md부터 읽으세요. 기존 Galaxybook PC와 hybrid-windows DB/볼륨을 유지합니다.

취소된 to_thread의 DB 작업이 끝나기 전에 다음 정리를 시작하던 경로를 보완했습니다. 실제PG에서 부모 버전의 행 잠금 시간 초과와 사용자와 같은 DATABASE_ACCESS_FAILED 결과를 재현하고 수정판을 검증했습니다. 전체682개 테스트/mypy80개 소스/Ruff 통과. 실제 사용자 원래 SQLSTATE를 수집했다고 주장하지 않습니다.

설치 후 --confirm-flat으로 기존 종료만 대조합니다. 신규 진입이나 청산 POST는 없고, 실제 청산이 입증된 경우에만 로컬 상태와 잔여 보호를 정리합니다. 포지션이 남으면 UNKNOWN입니다. EMERGENCY/paused·기준/과거 기록은 유지합니다. 실제 새 자동 진입·청산 검증 성공으로 처리하지 않습니다.

초기화, export 재요청, LIVE/Phase5 진행이나 main 변경 없음. 이전 런타임·시각 정밀도·일별 기준 수정도 포함합니다.
