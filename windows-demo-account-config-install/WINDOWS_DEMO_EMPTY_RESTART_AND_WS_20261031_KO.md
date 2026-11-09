# 기존 Windows 실제 빈 API 재시작·정상 WS 관찰

사용자 터미널 캡처에 근거한 기록입니다. 설치판은 windows-demo-multi-execution-fix-20261031, commit62e9720, ZIP SHA256 b2a3dc813f8f70e9c62a3423f3c2ce3e6809684fabfd308baa1902e1124f7677입니다.

실제 PASSIVE 작업1394c054-7da1-4113-a8ef-ddb37ac1e285는0.0007 BTC 전량 체결, SL/TP 조회 확인, 최종 청산과 FINISHED_FLAT을 확인했습니다. session_error=false, filled_protection_and_flat_verified=true입니다. 전량 체결이므로 부분 체결 수락으로 세지 않습니다. 그 전 실제0.0006 부분 체결 실패는 포함된 WINDOWS_DEMO_PARTIAL_EVIDENCE_20261031_KO.md에 보존돼 있습니다.

후속 복구 감사는37건/5묶음, 서명GET172회, 시각1회, 미해결2건, 위험PASS, 포지션0/활성 주문0을 확인했습니다. 토큰308d3a9b80689931461e6a9f5aef8219484bfead0355167ef11b202fc6c03c8b, 작업569c5414-363a-4f65-b346-d4c8af61c837로 복구 적용이 성공했고 진입 정지는 유지됐습니다.

사용자가 API 컨테이너를 restart한 뒤 up -d --wait api에서 DB/API Healthy를 확인했습니다. 메모리 키를 숨김 재등록했고, 기본 run_demo preflight는 READ_ONLY, account_reconciled=true, private_stream_ready=false, orders_enabled=false, LIVE=false, risk_state=NORMAL, BTCUSDT ISOLATED/2배를 출력했습니다. 이 단계는 WS에 연결하지 않으므로 stream_ready=false는 연결 오류가 아닙니다.

이후 --enable-demo --check-private 실행 캡처는 CONNECTED, account_reconciled=true, private_stream_ready=true, entry_signal_supplied=false, LIVE=false를 출력했고 OBSERVATION_FINISHED로 정상 종료했습니다. 약20초 연결 관찰을 완료한 증거이며 실제 강제 단절 주입은 포함하지 않습니다. 사용자 요청에 따라 다음 단계로 전용 빈 계정 단절·재연결 검사를 추가합니다. 신규 도구의 실제 사용자 실행 결과는 아직 없습니다.

기존 기준은 일일4999.35095801 USDT(10월31일), 주간4999.64804199(10월26일), 고점5000입니다. 초기 기준/과거 손실을 리셋하지 않습니다. 원본 WS 장기 보관·열린 주문 중 재시작·실제 부분 체결 보호·무인 장기 운영과 전체 Phase4 완료를 주장하지 않습니다.
