# Windows 배포 전 검토 ZIP

- 파일: `windows-demo-read-deployment-review-20261112.zip`
- SHA256: `30a48a1c79a6e083a639e76be1c6c7be880ae3d18a01bf05ff5dbdf4a668023c`
- 후보 소스: `163424c51875b87dc23fd4552064115a0a9c7b42`
- 기존 runtime 기준: `e03b6ba683732cd5872c30cf0d245a5b1fb6d325`의 설치 파일 88개

바탕화면에 압축을 해제한 뒤 CMD에서 실행한다.

```cmd
cd /d "%USERPROFILE%\Desktop\windows-demo-read-deployment-review-20261112"
RUN_REVIEW.cmd
```

사용자 PC의 Docker Desktop과 API 컨테이너가 정상 실행 중이어야 한다. 명령은 기존 컨테이너의 고정 ID에서 별도 Python 프로세스로 후보 소스를 실행하며, 현재 API 프로세스의 설치 소스를 교체하지 않는다. 감사 연결은 계획만 하고 테이블 생성·기간 변경·주문 활성화를 하지 않는다. 출력 전체로 다음 배포 조건을 판단한다.

관련 자동 검사 72개(신규 29개)가 통과했다. PowerShell 검사는 Linux PowerShell 7과 가짜 Docker 메타데이터를 사용했다. Windows CMD/PowerShell 5.1 및 사용자 PC에서의 실행 결과는 아직 없다.

실제 ZIP의 바이너리 표준입력 경로도 보관된 기존 Docker 이미지와 별도 합성 계정에서 검증했다. 준비된 startup 시각을 사용한 사전 검토이며 자연 경과 시간 검사를 다시 수행한 것은 아니다. 소스·모든 합성 DB 테이블·공유 저널의 실행 전후 해시가 일치했고 후보 임시 파일이 정리됐다. 상세 결과는 `DOCKER_VALIDATION.json`에 기록했다. 실제 거래소 HTTP 호출은 0회이며 사용자 PC·계정에는 적용하지 않았다.
