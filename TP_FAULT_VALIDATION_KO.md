# 로컬 TP 조립 실패·SL 유지·진입 차단·운영자 비상 정리 검증

부모 ZIP SHA256: 904ebf67fa96d7ec473a91b49c7356edcbdb1f38d47dc3b6a4e19806b7d08e7f.
기존 manifest 파일363개를 바이트 그대로 보존한다. 새 실행 모듈/CLI/통합·단위 테스트/안내·검증 문서와 실제30분 관찰 및 미체결 추가 시도 증거를 추가한다.

## 정확한 주입 지점

원래 _protect는 SL 생성·조회 후 보호된 포지션에 TP를 조립한다. 새 CLI는 같은 의도의 TP AlgoOrder만 검사하는 임시 build_protection wrapper를 그 CLI 프로세스 안에 설치한다. 다른 의도/SL은 저장해 둔 원래 함수를 그대로 호출한다. 프로세스 밖 Uvicorn이나 파일은 변경하지 않는다. SDK 서명·전송 및 coordinator의 _protect 구현은 수정하지 않는다. 조립 함수의 참조에만 위 임시 wrapper를 적용한다.

TP 조립 전에 현재 ledger의 같은 의도, 등록 SL, protected/open_quantity, SL 요청 VERIFIED를 확인하고 signed GET /fapi/v1/algoOrder로 SL을 조회한다. 원래 _stop_ok와 normalized algoId의 기존 서버 ID 일치를 요구한다. own TP ID/계좌/종목/SELL/트리거/MARK_PRICE도 확인한다. 실제 원문/키/URL은 보고하지 않는다. 확인 불능이면 주입 횟수0으로 거부하고 기존 정리를 시도한다.

검증 후 단 한 번 ValueError를 발생시켜 원래 submission_attempted=False 분기가 NOT_SENT 및 LOCAL_VALIDATION_FAILED를 저장하게 한다. 전송 실패 뒤 UNKNOWN을 NOT_SENT로 재분류하지 않는다. TP POST가 뜻밖에 발생하면 client의 별도 own-ID 장벽이 SDK 전송 전에 차단하고 성공을 거부한다. SL을 차단하거나 실패시키지 않는다.

## 기존 동작과 도구의 동작 구분

TP 생성 실패의 원래 정책은 paused=true로 추가 진입을 막고 등록된 SL을 유지한다. 자동 즉시 비상 청산으로 설명하지 않는다. 도구는 원래 처리 후 risk NORMAL/paused/reconciled false/SL protected/실제 노출 및 NOT_SENT 증거와 진입 게이트 거부를 관측한다. 이 확인 뒤 원래 session의 finally를 drain하고 PAUSE_ENTRIES 및 노출이 있으면 EMERGENCY_FLATTEN을 실행한다. 이는 명시적으로 요청한 검증의 운영자 정리 단계다.

진입은 기존 prepare의 LONG OPPOSITE_QUOTE LIMIT/최소 cap/100 USDT 상한이다. 새 시장가 진입은 허용하지 않는다. 기존 RestartClient의 신규 진입1회 장벽을 사용하며 원래 reduce-only 위험 축소는 차단하지 않는다. UNKNOWN 종료는 기존 idempotency에 따라 재POST하지 않는다. 종료/청산의 FILLED와 원래 bounded confirm_exit의 VERIFIED_FLAT, 최종 EMERGENCY/paused/stream false/reconciled false를 요구한다. 미체결/조건 미관측/세션 오류/정리 불확실은 통과가 아니다. 함수 wrapper는 세션과 DB worker를 drain한 뒤 컨텍스트 종료로 복원한다.

기존 주문/요청/수량 cap/검증/회복 증거, 일·주 기준/고점/순입출금/정책/기존 income_seen 값은 보존한다. 새 실제 자기 거래의 fee/PnL은 원래 coordinator가 대조해 반영하며 기존 income 항목을 지우지 않는다. 로컬 TP NOT_SENT 증거는 기존 enum 값 그대로 남기고 수동 교정·기준금 재설정·리스크 해제를 하지 않는다. 기존 의도 재실행은 복구만 하고 RECOVERY_ONLY로 보고한다. 의도 없이 시작/결과 기록이 있는 UUID는 재주문 전에 차단한다.

새 fenced 결과 감사 실패나 CLI lease release 실패도 성공을 거부한다. 기존 SDK/수신기/coordinator/리스크/보호/청산/계약/키/기준금/Compose/exports/이전 테스트를 변경하지 않는다. 보호 확인을 위한 signed GET은1회 추가되며 원래 HTTP/시계/RTT/lease/freshness 제한을 늘리지 않는다.

## 로컬 검증

집중 테스트: 18 passed in 6.86s.
전체 테스트: 1135 passed in 236.08s (0:03:56). Ruff check/format 통과, mypy102소스 통과.

실제 PostgreSQL 격리 schema/localhost WSS와 synthetic signed SDK 응답을 사용했다. 자기 SL 생성·검증 후 TP 조립 실패, NOT_SENT 원래 기록 경로, 진입 차단·SL 유지, 운영자 reduce-only 청산 및 보호 잔여 정리까지 검사했다. 실제 거래소 주문은 클라우드에서 전송하지 않았다.

새 테스트: 정상 경로, 미체결의 주입0/취소, 동일 의도 재실행의 무재주문·무재주입, SL 실패 시 주입 금지, 별도 signed SL proof 불일치 거부, UNKNOWN 비상 종료의 재POST 금지/flat 오판 금지, 입출금/기준 변화 거부, 감사 실패 시 거부와 wrapper 복원, 준비 실패의 무주문·wrapper 복원, 명시적 DEMO/주입/UUID 및 LIVE 거부, TP 전송 추가 장벽을 확인했다.

## 최근 실제 결과와 남은 범위

실제30분 관찰 87723a08-552e-43aa-9d5c-062de037aec8은 monotonic1800014ms, 키 생성/갱신/삭제 각1, request GET4392, 수신0/PING 오류NONE, 이력·기준 보존, NORMAL/paused/진입0으로 통과했다. 갱신 API 성공 응답 확인 범위이며 최초 키 만료시한 뒤 연결 유지까지 검증한 것은 아니다.

부분체결 추가 시도 fe07c616-cf71-4ed9-b972-14c63b67d141은0.0007 LONG PASSIVE 주문이 미체결/정상 취소/CLOSED/FINISHED_FLAT로 끝났고 부분체결 검증은 미완료다. 기존 실패를 통과로 바꾸지 않는다.

새 TP 장애 검증은 Windows 실제 실행 전이다. 실제 거래소 거부나 SL 실패를 직접 주입한 테스트, 실제 부분체결 전체 보호, 보유 Worker 중단·복구, 호스트/DB 장애 또는 Phase4 전체/LIVE/Phase5 통과로 확대하지 않는다.
