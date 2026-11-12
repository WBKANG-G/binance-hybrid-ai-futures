# 조회 감사 코드 이미지 설치 ZIP

- 후보 소스: `be724f557c023516f48f96a81d95474569036fb3`
- ZIP: `windows-demo-read-code-install-20261112.zip`
- SHA256: `9c041031dcb7db3b4a29851a81606b9e5f4c2d28a3d7b685c546b8e22670d933`

다른 조회·복구·설치 명령이 종료된 상태에서 바탕화면에 압축을 해제하고 CMD로 실행한다.

```cmd
cd /d "%USERPROFILE%\Desktop\windows-demo-read-code-install-20261112"
RUN_INSTALL.cmd
```

API를 잠시 중지한 뒤 기존 설정·영구 볼륨을 보존한 새 로컬 이미지로 교체한다. 새 모듈 6개만 추가하며 기존 기준 파일 88개는 유지한다. 금융 원장·감사 이력·저널·공유 체크포인트를 쓰지 않고, 설치된 운영 점검 명령을 실제 `/app` 소스에서 실행해 검증한다. 거래소 HTTP 호출·주문 활성화는 없다. 저장소가 volume/bind에 연결되지 않았거나 소스·데이터가 검증 조건과 다르면 교체 전에 거부한다. 사후 검증 실패 시 원래 API 복원을 시도하고 데이터 재검증 결과를 따로 표시한다.

최종 성공은 `READ_CODE_IMAGE_INSTALLED_AUDIT_VERIFIED_KEEP_PAUSED_NO_ORDERS`다. 원래 컨테이너는 백업으로 남기고 자동 재시작을 끈다. 새 API는 원래 restart policy를 유지한다. 이미지 태그·백업·원래 restart policy·데이터 검증 해시는 `INSTALL_STATE.json`에 기록한다. 향후 과거 Compose 파일로 다시 배포할 때는 새 이미지 태그를 명시적으로 반영해야 한다. 실패한 후보나 이미지를 자동 삭제하지 않으며 오류 시 같은 명령을 무조건 반복하지 않는다.

관련 검사 109개(신규 15개), Ruff·포맷·새 2개 모듈 strict mypy가 통과했다. 별도 합성 계정·영구 볼륨·기존 Docker 이미지에서 실제 PowerShell 설치 성공과 사후 검사 실패 주입에 따른 원본 API 복원을 검증했다. 모든 기존 DB 행·원장·공유 저장소·복구 백업·기존 소스 파일은 실행 전후 일치했고, 성공 경로에는 지정한 6개 소스 파일만 추가됐다. 보관/실패 컨테이너의 restart policy는 no로, 실제 실행 API는 원래 policy로 확인했다. 설치 성공 후 ZIP의 guard payload를 Docker에서 직접 실행해 설치된 운영 명령 검증도 확인했다.

Docker 시험은 Linux PowerShell 7과 `.cmd`의 네이티브 셸 대역으로 진행했다. 후보 ZIP·payload·Dockerfile·설치 PowerShell과 컨테이너 복제/복원 코드는 실제 묶음의 바이트를 사용했다. Windows CMD/PowerShell 5.1 및 사용자 PC에서의 설치는 아직 실행하지 않았다. 상세 결과는 `DOCKER_VALIDATION.json`에 있다.
