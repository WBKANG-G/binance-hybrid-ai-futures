# Phase4 진입 SDK 응답 유실·동일 ID 복구·중복 진입 방지

ZIP을 새 windows-demo-entry-response-loss 폴더에 풀고 ENTRY_LOSS_START_HERE_KO.md 순서대로 진행합니다. CMD 하나에서 빌드 Healthy → DEMO 키 RAM 등록 → 문서의 새 UUID로 한 번 실행합니다. 최소 cap/100 USDT 이내 BTCUSDT LONG OPPOSITE_QUOTE LIMIT 진입1건을 DEMO에 실제 제출합니다.

SDK가 성공 응답을 받은 뒤 coordinator에 전달할 ACK만 로컬에서1회 유실시킵니다. 원래 코드가 UNKNOWN 의도를 기록하고 진입 게이트를 차단한 상태에서 signed REST 대조를 수행하는지 확인합니다. 같은 client ID와 거래소 주문 ID의 명시적 signed GET 및 정리 전 로컬 주문 복구, 신규 진입 POST/시도 각각1, 최종 VERIFIED_FLAT가 모두 필요합니다. 과거 requests의 UNKNOWN 증거는 고치지 않습니다. 미체결이어도 검증할 수 있습니다. 체결되면 기존 SL/TP와 비상 정리 경로를 사용합니다. UNKNOWN 종료는 재POST하지 않습니다.

성공 후에도 신규 진입은 일시정지됩니다. 체결 뒤 비상 정리를 사용한 경우 EMERGENCY가 유지되며 별도 복구 검토가 필요합니다. BLOCKED/NOT_ACCEPTED/UNKNOWN이면 임의로 재주문하지 않고 전체 JSON을 공유합니다. 기존 손절 실패 시험은 미체결로 주입0회였으며 검증 미완료 증거를 보존했습니다. 이번 시험은 손절 실패나 실제 부분체결 수락을 대신하지 않습니다.

전체1195개·집중19개 시험, Ruff 및 mypy107개 소스 통과. 격리 PostgreSQL/localhost WSS와 합성 SDK 전송으로 검사했으며 클라우드 인증 거래소 호출은 없습니다. 새 Windows 시험은 아직 실행 전입니다. 실제 네트워크 timeout이나 패킷 손실을 일으킨 검증으로 확대하지 않습니다. 기존386개 파일·테스트·SDK·coordinator·주문/리스크/보호/청산·기간 기준금·시계/리스/펜스·Compose/키/exports는 모두 동일합니다. 새 모듈/CLI/시험/한국어 문서·증거7개만 추가했습니다. main/LIVE/Phase5 및 Phase4 전체 완료 판정 변경 없음.
