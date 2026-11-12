# 조회 코드 설치 V2 — 이력 조회 시간초과 개선

- 소스: `fb371aeabcc9c4611d7b7d20def1a4e3edeaa41d`
- ZIP: `windows-demo-read-code-install-v2-20261112.zip`
- SHA256: `788f97733f44d22809435d80466a27dc54d011817da48cfef5626a6443f2f3dc`

PC 진단의 `DATABASE_TABLE_READ`, `DATABASE_TIMEOUT`, SQLSTATE 57014와 자원 ID `4398a2bf945bcb1e`는 `demo_events` 이력 조회 단계에 해당한다. V2는 전체 이력을 한 번에 JSON으로 수집하는 대신 기본키 순서로 한 행씩 읽어 누적 해시를 계산한다. 마지막 행까지 포함하며 원장/이력 삭제·일부 생략을 하지 않는다. SQL별 2초 제한은 유지하고 행당 16MiB·테이블당 1,000,000행·검사당 공통 60초 예산을 적용한다. 기본키나 제한 조건이 맞지 않으면 부분 해시를 성공으로 반환하지 않는다.

다른 조회·복구·설치 명령이 종료된 상태에서 새 ZIP을 바탕화면에 별도로 압축 해제하고 CMD에서 실행한다.

```cmd
cd /d "%USERPROFILE%\Desktop\windows-demo-read-code-install-v2-20261112"
RUN_INSTALL.cmd
```

기존 컨테이너 설정과 영구 볼륨을 보존해 새 모듈 6개만 추가한 로컬 이미지로 API를 잠시 교체한다. 금융 원장·감사 이력·저널·자원 체크포인트를 쓰지 않는다. 설치 전후 V2 해시와 실제 설치된 명령을 검증하며, 사후 검사 실패 시 원래 API와 restart policy 복원을 시도하고 데이터 재검증 결과를 표시한다. 백업/실패 컨테이너는 중지 상태에서 restart policy를 no로 유지한다. 거래소 HTTP 호출·주문 쓰기·실행 승인·실거래 활성화는 없다.

관련 검사 129개가 통과했다. 합성 행별 지연으로 같은 200ms 제한에서 기존 일괄 조회의 실제 57014 실패와 새 조회의 전체 80행 처리를 비교했다. 40MiB 이상 및 10,001행 이력의 마지막 변경 검출, 복합키·인용부호·한글 키·행 순서·크기/행/시간 제한도 확인했다.

실제 V2 ZIP의 Docker 설치 시험에서는 40MiB 이상 합성 금융 이력, 기존 감사 저장소와 복구 백업을 구성했다. SQL별 2초 제한을 유지한 정상 교체·실제 설치 명령 검증과, 사후 실패 주입에 따른 원본 API 복원 모두 통과했다. 모든 기존 DB 행·저장소·기존 소스는 유지됐고 성공 경로의 새 파일은 지정한 6개뿐이었다. Docker 시험은 Linux PowerShell 7과 native CMD 대역을 사용했다. 실제 Windows CMD/PowerShell 5.1 및 사용자 PC에서의 해결 여부는 이번 실행 결과로 확인해야 한다.

성공 마지막 줄은 `READ_CODE_IMAGE_INSTALLED_AUDIT_VERIFIED_KEEP_PAUSED_NO_ORDERS`다. 이미 같은 소스가 설치돼 있으면 `READ_CODE_ALREADY_INSTALLED_VERIFIED_KEEP_PAUSED_NO_ORDERS`로 무교체 검증한다. 출력 전체를 공유한다.
