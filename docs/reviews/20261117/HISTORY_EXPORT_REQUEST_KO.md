# 데모 주문 이력 내보내기 요청 실행기

사용자의 고객지원 외 대안 조사 후 다음 단계 지시에 따라 구현했다. 독립된 Windows
PowerShell 실행기이며 기존 원본/복사본 컨테이너·DB·writer를 시작하거나 수정하지 않는다.
키를 로컬 숨김 입력하고 공식 데모 호스트만 접근한다. 키/Secret은 파일·환경변수·명령줄에
저장하지 않으며, 프로세스 메모리 보호의 한계를 README에 명시했다.

공식 SDK 고정 커밋과 월간/가중치 근거는 NON_SUPPORT_RECOVERY_SOURCES.json에 있다.
주문 내보내기 생성 GET /fapi/v1/order/asyn은 명시적 RequestExportOnce만 허용한다.
일반 조회와 구분해 다운로드 작업과 월간 쿼터를 소비함을 사용자에게 표시한다.
월간 잔여 횟수는 조회하지 않았고 데모 서버 지원도 실제 확인 전이다.

## 수집 경계

- 고정된 manifest/archive를 검증하고 원장 34개/미확정 요청 4개의 읽기 도우미를 재사용한다.
- 과거 보고서의 Filled ENTRY 두 건이 대조 기준이다. 새 키의 서명 조회에서 두 원래
  client ID 해시·서버 ID·종목·side·LIMIT·Filled·origQty·executedQty·price를 모두 대조한다.
  현재 키의 거래소 응답과 과거 요청의 연결이며, 전체 계정 이력의 독립 증명이 아니다.
- 서버 시각 1회, exchangeInfo 1회, 진입 주문 2회, 내보내기 생성 1회가 최대다.
  상태 조회 모드는 서버 시각 1회와 저장된 downloadId 조회 1회만 보낸다.
- 현재 분당 REQUEST_WEIGHT 한도(상한 6000)와 마지막 anchor 응답의 사용량을 확인한다.
  한도에서 현재 사용량을 뺀 여유가 1200보다 작으면 생성하지 않는다. 외부 IP 사용을
  완전히 통제하거나 기존 managed-weight 저널을 갱신했다고 주장하지 않는다.
- HTTP GET 고정, HTTPS demo-fapi.binance.com 고정, 시스템 TLS 검증과 TLS1.2 사용,
  자동 리디렉션·LIVE 호스트 fallback·자동 재시도·자동 폴링 없음이다.
- 새 보호 NTFS 폴더와 현재 사용자/SYSTEM ACL 요구를 기존 검증된 helper에서 재사용한다.
  생산 코드에는 Linux ACL 우회가 없다. 임의 URL 또는 셸 명령으로 키를 전달하지 않는다.

## 생성/저장 실패 경계

네트워크 직전에 전역 ORDER_EXPORT_ATTEMPT_20261117.json을 CreateNew+Flush와 읽기
대조로 기록한다. 동시 실행이어도 같은 guard는 원자적 새 파일 하나만 허용한다.
기존 guard가 있으면 생성은 중단한다. 삭제/복구/자동 만료 경로가 없다.
타임아웃·HTTP 오류·생성 응답 누락·downloadId 저장 실패도 재생성하지 않는다.
유효한 JOB.json이 있을 때만 기존 작업 상태를 한 번 조회할 수 있다.
같은 API key의 SHA256과 원래 manifest 연결, 보호 디렉터리, 작업 ID를 다시 확인한다.

거래소 오류 본문과 예외 메시지를 출력하거나 저장하지 않는다. 성공 JSON에 키/Secret
원문이 포함되면 저장 전에 거부한다. 원래 client ID와 download URL은 비공개 파일에만
들어가며 콘솔은 고정 분류·횟수·개인 폴더 경로만 출력한다.

## 실제로 하지 않은 일

클라우드에서는 합성 서버 응답과 PowerShell 7의 실제 HMAC/요청 구성/저장 경계만
시험했다. 실제 Binance HTTP, 실제 키, Windows 5.1 TLS·숨김 입력·ACL의 양성 실행은
없다. Windows 사용자 실행 전에는 성공했다고 기록하지 않는다.
내보내기 생성 성공은 CSV 확보·자료 완전성·알고 주문 포함·자금 흐름 확보·과거 요청
해소·기준금액 재구성·거래 재개를 의미하지 않는다. 자동 다운로드도 하지 않는다.
이 단계가 성공하면 저장된 동일 작업 ID에서 원문 확보 경로를 이어서 검토한다.
