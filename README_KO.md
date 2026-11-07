# 읽기 전용 준비 상태 확인의 수동 청산 이력 조회 축소

앞선 Windows 실측에서 원본28회와 후보22회, 금융·입출금 필드 일치를 확인했습니다. 이 패키지는 그 범위를 **scripts.check_demo_session_ready에만** 적용합니다. 신규 TP 테스트나 진입 재개를 실행하지 않습니다.

## 변경 범위

운영 변경은 준비 상태 확인 스크립트6줄과 별도 readiness_closed_history 모듈입니다. 전역 snapshot 파서, coordinator, SL/TP 보호·삭제·청산·전체 이력 flat 확인, income 파서, 위험 정책과 저장소, DB 마이그레이션, Docker Compose는 기존 배포 e9327bd와 같습니다.

새 모듈은 이번에 검토한 수동 청산 사건041d1f35-870e-46cb-aad2-ed6059bc1095만 대상으로 합니다. NORMAL/일시정지·모든 로컬 노출0·ISOLATED BTCUSDT·2배·읽기 전용 정책과 다음 증거가 모두 맞아야 합니다.

- CLOSED/잔여0/노출0/보호false, 원래 진입 FILLED/수량0.0007, 앱 청산 ID 없음
- 채택 영수증의 출처·진입/청산 주문 ID·두 체결 ID·정확한 순손익−0.07032487
- 원본 주문과 현재 주문의 변경 필드가 채택 당시의 노출·위험·보호·포지션 상태4개뿐임
- 원래 신호·SL/TP ID·실패 이력과 증거 해시 형식, 영수증 시각 및 초기화된 income 범위

증거가 없거나 맞지 않으면 최적화하지 않고 기존 조회를 그대로 수행합니다. 다른 주문이나 미해결 요청을 생략하지 않습니다. 원본 장부는 바꾸지 않고 이번 준비 상태 확인 입력의 깊은 복사본에서 검증된 종료 기록 한 건만 제외합니다. 앱 청산이나 TP 성공 이력을 만들지 않습니다.

## 유지되는 현재 상태 확인

매 실행마다 두 개의 새 snapshot을 조회합니다. 전역 포지션·일반 주문·알고 주문·잔액·증거금·설정·income과 다른 원래 ID 조회는 유지됩니다. 예전 REST 응답을 캐시하지 않습니다. 전역 노출이나 주문이 다시 나타나면 기존 준비 상태 확인이 차단합니다.

원래 진입·SL/TP가 terminal이었다는 채택 영수증을 사용하므로 준비 상태 확인에서 해당3개 과거 ID를 매번 새로 조회하지 않습니다. 설치 사전/사후 가드는 그 원래 ID를 새로 조회하고 terminal 조건까지 다시 확인합니다. 전체 이력 검사와 보호·청산 확인은 계속 원래 ID를 조회합니다.

성공한 준비 상태 확인에는 closed_history_omitted_count=1, closed_history_scope=READ_ONLY_READINESS_ONLY가 추가됩니다. 사용자 계정 상태가 그대로라면 signed_get_count=22가 예상됩니다. 이 숫자는 이력과 페이지 수에 따라 달라질 수 있습니다. GET 횟수 감소가 같은 비율의 IP weight 감소를 뜻하지는 않습니다. 고비용 전역 조회는 그대로 유지합니다.

## 설치 순서

ZIP을 바탕화면 windows-demo-readiness-history 폴더에 풉니다. DB·키 세션·API 컨테이너와 Docker Desktop을 실행 상태로 유지합니다. 현재 등록한 임시 DEMO 키를 사용합니다.

RUN_COMMAND.txt의 두 줄을 CMD에 차례로 붙여 넣습니다. 파일 자체를 실행하는 방식이 아닙니다. 다운로드한 CMD/BAT/PS1/EXE는 없습니다.

명령은 다음 순서로 진행하며 실패하면 이후 단계는 실행하지 않습니다.

1. 고정된 manifest 해시와 모든 제공 소스의 파일 해시 확인
2. 기존 컨테이너에서 읽기 전용 사전 검사 및 장부/fence/리더 상태의 해시를 readiness_precheck.json에 저장
3. 기존 프로젝트 hybrid-windows의 API만 빌드·갱신하고 healthcheck 대기
4. 새 소스31개 해시, 설치된 준비 상태 확인의 입력 뷰, 전역 현재 상태, 원래 진입/SL/TP의 terminal 상태와 원본/후보 두 snapshot pair를 검증
5. 사전 해시와 사후 장부 해시가 같아야 성공

DB/키 RAM 볼륨은 유지하고 다른 서비스를 재시작하지 않습니다. DB 마이그레이션 파일은 기존과 같으며 새 스키마 변경은 없습니다. 가드는 장부를 읽기 전용 트랜잭션으로 읽고 리더를 획득하거나 주문을 생성하지 않습니다.

사전 결과는 PRECHECKED_READY_KEEP_PAUSED, 사후 성공은 **POSTCHECKED_READ_ONLY_READY_KEEP_PAUSED**입니다. account_digest가 일치하고 database_changed=false, orders_enabled=false, live_trading_enabled=false, paused=true여야 합니다. 가드 전체 측정량52회는 원본28+후보22 및 시계/공개 한도 조회를 함께 센 값입니다. 일반 준비 상태 확인의22회와 구분합니다.

오류 시 출력과 readiness_precheck.json을 보존하고 재실행하지 말고 공유합니다. 사후 검사가 실패했을 때는 이미지가 이미 갱신됐을 수 있으므로 installation_verified=false는 설치 여부의 확정 판정이 아닙니다. 자동 복구·재개·재시도를 실행하지 않습니다.

## 검증 결과

단일 실행753개 테스트(단위725, 통합28)가 통과했습니다. 새 조건24개와 준비 상태 확인·설치 가드, 기존 단일 키 흐름 및 TP trigger 경로를 포함합니다. 전체 코드/테스트 Ruff, 132개 소스 mypy, diff 검사를 통과했습니다. 격리 PostgreSQL과 합성 SDK 응답에서 다음을 검사했습니다.

- 준비 상태 확인의28→22회, 다른 미해결 원래 ID 조회 유지, DB 전체 account/event 행 보존
- 다른 일반 operational/full snapshot의 원래 ID 조회 유지
- 전역 노출·미체결 주문·HTTP 제한은 준비 상태 확인 차단
- 불완전/불일치 영수증은 기존28회 조회 유지
- 설치 전후 소스 혼합 거부, 새 모듈 실제 호출 확인, 사전 장부 해시 불일치 시 사후 조회 전 차단

실제 Windows 설치는 이 패키지 실행 전까지 완료되지 않았습니다. 활성 TP의 요청 비용이나 청산 여유를 보장하는 변경은 아닙니다. 주문/LIVE 비활성화와 일시정지는 유지합니다. 실제 부분체결 보호 및 TP 체결 후 원래SL 정리의 승인 증거도 추가되지 않았습니다.

SOURCE_REFERENCE.json에 소스 커밋과 패치 해시, VALIDATION.json과 test-results/에 검사 내역이 있습니다. source.patch는 e9327bd에 적용할 재현용 패치입니다. 기존 main은 변경하거나 병합하지 않았습니다.
