# WebSocket 단절 관측/정리 분리 수정 검증

부모 ZIP SHA256 86e938597771647f078701f24c41b2f74c2d19e5896c78204a93cf4d554e7530.
기존 파일 중 services/execution/demo_open_disconnect_check.py만 수정한다. 기존 테스트는 그대로 보존하며 새 통합/단위 테스트, 안내 및 실제 실패 타임라인을 추가한다.

## 확인된 실제 결과

4e81bbe7-12f8-4e65-848a-f3a928b13a6e는 미통과다. 신규 진입1/close요청1/재연결0, OTHER_VALIDATION_FAILURE, 최종 flat/NORMAL/paused. 후속 READ ONLY 조회에서 같은 주문 CANCELED/CLOSED/체결0/잔량0 및 last_private_session_failure SOCKET_CLOSED를 확인했다. READY 08:46:56.835974Z, 실패기록 08:47:03.381453Z, 최종결과 08:48:14.469915Z다. 정확한 최초 타이머 시작과 원래 예외 객체는 보존 출력에 없으므로 원본 예외를 관측했다고 주장하지 않는다.

## 수정 동작

기존 코드는 최초 session 전체를 asyncio.wait_for45초로 감싸 준비·단절 후 finally 정리까지 취소 대상으로 삼았다. 정리 중 타이머가 만료되면 cleanup이 중단되고 TimeoutError도 OTHER_VALIDATION_FAILURE로 분류됐다. 의도적으로 정리를 지연시킨 기존판 테스트가 통과를 거부하는 현상과 타이머 분류 오류를 재현했다.

변경은 recv를 한 번만 위임하는 얇은 연결 wrapper로 ConnectionClosed 발생을 기록하고 원래 수신기에 같은 예외를 그대로 전달한다. 이벤트 내용·E·검증·인박스·SDK·coordinator 동작을 수정하지 않는다. 최초 연결·준비·단절 recv 관측은 같은45초 안에 끝나야 한다. 관측 시각이 제한 밖이면 실패한다. 관측 뒤 session의 finally를 끝까지 기다리고 반환된 오류를 소비한다. 대기 중 반복된 호출자 취소도 cleanup을 다시 중단시키지 않으며, cleanup 완료 후 호출자 취소를 전파한다.

관측하지 못한 채 제한이 만료되면 해당 세션에 취소를 1회 요청하고 정리를 drain하며 PHASE_TIMEOUT/WAIT_SOCKET_LOSS로 보고한다. 정상 단절 확인 뒤에는 기존 SOCKET_CLOSED/게이트 차단 검사를 통과해야 한 번만 재연결한다. 이후에도 같은 주문 CANCELED/체결0, 중복 POST0, 기존 이력·기준 보존, VERIFIED_FLAT/NORMAL/paused 조건은 그대로다. 재연결 대기도 기존45초, SDK clock/HTTP/fencing/TTL/리스크 제한은 변경하지 않는다.

단절 요청/수신 관측/최초 세션 완료 UTC, 관측 경과 ms와 제한 값을 보고한다. 마지막 보존 세션 오류는 별도 이름 last_recorded_private_session_failure로 출력한다. 단계는 WAIT_SOCKET_LOSS/DRAIN_DISCONNECTED_SESSION을 구분한다. 성공을 못하면 이전 판정을 재작성하지 않는다.

## 재현·검증

부모판 바이트 그대로인 실행 모듈에 새 회귀 테스트를 적용: 2 failed in 3.13s. 단절은 관측했지만 session 정리를 2.1초 지연해 2초 관측 제한을 넘기는 경우의 거부 및 최초 연결 지연의 PHASE_TIMEOUT 분류 누락을 확인했다. 이는 실제 Binance 네트워크 지연을 그대로 재현했다는 뜻은 아니다.

집중 테스트: 29 passed in 9.83s. Ruff check/format 및 mypy 96 sources 통과.
전체 테스트: 1077 passed in 218.10s (0:03:38). Ruff check/format 통과, mypy 96 sources 통과.

검증에는 실제 PostgreSQL 격리 schema, 로컬 WSS 서버, signed SDK 응답 fixture를 사용했다. 새 테스트는 관측 후 지연된 정리의 완료/재연결, 제한 내 단절 미관측 거부, 반복 호출자 취소 중 정상 cleanup 보존 및 취소 전파, 단일 취소 요청 뒤 finally drain, 예외 소비·비노출을 검사한다. 기존 단절·체결 중 취소·보호·중복 방지·회복·기준 보존 테스트도 그대로 실행한다.

클라우드에서 인증된 실제 거래소 주문을 보내지 않았다. 새 ZIP의 실제 Windows 검증은 아직 진행 전이며, 부분체결 보호/worker·호스트·DB 중단/장기 무인 운용 및 Phase4 전체 통과로 확대하지 않는다. main/LIVE/Phase5/exports/영구 키/기준금 변경은 없다.
