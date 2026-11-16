# 16일자 합성 키 검토 v2: 준비와 서명 권한의 수명 분리

사용자의 v1 실행은 preflight를 통과한 뒤 PRIVATE_READ_EXPIRED_OR_CLOCK_CHANGED로 중단됐다. Docker inspect 화면은 시작 2026-11-16T05:08:45.537740973Z, 종료 05:12:29.893704677Z, Exit 2, OOM false를 보여준다. 총 224.356초에는 시계 대기와 여러 자료 검사가 포함된다. 실제 lease 시작 시각이 남지 않았으므로 75초 초과와 시계 변화 중 정확한 원인은 아직 확정하지 않는다. 화면상 새 DB 정지·합성 키 소멸·원본 미시작·HTTP/주문 0은 보고됐으며 이를 독립 검증 결과로 승격하지 않는다.

v1은 70초 hold 이후 lease를 열지만, 그 뒤에 주간 기준 확인과 여러 전체 DB/runtime snapshot 및 보조 자료 설정을 실행한다. 큰 자료를 가진 PC에서는 서명과 무관한 이 준비 시간도 75초 기한을 소모한다. v2는 서명 권한 없는 SyntheticPrivatePreparation을 먼저 사용한다. 이 객체는 credential 객체를 보유하지 않으며, 고정된 합성 pair만 허용한다. readonly/noexec/nosuid/nodev tmpfs, UID/mode, 단일 파일, 같은 FD와 경로, HMAC 지문, SH lock, PID·scope·boot·wall/monotonic 연속성과 원래 파일 만료를 검사한다. 준비는 300초로 제한되며 그 사이에도 등록/삭제는 잠금으로 거부한다.

자료 준비가 끝나고 capture에 들어갈 때만 acquire_once가 실제 PrivateReadCredentialLease를 한 번 만든다. 새 lease가 보는 파일의 identity와 바이트를 준비 객체가 유지한 자료와 대조한 뒤 준비 객체를 닫는다. 실제 서명·응답·보관 경로의 75초 기한과 1초 시계 차이 제한은 유지한다. 실패한 acquire도 객체를 소진하며 자동 갱신/재시도는 없다. capture가 끝나면 lease는 닫히며 이후 내용·원장 보존 검사는 서명 권한 없이 수행한다. 기존 eager lease API와 기본 합성 파이프라인의 동작은 유지했다. 기존 오류 문자열도 보존하고 만료·시계 역행·boot 변경·wall/monotonic 차이·파일 수명 오류를 고정된 private_timing_reasons로 구분한다. 키·서명·파일 내용이나 실제 시각은 오류 상세에 넣지 않는다.

Windows 패키지는 이전 실패 화면의 5개 helper ID를 config에 고정하고, 현재 Docker 메타데이터에서 각 역할·이미지·정지 상태·restart no·network none·교차 마운트 집합을 확인한다. private helper의 RO 키/키 전용 registrar/새 runtime·socket과 원본 볼륨 제외를 확인한다. 기존 실패 사본을 시작하거나 재사용하지 않는다. 이전 백업·복구·시계·증거 영수증의 고정 해시를 확인한 뒤 새 볼륨·키 tmpfs로만 실행한다. 파일은 새 v2 폴더와 COPY_PRIVATE_WEEK_V2_RECEIPT.json에 남긴다. 원본 정지와 USB 분리를 유지하고 실제 키·HTTP·주문·기간 채택은 허용하지 않는다.

준비에 200초가 경과하는 합성 시계 시험은 서명 직전 lease가 시작돼 한 번의 capture가 성공하는지 확인한다. 실제 lease가 75초를 넘거나 파일/clock이 달라지면 거부하고, 잘못된 pair는 보조 자료 변경 전에 거부하는 회귀 검사를 유지한다. handoff 중 파일 변경은 새 lease를 닫으며, 실패 후 acquire 재시도도 거부한다. 네이티브 PowerShell 검사는 이전 helper가 실행 중이거나 image/project/mount/restart가 바뀌면 새 자원 생성 전에 차단한다.

클라우드 Docker 전체 시험의 입력은 새 합성 보존 파일이다. 이전 resource 집합은 같은 시험에서 완료한 v1 복사본으로 대체하며 실제 사용자의 실패 사본을 읽거나 시작하지 않는다. Windows ACL과 입력 발견은 Linux용 대체 함수를 사용한다. 따라서 실제 Windows·별도 PC·호스트 swap 보호나 사용자 실패 사본 내용에 대한 독립 검증으로 표현하지 않는다. 실제 사용자 재실행 결과는 전달 후 확인해야 한다.
