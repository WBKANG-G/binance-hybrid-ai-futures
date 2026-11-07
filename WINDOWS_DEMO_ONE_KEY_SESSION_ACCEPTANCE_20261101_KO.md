# Windows RAM 키 재사용 및 API 재생성 검증

근거는 사용자가 공유한 두 차례 CMD 결과 캡처다. 배포는 windows-demo-one-key-session-20261101 / 7e290f1이며 ZIP SHA256은 6965ed1b33a34383876b3b04d32fba5e79b56f75e53e05d6e0126d084258d2b5다. 원본 스크린샷 자체를 이 파일에 저장한 것은 아니다.

첫 빌드에서 api/db/web/key-session이 Healthy였고 demo_key_session_ram 볼륨 생성이 확인됐다. --ensure에서 키와 Secret을 숨김 입력한 뒤 demo_key_session AVAILABLE, maximum_lifetime_hours 12, persistent_key_storage false가 출력됐다. 이후 -T 준비 점검은 키 입력 없이 READ_ONLY_READY_ENTRIES_STILL_PAUSED를 반환했다.

운영자가 --no-deps --force-recreate --wait api를 실행했고 API Healthy(6.6초)를 확인했다. 그 뒤 -T 준비 점검도 키 입력 없이 같은 성공 결과를 반환했다. 두 결과 모두 credential_source PRIVATE_TMPFS_SESSION, period_keys_match true, 일간 stored/current 2026-11-01, 주간 stored/current 2026-10-26, NORMAL/paused true, exchange_empty_verified true, database_changed false, orders_enabled/live_trading_enabled false였다.

첫 점검은 signed GET22, HTTP22, HTTPS 연결1/재사용21, 서버 close0, 전송1548ms, 예산 내 clock probe0이었다. API 재생성 후 점검은 같은 요청/연결 수와 전송1521ms를 출력했다. clock_probe_count는 읽기 예산 내부 수치이며 프로그램 전체의 사전 시계 동기화가 없었다는 뜻은 아니다.

판정은 실제 Windows에서 최초 숨김 입력 후 별도 CLI의 캐시 재사용과 API 단독 재생성 후 재사용 범위 통과다. 컨테이너 ID 비교나 파일 원문/만료시각을 사용자 캡처에서 별도로 관측한 것은 아니다. Docker/호스트 재시작 후 유지 또는 12시간 이후 재사용을 주장하지 않는다. 이후 키는 만료 또는 전체 메모리 마운트 해제 전까지 재사용할 수 있다. 거래 재개나 Phase4 전체 완료 판정은 포함하지 않는다.
