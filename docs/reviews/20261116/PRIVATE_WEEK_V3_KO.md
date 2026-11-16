# Windows PowerShell 5.1 Decimal 결과 검증 호환

사용자 v2 화면은 PRIVATE_LATE_LEASE_RESULT_INVALID를 보고한다. 합성 GET/보관과 내용·보존 검사 단계 이후 PowerShell 결과 검증에서 중단됐으며, 화면상 clone_stopped/private_key_session_destroyed는 true, originals_started는 false, HTTP/주문은 0이다. 이어 사용자 PC의 PowerShell 5.1.26100.9818은 JSON 소수 1.236의 자료형을 System.Decimal로 출력했다. 같은 전체 결과 객체에서 Double 1.236은 v2 검증을 통과하고 Decimal 1.236은 거부되는 문제를 클라우드에서 재현했다. 실제 v2 준비 시간 값 자체는 저장되지 않아 알 수 없으므로 모든 조건의 성공을 단정하지 않는다.

v3는 결과 검증의 Decimal 누락을 수정한다. Double/Decimal/Int32/Int64만 허용하고 문자열·bool·null·NaN·양/음의 무한대를 거부한다. 서명 직전 lease 사용 여부는 bool true여야 한다. 준비 시간 범위는 정확히 0 이상 300 미만이며, 서명 75초 제한이나 파일·시계·기간 검사 및 갱신 금지는 바꾸지 않는다. 실패 원인, 숫자 자료형, 유한하고 제한된 시간 값만 진단에 기록한다. 잘못된 임의 문자열/객체를 출력하지 않는다.

이전 v1 실패 도우미 5개 검사를 유지한다. v2는 화면에서 확인한 reader ID eb09666ae4235987563455557aca103bbc38018d0776f553fbb7cd0e2b8a0b43로 로컬 FAILURE.json을 한정해 찾는다. 해당 폴더/파일의 기존 ACL·reparse 검사와 read sharing을 사용한다. 실패 분류·정지/키 소멸/재시도 금지·보존된 2+3개 ID 형식을 확인하고, 실제 Docker에서 같은 역할·이미지·정지 상태·restart no·network none·상호 마운트 집합을 다시 확인한다. 첫 집합과의 ID 별칭도 거부한다. 이 로컬 기록은 외부 독립 해시 증명이 아니며, 이전 두 사본을 시작하거나 복구 자료로 사용하지 않는다.

Python worker·후보 16개·복구 worker·cold archive·SQL은 게시된 v2 ZIP과 바이트가 같다. 새 Docker lifecycle이나 PostgreSQL 로직을 추가하지 않고 PowerShell 결과 검증/읽기 전용 prior 검사와 진단·파일 이름만 변경했다. v2에서 통과한 실제 Docker 경로와 200초 준비/75초 서명 기한 검증을 재사용하고, v3에서는 네이티브 형식·범위·기존 보안 조건·둘째 실패 기록·seal 변조를 시험한다. PowerShell 7에서 Decimal 객체를 명시적으로 넣어 검증하며 실제 Windows PowerShell 5.1 실행을 주장하지 않는다.

새 폴더 copy-private-week-v3-20261116-*와 COPY_PRIVATE_WEEK_V3_RECEIPT.json을 사용한다. 원본과 두 실패 사본은 정지, USB는 분리 상태를 유지한다. 실제 키·거래소 통신·주문·기간 채택·설치 승인은 없다. 한국 날짜 16일 전용이다.
