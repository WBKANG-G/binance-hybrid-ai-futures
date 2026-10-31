# Phase4 손절 로컬 조립 실패·원래 자동 비상 종료 검증

ZIP을 새 windows-demo-sl-fault-check 폴더에 풀고 SL_FAULT_START_HERE_KO.md 순서대로 진행합니다. CMD 하나에서 빌드 Healthy → DEMO 키 RAM 등록 → 문서의 새 UUID로 한 번 실행합니다. 최소 cap/100 USDT 이내 BTCUSDT LONG OPPOSITE_QUOTE LIMIT 진입1건을 DEMO에 실제 제출합니다.

체결 뒤 같은 의도의 SL 조립에만 로컬 ValueError를1회 주입합니다. 원래 coordinator의 NOT_SENT/LOCAL_VALIDATION_FAILED, EMERGENCY/paused, 신규 진입 차단 및 자동 reduce-only 종료가 동작해야 통과합니다. SL/TP POST0건, 진입1건/종료1건, FILLED/VERIFIED_FLAT, 과거 이력·기준금 보존을 확인합니다. 후속 운영자 정리만 성공하면 통과하지 않습니다. UNKNOWN 종료는 재POST하지 않습니다. 최종 비상 잠금과 신규 진입 일시정지를 유지합니다.

임시 조립 함수와 종료 관찰은 별도 CLI 프로세스 안에서만 적용하고 세션/DB worker drain 뒤 복원합니다. 진단 실패는 위험 축소 종료를 막지 않습니다. 기존378개 파일·테스트·SDK·실행/보호/리스크/청산·시계/리스/펜스·기준금·키·Compose·exports는 모두 동일하며 별도 모듈/CLI/시험/한국어 문서·증거8개만 추가했습니다.

전체1176개·집중20개 시험, Ruff 및 mypy105개 소스 통과. 실제 격리 PostgreSQL/localhost WSS 및 합성 거래소 SDK로 검증했으며 클라우드 인증 거래소 호출은 없습니다. Windows 실제 새 시험은 아직 실행 전입니다. 실제 거래소 SL 거부, 외부 긴급 알림 전달, 부분체결 보호 성공 또는 Phase4 전체 통과로 확대하지 않습니다. 기존 HTTPS 복구 검토55건/13.891초 및 명시적 비상 상태 승인 완료 증거를 함께 보존했습니다. main/LIVE/Phase5 변경 없음.
