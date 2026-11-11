# TP 제어 코드만 설치 — 주문 정지 유지

Windows 점검 결과 누락된 핵심 제어 파일 11개와 의존성을 설치합니다. 대상은 e03b6ba683732cd5872c30cf0d245a5b1fb6d325의 의존 파일 66개입니다. 설치 기준 소스 5abc9f6c27f330136e318abf9f9bd4f958398763 대비 새 파일은 15개이고 기존 51개는 같은 바이트입니다. 기존 코드에 알 수 없는 변경이 있으면 덮어쓰지 않고 중단합니다.

ZIP 안의 `windows-demo-runtime-code-install-20261111` 폴더를 바탕화면에 놓고 CMD에서 실행하세요.

```bat
cd /d "%USERPROFILE%\Desktop\windows-demo-runtime-code-install-20261111"
RUN_INSTALL.cmd
```

실행 순서:

1. 전송 파일 해시, 현재 API 컨테이너, 기존 Compose 프로젝트 설정을 확인합니다.
2. 설치된 핵심 소스 55개, 의존 파일, 11월 11일 적용 영수증·날짜·정지 상태를 검사하고 계정/이벤트/outbox 해시를 기록합니다.
3. 현재 API 이미지에 로컬 보관 태그를 붙이고, 그 이미지를 기반으로 코드를 추가합니다. 패키지 설치·외부 이미지 다운로드는 없습니다.
4. DB 상태를 다시 확인하고 기존 Compose 설정에 이미지와 비활성 플래그만 추가하여 API 컨테이너만 교체합니다. DB·key-session·web·alerts 컨테이너를 시작하거나 교체하지 않습니다. 기존 볼륨·네트워크·포트 설정은 Compose 설정을 재사용합니다.
5. 의존 파일 66개, 모듈 가져오기, 실행 기본 차단, 같은 계정/이벤트/outbox 해시를 확인합니다.

성공 결과는 `POSTCHECKED_CODE_ONLY_KEEP_PAUSED` 및 `CODE_ONLY_INSTALL_VERIFIED_KEEP_PAUSED_STORAGE_NOT_PROVISIONED`입니다. 출력 전체를 공유해 주세요.

이번 단계는 이미지 코드 설치입니다. 주문 한도/공유 예산 저장소 생성·DB 등록, 리스 획득, 거래소 API 호출, 주문 생성, 일간/주간 기준 변경, 거래 재개는 수행하지 않습니다. 설치 후에도 두 저장소는 NOT_PROVISIONED이며 실행 승인은 false입니다. 다음 단계는 저장소 구성입니다.

원래 Compose 파일과 작업 폴더가 남아 있어야 합니다. 찾을 수 없거나 중단 코드가 나오면 반복 실행하지 말고 출력을 공유하세요. 원본 이미지는 보관 태그로 남고 해당 정보는 이 폴더의 INSTALL_STATE.json에 기록됩니다. 성공 후에도 이 폴더와 runtime-image.override.json을 보관하세요. 이 설정을 제외한 예전 Compose 명령으로 다시 배포하면 이전 이미지가 선택될 수 있습니다.

검증: 격리 PostgreSQL 기반 20개 검사, Ruff 통과. 설치 전/후 실제 Docker 이미지에서 동일한 합성 적용 원장의 사전/사후 가드와 DB 해시 보존을 확인했습니다. 새 Docker 이미지를 네트워크 없이 빌드하고, 기본 실행 거부를 확인했습니다. 실제 거래소 호출 및 주문은 0회입니다. Windows PowerShell 오케스트레이션은 정적 계약 검사만 수행했으며 실제 Windows의 Compose 교체는 이 환경에서 실행하지 못했습니다. 설치 전후 가드가 Windows에서도 검증을 수행합니다. 과거 1,872개 전체 회귀 검사를 이번에 다시 실행했다고 주장하지 않습니다.

재현용 테스트는 evidence/에 있으며 Linux 프로젝트 테스트 환경, 격리 PostgreSQL, 지정 Docker 이미지가 필요합니다. 테스트용 적용 도구는 합성 원장 준비용이며 RUN_INSTALL.cmd는 실행하지 않습니다.
