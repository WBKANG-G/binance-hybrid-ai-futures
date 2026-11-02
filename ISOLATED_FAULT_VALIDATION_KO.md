# 독립 장애 검사 배포 검증

부모는 실제 기준금 적용 코드가 포함된 windows-demo-paused-period-apply ZIP / SHA256 ea8383bdc2517b696885ff61c014902ef0ceb8f0a203dc3be6b79a43c2ae40dd다. 부모485개 파일은 바이트 그대로 유지한다. 금융/주문/리스크·기존 테스트·기본 Compose·migration·키/메일·export·MASTER_SPEC를 변경하지 않고 별도 전용 파일만 추가한다.

전용 Compose는 hybrid-phase4-fault-check 프로젝트에 fault-db/checks 두 컨테이너만 만든다. fault-db의 네트워크는 internal:true, 호스트 포트/볼륨/키 마운트는 없다. checks는 service:fault-db 네트워크 namespace를 공유해127.0.0.1의 시험 DB/HTTP/ws만 사용한다. DB 이름hybrid_fault_review와 고정 합성 접속 정보만 허용한다. checks는 root read-only, cap_drop ALL이며 /tmp tmpfs만 쓴다. DB 데이터도 tmpfs다. 실제 inspect로 이 구조를 검증했다. BINANCE 환경/운영 DATABASE_URL/키 디렉터리/틀린 DB URL/LIVE/모드/명시 플래그 없음은 DB 연결 전에 거부하며 검사최종7개 통과(0.12초)다.

배포 Dockerfile은 Python3.12/Linux AMD64에 별도 시험 의존성을 표준 site-packages로 배치한다. 기존 uv.lock의 버전과32개 distribution을 대조했고1779개 파일 SHA256 및 라이선스/배포 metadata를 아카이브에 넣었다. 아카이브 SHA256은 Dockerfile에서도 확인한다. 검사 컨테이너에서 실제 pytest9.1.1/sqlalchemy2.1.5/psycopg3.3.6/pydantic2.14.1/websockets17.2/alembic1.20.0을 확인했다. 이 의존성 묶음은 시험 이미지 전용이며 운영 가상환경을 교체하지 않는다.

처음 온라인 설치 방식은 클라우드 Docker DNS 실패로 패키지 다운로드가 불가능했다. 그 방식은 배포에서 제거했고 최종 오프라인 Dockerfile을 --network=none으로 실제 빌드했다. 중간 런타임은 PYTHONPATH에만 의존성을 둬 자식 worker가 찾지 못해1개 실패/12개 통과했다. 표준 site-packages 배치로 수정했고, collected 집계는 collection_finish 시점의 session.items 길이를 사용하도록 고쳤다. 기존 시험/타임아웃/원래60초 리스/수락 조건은 바꾸지 않았다. 과거 실패를 숨기지 않는다.

최종 컨테이너 실행 결과: 13개 통과(64.56초), failed/skipped0. 실제 child exit-9/finally 표식 없음, 남은 리스59961ms에 대해 monotonic59968ms 자연 대기, lease_until 불변, SLTP 보호 확인31회, 펜스1→2, WSS 연결2회, 인계 전 entry POST1/protection POST2, 동일 ID 회복과 최종 VERIFIED_FLAT 확인. 인증된 실제 거래소 호출0. 전용 runner도 collected/passed13/failed/skipped0으로 수락했다.

최종 Ruff/포맷 및 mypy: Ruff 검사/포맷444개 및 mypy125개 소스 통과. 별도 전용 검사와 가드만 실행했으며 전체 앱 회귀는 다시 실행하지 않았다. 부모의 과거 전체 회귀/분리 실행 및 실패 한계는 원래 문서를 보존한다. 클라우드 결과를 Windows 실제 수락으로 확대하지 않는다.

worker 검사 동안 부모의 공유 합성 거래소·DB·WebSocket 서버는 살아 있다. 실제 서버 전체 전원 손실, 실제 Binance WSS/TLS/listen-key, 미보호·부분체결 중 모든 종료 시점, 실제 TP/SL 트리거는 별도다. DB 단절 시험은 실제 backend 연결 종료이지 서버 전체 정지가 아니다. 나머지 DB 단절 인계의61초는 기존 논리 시계 전진이며 worker의60초 자연 대기와 구분한다. 최종 요약은13개 전부 통과·실패/skip0일 때만 수락한다. 실제 DEMO 계좌 수락/Phase4 전체 완료/LIVE/Phase5는 없다.

의존성 tar.gz는 이진 자산이므로 텍스트 패치에 포함하지 않는다. 패치 재현은 부모에 새 텍스트9개를 적용하고 ZIP의 해당 이진 자산을 manifest SHA256으로 확인해 복사하는 절차다. 이 조합으로 전체495개 파일이 일치하는지 확인한다.
