# 미체결 진입 주문 중 WSS 단절·취소·재연결 검증 기록

## 변경 범위

부모 ZIP SHA256: 4f169d3a03132f0e7acc63e64125f25d17a5d4c9c4ee4ad53748bf66a2334eb5.
기존 manifest 파일 334개 모두 바이트 단위로 동일하다. 새 실행 모듈, CLI, 통합/단위 테스트, 시작/검증 안내 및 최근 실제 검증 문서 4개를 추가한다.

SDK 서명·REST/WSS 호스트·시계 검사·조회 계약·주문 cap·리스크·보호·청산·기준금·키 보관·Compose·exports·이전 테스트를 변경하지 않는다. 신규 진입에는 이미 실제 DEMO API 재시작에서 사용한 RestartClient/terms를 재사용한다. LIVE 컨텍스트는 키 읽기 전에 거부하고 DEMO 및 fault injection 두 플래그와 UUID가 필수다.

## 새 실행의 검사

- 처음에는 NORMAL/paused, ISOLATED BTCUSDT 2x, 거래소·로컬 빈 계좌가 필요하다. 다른 활성 의도는 거부한다.
- 단일 LONG LIMIT 신규 진입 POST만 허용한다. 같은 의도 재호출은 SDK 진입 POST가 추가되지 않아야 한다. 두 번째 진입 transport 시도는 SDK 전에 거부하고 성공도 차단한다.
- 서명 NEW/체결0의 주문을 확인한 후 소켓 close1011를 1회 주입한다. 전체 네트워크 차단이나 프로세스 강제 종료가 아니다.
- 기존 session의 예외 처리와 finally가 pause/cancel/reconcile을 수행한다. 이 안전 동작을 우회해 주문을 열어 두지 않는다.
- SOCKET_CLOSED와 paused true, stream_ready/reconciled false, paused만 제거한 깊은 복사본의 게이트 거부를 확인한다. 실제 계좌를 이 검사로 unpause하지 않는다.
- 새 세션 한 번만 연결하며 원래 connected()의 전체 REST 대조·보호 경로가 먼저 수행된다. 이후 같은 주문 ID/종목/client ID/방향/LIMIT/원수량/가격/체결0/CANCELED/CLOSED를 확인한다. 재연결에서 resume_entries를 호출하지 않는다.
- 첫 번째/두 번째 callback 오류는 thread 완료 신호보다 먼저 고정된 코드로 보존한다. 세션 thread는 원래 settled_call로 모두 drain한다. 두 번째 세션 취소 중 뒤늦게 확인된 수신 실패도 성공을 차단한다.
- 신규 주문의 체결·모호한 취소·재연결 실패는 성공이 아니다. 최종 pause, 필요 시 기존 EMERGENCY_FLATTEN, 기존 bounded confirm_exit를 수행한다. 정리 실패는 UNKNOWN으로 출력하며 flat을 추정하지 않는다.
- 기존 주문·요청·수량 cap·검증 증거·회복 기록·일/주 기준·고점·순입출금·설정·정책을 보존하는지 확인한다. 새 감사 operation tag는 fenced store.change로 기록하고 같은 결과 tag를 덮어쓰지 않는다.
- 이미 존재하는 UUID는 기존 의도의 복구·정리만 하며 RECOVERY_ONLY로 보고한다. 신규 진입과 장애 주입·재연결을 추가하지 않는다.
- 각 세션 대기는 최대 45초다. 기존의 취소 시 worker drain과 최종 SDK 정리는 이 대기시간 뒤에도 완료까지 수행할 수 있으므로 전체 실행의 강제 종료 타이머라고 주장하지 않는다. 주문 TTL60초와 실제 quote/signal/account/SDK clock/lease 제한은 늘리지 않는다.

## 검증 결과

전체 테스트: 1072 passed in 203.04s (0:03:23). Ruff check/format 통과, mypy 96 sources 통과.

집중 테스트: 24 passed in 7.72s. 실제 PostgreSQL의 격리 schema와 로컬 WSS 서버, 결정적인 signed SDK 응답 fixture를 사용했다. 정상 취소·재연결, 기존 UUID 복구, 최초 소켓 실패, 취소 중 부분체결의 기존 보호 및 flat, 재연결 실패, 중복 transport 차단, callback 원인 보존, 입출금/기준 변화 거부, 게이트 검사 실패, flat 확인 실패, 감사 기록 실패, ACK 응답 유실, 기존 결과 tag 보존, 늦은 수신 실패, 이전 완료 주문과 cap/이력 보존을 검사했다.

클라우드에서 인증된 Binance 주문을 제출하지 않았다. 테스트 fixture의 부분체결은 실제 DEMO 부분체결 통과 증거가 아니다.

## 최근 실제 Windows 결과

- 미체결 주문 중 API 재시작: a51c3737-ce9c-4645-ba59-44c259f32e91, OPEN_ORDER_API_RESTART_VERIFIED, 진입 POST1, 동일 주문 확인, VERIFIED_FLAT, NORMAL/paused, 기존 기준 보존.
- LONG PASSIVE: 648b31e2-570c-4ac5-99a6-38b6dbb8bcee, 0.0007 전량 체결, SL/TP 확인, FINISHED_FLAT/CLOSED, session/정리/DB 오류 없음.
- SHORT PASSIVE: 095b9d5c-b79d-4731-9d64-971e4ffde7d0, 0.0007 전량 체결, SL/TP 확인, FINISHED_FLAT/CLOSED, session/정리/DB 오류 없음.
- 마지막 복구: 48 requests / 6 batches / 216 signed GET / clock1 / unresolved2 보존 / risk PASS / 포지션0 / 활성 주문0. 적용 88bc4aa7-8fc4-4940-8c2c-a759a05d1022, EMERGENCY_ACKNOWLEDGED_ENTRIES_STILL_PAUSED.

새 미체결 주문 중 WSS 장애 검증은 아직 Windows 실제 실행 전이다. 이전 실제 부분체결의 실패는 실패로 유지한다. 실제 부분체결 보호 성공, 실행 프로세스 강제 종료/호스트·DB 장애, 보유 포지션 장애 및 장기 운영은 미완료다. Phase4 전체 통과 또는 Phase5/LIVE 승인으로 확대하지 않는다.
