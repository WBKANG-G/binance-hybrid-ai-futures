# 설치 사전 검사 진단 ZIP

- 소스: `c0da3ad078c46adadb704aaf6c88ad95f17ae396`
- ZIP: `windows-demo-read-install-diagnostic-20261112.zip`
- SHA256: `4c31166ffcb766af9d5583f04e21316d2cae6d21bc6c6f61b3d20013b9bf3871`

앞선 PC 출력은 PRECHECK에서 일반 오류로 차단됐으며 API 교체는 시작되지 않았다. 이 ZIP은 같은 사전 검사를 읽기 전용으로 실행하면서 단계와 오류 종류를 구분한다. API 교체·이미지 빌드·감사 적용은 하지 않는다. 설치 기능과 Dockerfile/payload 디렉터리는 포함하지 않는다.

바탕화면에 압축 해제하고 CMD에서 실행한다.

```cmd
cd /d "%USERPROFILE%\Desktop\windows-demo-read-install-diagnostic-20261112"
RUN_DIAGNOSE.cmd
```

출력 전체를 공유한다. `operation_stage`, `failure_category`, 가능한 경우 SQLSTATE/errno와 비공개 자원 해시 식별자를 표시한다. 오류 원문·SQL·파라미터·경로·파일 내용·키는 출력하지 않는다. 진단 성공이어도 설치 완료를 뜻하지 않는다. 사용자 PC의 실제 오류 원인은 아직 확정되지 않았다.

관련 검사 122개와 변경 3개 모듈 strict mypy/Ruff/포맷 검사가 통과했다. 실제 진단 ZIP을 기존 Docker 이미지의 별도 합성 계정에서 실행해 정상 사전 검사와 DB 권한 오류 주입(SQLSTATE 42501) 분류를 확인했다. 기존 DB 행·저널·소스 파일·컨테이너 ID와 이미지가 유지됐다. 실제 사용자 PC 또는 거래소 계정에서 실행한 결과는 아니다. PowerShell은 Linux PowerShell 7에서 검사했으며 Windows CMD/PowerShell 5.1 결과는 사용자 실행으로 확인한다.
