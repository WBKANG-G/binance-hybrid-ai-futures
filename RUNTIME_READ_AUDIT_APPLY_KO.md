# 기존 공유 저널에 조회 감사 저장소 최초 적용

앞선 PC 출력의 `CANDIDATE_PLAN_VERIFIED_KEEP_PAUSED`, `startup_ready=true`, `audit_schema_required=true`, `audit_binding_required=true` 이후 단계다. 현재 등록된 공유 저널과 체크포인트는 유지하고 보조 감사 테이블·불변 트리거·최초 바인딩만 생성한다. API 컨테이너나 설치된 소스를 교체하지 않는다. 이 작업은 실제 조회 전송 경로를 활성화하지 않는다.

## 실행 조건과 명령

같은 공유 범위의 조회·복구·후보 검토 프로그램이 실행 중이지 않아야 한다. 다른 CMD에서 이전 명령이 아직 실행 중이면 종료를 기다린다. API는 기존 일시정지·주문 비활성 상태를 유지한다. 별도 reader를 실행했다면 먼저 중지한다. `RUN_APPLY.cmd` 실행은 이 조건을 확인한 운영자의 명시적 적용이며 내부 `--readers-stopped` 확인으로 기록한다. 별도 프로세스 중지를 독립 증명하거나 자동 중지하는 기능은 없다.

ZIP을 바탕화면에 압축 해제한 뒤 CMD에서 실행한다.

```cmd
cd /d "%USERPROFILE%\Desktop\windows-demo-read-audit-apply-20261112"
RUN_APPLY.cmd
```

설치 파일 88개와 후보 소스, 기존 저장소 설정·공유 바인딩 영수증을 검증한다. 최초 감사 연결은 기존 저널의 total=0·요청 행 없음·stopped=0 조건을 요구한다. 계정 owner 없음, 일시정지·NORMAL·로컬 무포지션, 현재 기간 일치, 시계·대기 완료, 원본 복구 백업과 독립 체크포인트 일치도 검사한다. 조건이 달라졌으면 변경 전에 차단하며 이 묶음에서 시계·기간을 자동 수정하지 않는다.

## 변경과 사후 검증

감사 스키마와 바인딩은 하나의 PostgreSQL 트랜잭션에서 생성된다. 바인딩 실패 시 새 테이블까지 함께 롤백한다. 계정 행·금융 이벤트·outbox·기간·기준금액·공유 SQLite·독립 자원 체크포인트는 쓰지 않는다. 기존 감사 연결이 이미 있으면 전체 검증 후 같은 기록을 유지한다. 손상되거나 일부만 존재하는 스키마는 자동 보수하지 않는다.

적용 후 별도 읽기 전용 검사를 다시 실행하고 `audit_postcheck_passed=true`를 확인한다. 최종 성공 문자열은 `AUDIT_SCHEMA_AND_BINDING_CONFIGURED_KEEP_PAUSED_NO_ORDERS`다. 오류 또는 사후 검증 실패 시 적용 시도 여부를 표시하고 자원 변경 여부는 추정하지 않는다. 자동 재시도·삭제·초기화는 하지 않는다. 출력 전체를 공유한다.

실제 거래소 HTTP 호출·키 세션 접근·주문 쓰기는 없다. 실행 승인·주문·실거래는 계속 false다. 이 단계가 끝나도 `full_deployment_completed=false`이며 운영 API의 새 코드 설치와 조회 경로 연결은 남아 있다.

## 검증

신규 7개를 포함해 관련 검사 94개가 통과했다. 명시적 reader 중지 확인 전 DB 접근 거부, 보조 감사 저장소만 생성, 독립 사후 점검, 재실행 무변경, 적용 오류 정보 제한과 시도 사실 표시, 별도 ZIP 진입점과 복구/적용 혼합 금지, PowerShell 성공/실패 판정을 검증했다. Ruff·포맷 검사와 변경 2개 모듈 strict mypy도 통과했다. DB 검사는 격리 PostgreSQL·SQLite와 합성 계정을 사용했다. PowerShell은 Linux PowerShell 7에서 가짜 Docker 메타데이터로 검증했으며 실제 Windows CMD/PowerShell 5.1의 결과는 사용자 실행으로 확인해야 한다.
