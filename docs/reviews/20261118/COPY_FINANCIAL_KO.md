# 새 격리 사본의 SQL 재무 대조

사용자는 복원 원장 보고서 연결 성공 화면을 보내고 사본 SQL 재무 대조의 다음 단계 진행을
지시했다. 이번 도구는 고정 백업에서 새 사본을 복원하며 원본·기존 사본을 시작하지 않는다.
기존 검증된 fresh-copy 복원/마운트 검증/정상 종료 제어 명령은 변경하지 않는다.
기존 추가 앱 진단 worker는 제거하고 고정 읽기 전용 SQL만 두 번 실행한다.

이미 사용자 화면에서 성공한 원본 비교 helper ID 하나를 고정한다. 이 helper를 기존 원본
scope에서 구별할 때마다 고정 API 이미지·원본 두 volume의 RO·networknone·rootRO·cap·user·
entrypoint·중지·종료0을 검사한다. 해당 ID 이외 컨테이너는 숨기지 않고 원래 scope 검사에
전달한다. 임의 역할/이름/label 기반 예외는 없다. helper가 없거나 상태가 바뀌면 중단한다.

고정 성공 복원 영수증을 읽기 공유로 유지하고 원장 PG 지문을 기대값으로 얻는다.
고정 runtime의 이전 금융 행·day13 채택 기록 및 원래 요청 계획을 검증한다. 새 사본 SQL은
일·주 기준금액, 고점, 가용금액, 초기/잔차실현/평가/fees/funding/순입금 구성, 평가시각,
key/count/paused/risk 상태·reconciled/stream_ready·last_reconciled 및 해시화된 미확정
요청 상태만 출력한다. 전체 state는 출력하지 않고 PG state::text SHA256으로만 비교한다.
이 지문이 고정 성공 복원 보고서와 같고 금융 값이 저장 기록과 같아야 성공한다.

SQL은 repeatable-read read-only, 제한된 search_path/statement/lock timeout을 사용한다.
재무 항목을 쓰는 SQL/앱·HTTP·키·거래는 없다. DB WAL 복구와 PID 정리는 새 사본에만
발생한다. 사본 DB를 정상 종료하고 private 보고서와 새 자원을 보존한다. 자동 삭제나 재시도는 없다.

성공해도 최초 자금의 경제적 증빙이나 자정 경계 평가액, 과거 미확정 요청 접수 결과를
증명하지 않는다. 금융 변경 전 값의 사본 직접 조회와 복원 지문 연결이 검증 범위다.
전체 신규 원장 전이 검증·source 수락·기준금액 채택·원본 SQL 조회·재개 승인은 하지 않는다.
새 적용 경로/기존 7일 제한 우회도 구현하지 않는다.

테스트는 합성 원장과 새 로컬 Docker PostgreSQL을 사용한다. 실제 사용자 볼륨·키에
접근하지 않는다. Windows5.1/NTFS ACL의 양성 실행은 사용자 결과를 관측하기 전까지
검증되었다고 표시하지 않는다. 원본 Docker scope/복원 제어의 기존 검증 근거는 고정 소스로 유지한다.
