# 설치 사전 검사 오류 분류

PC 출력에서 `PRECHECK`, `READ_INSTALL_REVIEW_UNAVAILABLE`, `api_replacement_started=false`가 확인됐다. API 교체와 이미지 빌드는 시작되지 않았다. 일반 오류 코드만으로 실제 원인을 확정할 수 없으므로 같은 설치를 반복하지 않고 읽기 전용 사전 검사로 상세 분류를 수집한다.

ZIP을 바탕화면에 압축 해제하고 CMD에서 실행한다. 기존 설치 명령이 종료된 상태여야 한다.

```cmd
cd /d "%USERPROFILE%\Desktop\windows-demo-read-install-diagnostic-20261112"
RUN_DIAGNOSE.cmd
```

이 묶음에는 설치 실행 명령, Dockerfile, 설치 payload, 컨테이너 교체 도구가 없다. 현재 API의 고정 ID에서 비공개 임시 후보 소스를 실행해 `pre` 검사만 한다. 기존 원장·감사 이력·저널을 변경하거나 API를 중지/교체하지 않는다. 임시 파일 생성·정리와 짧은 DB 잠금은 기존 사전 검사와 동일하다. 거래소 HTTP 호출과 주문 쓰기는 0이다.

오류 출력에 `operation_stage`, `failure_category`를 추가한다. DB 오류는 가능한 경우 SQLSTATE를, OS 오류는 errno 숫자를 표시한다. `resource_id`는 테이블 이름 또는 저장소 상대 경로의 SHA256 앞 16자리이며 원문 이름/경로는 출력하지 않는다. 예외 메시지·SQL·파라미터·DB URL·파일 내용·키를 출력하지 않는다. 알 수 없는 오류는 계속 일반 코드로 차단하며 위험한 추정 복구는 하지 않는다.

출력 전체를 공유한다. 성공은 `READ_INSTALL_PRECHECK_VERIFIED_NO_API_REPLACEMENT`이며 이 경우에도 설치 완료를 뜻하지 않는다. 실패 시 상세 분류를 확인한 뒤 원인을 수정해야 한다. 이번 단계는 진단 기능 개선이며 실제 PC 오류 원인을 해결했다고 주장하지 않는다. 실행 승인·주문·실거래는 계속 false다.

관련 검사 122개가 통과했다(기존 109개와 신규 13개). SQLSTATE·OS errno·타입 분류, 메시지/경로/SQL/파라미터 제한, 단계·해시 식별자와 문맥 정리, 설치 기능이 제외된 ZIP 구조, PowerShell의 inspect 및 pre 검사만 실행하는 경로를 확인했다. Ruff·포맷 검사와 변경 3개 모듈 strict mypy도 통과했다. PowerShell은 Linux PowerShell 7에서 검사했으며 실제 Windows CMD/PowerShell 5.1 실행 결과는 아직 없다.
