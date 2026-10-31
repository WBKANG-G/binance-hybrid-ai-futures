# 손절 조립 실패 도구 검증

전체1176개 테스트 통과(270.42초), 손절 실패 집중20개 통과(13.74초). Ruff check/format 및 mypy105개 소스 통과.

## 범위

별도 DEMO CLI, 검증 모듈, 테스트, 한국어 안내/증거만 추가합니다. 이전 ZIP의378개 manifest 파일은 모두 바이트 단위로 동일합니다. 기존 복구 HTTPS 재사용 수정과 실제 검토/승인 통과 증거도 포함했습니다. 기존 테스트·SDK·실행 coordinator·리스크·보호·청산·기간 기준금·키·Compose·exports를 수정하지 않았습니다.

시작 상태는 NORMAL/paused, 원격·로컬 포지션/활성 주문 없음, ISOLATED BTCUSDT 레버리지2입니다. 기존 prepare의 닫힌 봉·신선도·기준금/리스크 게이트·최소 cap/100 USDT 상한을 사용합니다. 목적 DEMO_VALIDATION, LONG, OPPOSITE_QUOTE LIMIT1건으로 제한합니다. 재사용 의도는 고유 시작 표식과 목적/방향/종목/진입 스타일이 일치해야 하며 정리만 수행합니다. 다른 활성 의도는 거부합니다.

SL 조립 직전에 own position/account/symbol/side/stop/workingType과 이미 확인된 로컬 체결·무보호 노출, 동일 SL 의도의 SUBMITTING 상태를 검사한 뒤 ValueError를1회 주입합니다. 실제 거래소 거부 응답을 꾸미지 않습니다. 원래 coordinator의 SL 효과 경로가 NOT_SENT/LOCAL_VALIDATION_FAILED와 EMERGENCY/paused를 기록합니다. 별도 observer가 원래 reduce-only POST 직전에 그 증거·진입 차단·TP 미생성을 확인합니다. 이 observer의 진단 오류는 종료 전송을 막지 않습니다. 정리 시작 후의 종료는 자동 대응 증거로 세지 않습니다.

성공은 원래 자동 종료 관찰, LIMIT 진입1건, reduce-only 종료1건, SL/TP POST0건, FILLED/VERIFIED_FLAT, 미해결 이력·기준금 보존, 최종 EMERGENCY/paused, 세션/감사 실패 없음이 모두 필요합니다. 모호한 종료를 다시 제출하거나 flat으로 확정하지 않습니다. 기존 자동 대응이 동작하지 않아 도구의 명시적 비상 정리만 성공하면 NOT_ACCEPTED입니다.

임시 build_protection 교체와 exit observer는 프로세스 안에서만 적용되고 세션과 DB worker를 drain한 뒤 복원합니다. 기존 lease60초, 시계 검증, 펜스, 스냅샷 일치, 손익/기준금 및 기본 주문 설정을 바꾸지 않았습니다. 결과 감사와 CLI lease 해제까지 확인해야 성공으로 반환합니다.

## 시험

실제 격리 PostgreSQL과 localhost WebSocket을 사용하며 거래소 SDK 전송은 합성 fixture입니다. 자동 비상 종료·NOT_SENT 증거·진입 차단·무보호 잔량0·기준금 보존, 미체결 무주입, 같은 의도 무재진입, UNKNOWN 종료 무재POST 및 재실행 무재POST, 후속 정리를 자동 대응으로 오인하지 않음, 진단 읽기 오류가 원래 종료를 막지 않음, 현금 이동 및 감사 실패 거부, 준비 실패 무주문, 함수/observer 복원, 명시적 플래그/LIVE 거부, own SL/TP 전송 장벽을 검사합니다.

클라우드에서 인증된 거래소 요청이나 주문은 실행하지 않았습니다. Windows 실제 시험은 아직 실행 전입니다. 실제 거래소 SL 거부 응답, 외부 알림 전달, 부분체결 보호 성공, 주문/포지션 worker SIGKILL, 호스트/DB 장애까지 검증한 것으로 확대하지 않습니다. Phase4 전체 완료·main 병합·LIVE·Phase5 전환을 하지 않습니다.
