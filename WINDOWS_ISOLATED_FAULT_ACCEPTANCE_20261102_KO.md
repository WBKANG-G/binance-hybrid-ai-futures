# Windows 격리 장애 검사 및 정리 실제 완료

사용자가 공유한 빌드·검사·로그 및 down 결과 화면을 근거로 한다. 배포 windows-demo-isolated-fault-check-20261102 / 4f0464256d03dcd9f80e38e138640e6e8437fa2b, ZIP SHA256 2cbb2d7e0555270b3e8dfb7046737b449a4390eeff9a3e72c6c6b15a1bcfd4bf다. 원본 캡처나 개인 키/메일 주소를 포함하지 않는다.

전용 프로젝트 hybrid-phase4-fault-check에서 이미지가 빌드됐고, 테스트 DB는 Healthy 후 검사를 실행했다. 의도한 DB 연결 종료 FATAL4회와 합성 보호 worker 시험이 실행됐다. checks 결과는13 passed in64.13s, collected/passed13, failed/skipped0, ISOLATED_SYNTHETIC_FAULT_CHECK_PASSED 및 종료 코드0이었다.

PROTECTED_WORKER_RESULT_JSON:

- child_exit_code -9 / finally_marker_absent true.
- remaining_at_wait_start_ms59963 / natural_wait_ms59974 / lease_until_unchanged true.
- protection_probe_count31 / fence1→2.
- stale_effect_and_renew_blocked true / wss_connections2.
- entry_posts_before_cleanup1 / protection_posts2.
- same_entry_and_protection_ids_recovered true / final_flat VERIFIED_FLAT.
- real_exchange_calls0.

숫자는 사용자의 Windows Docker 실행 관측이며 클라우드의64.56초/59968ms와 구분한다. 펜스1→2도 전용 테스트 schema의 값이지 실제 운영 장부의 펜스가 아니다. 합성 거래소/전용 DB 범위에서 DB 연결 단절·보호 중 child SIGKILL·자연 리스 인계·같은 ID 복구를 수락한다. 실제 Binance 포지션·WSS/TLS/listen-key·거래소 서버 장애나 DB 전원 손실의 수락으로 확대하지 않는다.

후속 down 결과는 checks 컨테이너와 fault-db 컨테이너, fault_only 네트워크 각각 Removed였으며3/3 정상 정리됐다. 이미지 삭제 명령은 없었다. 따라서 다음 독립 검사는 같은 검증 이미지와 별도 DB를 재사용할 수 있다. 운영 hybrid-windows 자원에 down을 적용하지 않았다.

마지막 실제 금융 상태는11월2일 일간·주간 기준금4998.99761493 USDT/NORMAL·일시정지/리더 반환 완료였다. 이번 격리 결과는 현재 실제 계좌를 새로 조회한 증거는 아니다. 실제 부분체결 보호·TP 체결/잔존 SL 정리·실제 원문 WSS 수집 재생과 Phase4 전체 완료/LIVE/Phase5는 별도다.
