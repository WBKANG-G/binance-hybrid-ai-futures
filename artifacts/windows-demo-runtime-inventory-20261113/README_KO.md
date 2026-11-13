# Windows 런타임 읽기 전용 진단 ZIP

- ZIP: [windows-demo-runtime-inventory-20261113.zip](windows-demo-runtime-inventory-20261113.zip)
- SHA-256: `b8d084c3301362421fbdc8fe8e1381d7c65ed7872dde1f7a1581eec622af63ca`
- 소스: `454edc8b940dbf07f0c15d4d97de6e8cd1ee2949`
- 마지막 설치 기준: `fb371aeabcc9c4611d7b7d20def1a4e3edeaa41d`

ZIP을 바탕화면에 압축 해제하고 CMD에서 한 번 실행합니다.

```cmd
cd /d "%USERPROFILE%\Desktop\windows-demo-runtime-inventory-20261113"
RUN_INVENTORY.cmd
```

`RUN_INVENTORY.cmd`가 있는 폴더 경로를 사용합니다. 출력 전체를 공유해 주세요. 키 입력은 필요하지 않습니다.

이 ZIP은 설치·컨테이너 재생성/재시작·이미지 빌드·DB 변경·시계 복구·키 세션·공유 저널·거래소 HTTP·주문을 수행하지 않습니다. 새 후보 모듈/테이블의 미설치는 현황으로 표시하며 자동 적용하지 않습니다. 코드 원문·키·DB URL·원장 본문·원시 Docker 정보는 출력하지 않습니다. DB 연결 설정만 컨테이너 프로세스 안에서 사용합니다.

출력의 orders_enabled/execution_approved=false는 진단이 주문 권한을 부여하지 않는다는 뜻입니다. 시스템 전체의 실행 설정이나 원격 계정 상태를 인증하지 않습니다. 후보 테이블도 이름의 존재만 확인하며 무결성·적용 완료를 뜻하지 않습니다.

전체 411개 검사와 실제 ZIP의 33개 재검증이 통과했습니다. 기존 설치 이미지의 별도 합성 계정에서 root filesystem read-only/사용자 10001로 실행해 기준 Python 파일 89개 일치와 후보 9개 미설치·상이 상태를 확인했습니다. PowerShell 7의 가짜 Docker/네이티브 명령 경계 10개 검증, 실제 Docker worker 2개, 읽기 전용 PG 9개, 단위 검사 12개를 포함합니다. 실제 사용자 Windows 상태를 검증한 결과는 아닙니다. Windows CMD/PowerShell 5.1은 사용자 실행 결과로 확인해야 합니다.

보관 방식은 별도 기기·오프라인 선택을 유지합니다. 이 패키지가 참조를 만들거나 기기로 복사하지는 않습니다. 세부 실행 설명은 ZIP 안 README_KO.md, 검증 기록은 VALIDATION.json 및 DOCKER_VALIDATION.json을 참고하세요.
