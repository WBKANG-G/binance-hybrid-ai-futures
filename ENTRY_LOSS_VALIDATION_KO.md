# 응답 유실 전용 도구 검증 결과

전체1195개 테스트 통과(295.72초), 응답 유실 집중19개 통과(17.33초). Ruff check/format 및 mypy107개 소스 통과.

## 변경과 수락 조건

이전 손절 실패 ZIP의386개 manifest 파일은 모두 바이트 단위로 동일합니다. 새 모듈/CLI/테스트와 한국어 안내·최근 미체결 증거만 추가합니다. SDK, coordinator, 주문/보호/리스크/청산, 기간 기준금, 시계/리스/펜스, Compose/키/exports 및 기존 테스트를 변경하지 않습니다.

시작은 NORMAL/paused, 로컬·원격 포지션/활성 주문 없음, ISOLATED BTCUSDT 레버리지2입니다. prepare의 원래 닫힌 봉/신선도/리스크 검사 및 최소 cap·100 USDT 상한을 사용합니다. LONG OPPOSITE_QUOTE LIMIT1건에만 응답 유실을 적용합니다. RestartClient의 중복 전송 장벽을 그대로 사용합니다.

별도 클라이언트가 실제 SDK의 성공 응답에 같은 client ID, BTCUSDT, 유효한 거래소 주문 ID가 있는지 확인하고 coordinator 전달 전에 DemoUnknown을 한 번 발생시킵니다. SDK 내부 서명·HTTP·응답 판독은 원래대로입니다. 실제 전송 오류/거부가 먼저 발생하면 의도적 유실 횟수0이므로 통과하지 않습니다. 가짜 거래소 응답/UNKNOWN 재분류/자동 재시도는 없습니다.

응답 유실 뒤 원래 submit이 UNKNOWN 의도를 DB에 기록합니다. 첫 후속 signed REST 조회 전에 읽기 전용 observer가 UNKNOWN 주문 및 요청 증거와 진입 게이트 차단을 확인합니다. 이 진단의 오류는 조회를 막지 않으며 통과를 거부합니다. 원래 스냅샷 대조가 로컬 주문을 복구한 뒤 명시적 signed GET으로 같은 client ID와 거래소 주문 ID, symbol/side/type/quantity/price/reduceOnly/positionSide를 확인합니다. 원래 snapshot/merge가 정합성을 받아들인 로컬 상태도 함께 확인합니다. 체결은 수락에 필수 조건이 아닙니다.

새 진입 POST/시도 각각1, 유실1회, UNKNOWN 기록과 게이트 차단, 같은 ID signed GET, 정리 전 로컬 주문 복구, 최종 VERIFIED_FLAT, 이력·기준금 보존, 세션·감사·정리 실패 없음이 모두 필요합니다. 원래 requests의 UNKNOWN 증거는 그대로 둡니다. 실제 주문 상태와 server_order_id가 복구됐다는 의미이며 과거 요청 ACK를 다시 쓰는 것은 아닙니다.

관찰90초는 연결/준비/조회 복구 상한입니다. 미체결이면 즉시 잔량 정리, 체결이면 기존 보호/비상 정리 및 flat 확인을 수행합니다. UNKNOWN 종료는 재POST하지 않습니다. 함수나 SDK 파일을 패치하지 않으며 임시 조회 observer만 프로세스 안에서 설치하고 세션/DB worker drain 및 정리 뒤 복원합니다. 재사용 의도는 고유 시작 표식/목적/방향/종목/스타일 검사를 통과해야 하며 새 진입과 유실 재주입 없이 정리만 합니다.

## 검증 범위

실제 격리 PostgreSQL/localhost WebSocket과 합성 SDK 전송으로 미체결·전량·부분체결의 동일 ID 복구, 원래 보호/비상 정리, 과거 UNKNOWN 증거 보존, 동일 의도 무재진입, 실제 전송 실패를 주입 성공으로 오인하지 않음, 잘못된 거래소 ID 거부, UNKNOWN 종료 및 재실행 무재POST, 감사 실패·준비 실패·진단 실패와 observer 복원, 명시적 플래그 및 LIVE 거부를 확인합니다.

클라우드 인증 거래소 요청/주문은 없습니다. 새 Windows 시험은 아직 실행 전입니다. 실제 네트워크 장애·worker/호스트/DB 강제 종료·외부 알림 전달·실제 부분체결 보호·SL 실패 대응의 DEMO 통과로 확대하지 않습니다. 앞선 SL 시험은 미체결/주입0회/VERIFIED_FLAT/NORMALpaused로 검증 미완료이며 별도 증거에 보존했습니다. main/LIVE/Phase5 및 Phase4 전체 완료 판정은 진행하지 않습니다.
