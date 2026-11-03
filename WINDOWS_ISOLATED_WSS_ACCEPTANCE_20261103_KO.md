# Windows 격리 WSS 검사와 자원 정리 실제 완료

사용자가2026-11-03 대화에서 공유한 CMD 결과를 근거로 한다. 실제 스크린샷의 검사 실행 시각은 이 출력에 없으므로 대화 수신 날짜와 구분한다. 원본 캡처·개인 키·메일 주소·서명 URL은 저장하지 않는다.

배포 windows-demo-isolated-wss-check-20261102 / 0275364879e2179b823db42b84aa16a793bd8c83. ZIP SHA256 bfe0326d35d6592ffaed64ba65854390c8ceb740304e746caa2223bb7e3aac79. 재사용 이미지의 소스 배포는 windows-demo-isolated-fault-check-20261102 / 4f0464256d03dcd9f80e38e138640e6e8437fa2b다.

## 실제 확인 결과

- 시험 DB fault-db Healthy 후 one-off checks 컨테이너 실행.
- 45 passed in6.67s.
- wss_check ISOLATED_SYNTHETIC_WSS_CHECK_PASSED.
- collected/passed45, failed/skipped0, checked_source_files9.
- scope SYNTHETIC_EVENTS_AND_LOOPBACK_WS_WITH_DEDICATED_PG_ONLY.
- captured_real_wss_frames_replayed/real_demo_account_verified false.
- real_exchange_calls0, orders_enabled/live_trading_enabled false.

이 값은 사용자 Windows 결과이며 클라우드의5.25초와 구분한다. 누적 체결의 중복/과거 이벤트 처리, inbox 역순·병합·잘못된 프레임·overflow, 시각 검증과 로컬 WS/PG worker 경합의 합성 검사 범위를 수락한다. 실제 원문 캡처 재생으로 확대하지 않는다. 기본 앱은 원문을 지속 저장하지 않으며 장부 감사 payload를 실제 WSS 원문으로 대체할 수 없다.

## 경로 오류와 정리

작은 실행 ZIP 폴더에는 infra가 없었다. 처음 경로 추정으로 실행한 Compose 명령들은 파일을 찾지 못해 검사를 시작하지 않았다. 사용자 where 조회로 바탕화면의 '새 폴더/windows-demo-isolated-fault-check/infra/docker-compose.phase4-fault-check.yml' 실제 경로를 확인한 뒤 해당 파일을 명시해 정상 실행했다. 이 경로 오류를 앱이나 DB 회복 실패로 기록하지 않는다.

후속 down은 fault-db 컨테이너와 fault_only 네트워크가 Removed,2/2였다. one-off checks는 --rm으로 실행 후 제거됐다. 운영 hybrid-windows에 down을 적용하지 않았다. 이전13개 독립 장애 검사와 그 정리 성공도 보존하며, 두 범위의 결과를 실제 Binance 보호 포지션 장애나 Phase4 전체 완료로 합치지 않는다.

## 다음 실제 검증 전 상태

마지막 실제 금융 적용은2026-11-02 기준 일간·주간 각4998.99761493 USDT, NORMAL/paused, 리더 반환 완료였다. 운영자 확인+서명 USDT 증거 출처이며 계좌 전체 export 검증이 아니다. 이제 한국시간11월3일이므로 현재 운영 컨테이너와 로컬 기준일은 새로 읽어야 한다. 현재 저장 기준일·시각·계좌 상태를 아직 재조회한 것으로 주장하지 않는다.

수정 후 실제 부분체결 보호, 실제 TP 체결/잔존 SL 정리, 실제 캡처 원문 재생·장기 운영 및 실제 Binance 포지션의 worker/DB 서버 장애는 별도 미완료다. 기존 실패·UNKNOWN/NOT_SENT·미해결 export 요청을 지우거나 신규 진입/LIVE/Phase5를 활성화하지 않는다.
