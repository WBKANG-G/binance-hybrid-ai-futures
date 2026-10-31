# Phase4 빈 계좌 관찰 프로세스 SIGKILL·리더 복구

ZIP을 새 windows-demo-empty-worker-recovery 폴더에 풀고 EMPTY_WORKER_RECOVERY_START_HERE_KO.md 순서대로 실행하세요. CMD 하나: build Healthy → DEMO 키 RAM 등록 → 새 UUID로 한 번 실행. 신규 주문은0입니다. listenKey 수명과 DB 감사/소유권은 변경하므로 DB 읽기 전용 검증은 아닙니다.

API 컨테이너 안의 별도 관찰 자식이 실제 WSS/REST 준비를 완료하면, 경쟁 리더 차단을 확인하고 그 Popen 자식만 SIGKILL합니다. 종료 -9를 확인한 뒤 기존60초 lease의 자연 만료를 기다립니다. 새 리더의 fence+1, 재대조 전 게이트 차단, 이전 리더 효과/갱신 거부, 전체 REST/WSS 대조, 정상 세션 정리와 lease release를 확인합니다. 부모/자식 모두 기존 ObservationClient를 사용해 일반 SDK request의 GET 이외를 거부합니다. 주문/리스크/기준금/키 보관/Compose/exports를 포함한 기존349개 파일은 모두 동일합니다.

로컬 테스트의 OS 종료와 PostgreSQL은 실제이며 거래소 응답/시각은 fixture입니다. 테스트 시계를61초 진행한 만료 검사와 실제60초 벽시계 대기를 구분합니다. Windows 실행은 실제 자연 만료를 기다립니다. 새 실제 검증은 아직 미완료입니다. 보유 주문/포지션 Worker 중단·복구, 실제 부분체결 보호, 전체 호스트/DB 중단, 장기 운영 또는 Phase4 전체 통과로 확대하지 않습니다. main/LIVE/Phase5 변경 없음.
