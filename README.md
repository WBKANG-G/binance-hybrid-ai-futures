# 다음 단계: Windows Docker의 독립 장애 복구 검사

후속 사용자 Windows 실행에서13개 통과(64.13초), failed/skipped0 및 종료0을 확인했고, 이후 테스트 컨테이너2개와 네트워크1개가 정상 제거됐습니다. 실제 자연 대기59974ms/원래 리스 잔여59963ms, 보호31회 확인, 펜스1→2와 동일 ID 회복/중복 없음/최종flat을 확인했습니다. 아래 배포 당시 Windows 미확인 문장은 이 후속 관측으로 갱신되며, [수락 기록과 다음 독립 WSS 검사](https://github.com/WBKANG-G/binance-hybrid-ai-futures/tree/windows-demo-isolated-wss-check-20261102)에 별도로 남겼습니다.

사용자 실제 일간·주간 기준금 적용이 성공했습니다. 두 기준일2026-11-02, 두 기준금4998.99761493 USDT, NORMAL/paused 및 고점·이전 평가 시각 보존·리더 반환 완료입니다. 미해결 기록 승인이나 신규 진입/LIVE 활성화는 없었습니다. 두 잘못된 토큰으로 차단된 이전 시도와 이후 성공을 별도로 기록했습니다.

이번에는 실제 DEMO 계좌를 그대로 두고 기존 보호 worker 강제 종료와 DB 연결 단절 검사를 Windows Docker에서 별도 재현합니다. ZIP을 새 windows-demo-isolated-fault-check 폴더에 풀고 ISOLATED_FAULT_START_HERE_KO.md의 전용 hybrid-phase4-fault-check 명령을 사용하세요. 운영 프로젝트를 다시 빌드할 단계가 아닙니다. 키/비밀번호 입력은 없습니다.

전용 tmpfs DB와 internal 네트워크만 쓰고 호스트 포트·운영 볼륨·키 RAM을 연결하지 않습니다. 검증 의존성32개의 버전을 기존 uv.lock과 대조했고1779개 파일 해시·라이선스/배포 metadata를 포함했습니다. Python3.12/Linux AMD64 테스트 전용 이미지이며 패키지 저장소 접속 없이 빌드합니다. 운영 환경의 의존성은 변경하지 않습니다.

클라우드에서 최종 오프라인 Dockerfile을 network=none으로 실제 빌드하고, 같은 Compose에서13개 모두 통과(64.56초)·실패/skip0을 확인했습니다. child 실제SIGKILL-9, 원래60초 리스 자연 만료59968ms, 보호31회 확인, 펜스1→2, 같은 진입/SLTP ID 채택, 중복 없음과 최종flat을 확인했습니다. 가드7개와 Ruff/포맷444개·mypy125개 소스도 통과했습니다. 초기 DNS 실패 및 자식 의존성 배치 실패와 최종 수정 결과는 검증 문서에 보존했습니다.

부모485개 파일은 모두 바이트 그대로입니다. 새 전용 파일10개만 추가했습니다. 텍스트 패치는9개 새 텍스트 파일을 포함하고, 의존성 tar.gz는 ZIP에서 manifest SHA256 검증 후 복사하는 이진 자산입니다. 이 조합으로 전체495개 파일을 재현·대조했습니다. 전체 앱 회귀는 다시 실행하지 않았습니다.

사용자의 Windows 실제 격리 실행 결과는 아직 확인 전입니다. 이 검사는 실제 Binance 보호 포지션에서 worker나 DB 서버를 종료한 수락이 아닙니다. 실제 부분체결 보호/TP 체결·SL 정리, 실제 서버 전체 정지·전원 손실, 장기 관찰과 원본 WSS 재생은 별도입니다. 이 합성 결과로 Phase4 전체 완료/main/LIVE/Phase5를 선언하지 않습니다.
