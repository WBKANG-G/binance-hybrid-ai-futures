# Windows 개발 실행 ZIP 다운로드

이 브랜치는 다운로드 배포 전용입니다. main을 변경하거나 병합하지 않습니다.

## 다운로드와 실행

1. `binance-hybrid-ai-futures-windows.zip` 파일을 클릭합니다.
2. 파일 화면 오른쪽 위의 **Download raw file**(다운로드 아이콘)을 누릅니다.
3. 다운로드한 ZIP을 마우스 오른쪽 버튼으로 클릭하고 **모두 추출**을 선택합니다.
4. 압축을 푼 폴더의 `WINDOWS_START_HERE.md` 안내서를 읽습니다.
5. Docker Desktop을 설치하고 실행한 뒤 `START-WINDOWS.cmd`를 더블 클릭합니다.
6. 시작 성공 후 `CHECK-DEMO.cmd`로 키 없는 공개 REST/WSS 연결을 확인합니다.
7. 종료할 때는 `STOP-WINDOWS.cmd`를 실행합니다. DB 볼륨은 보존합니다.

PAPER 모드, LIVE_TRADING_ENABLED=false입니다. 실제 API 키, 실제 .env, .git, 캐시 및 의존성 폴더는 포함하지 않습니다. 예제 설정의 dev/dev는 로컬 개발 DB 전용입니다.

원본 소스 168개의 전체 SHA-256은 b695f70a2f47bcf6d301c9cf388095215a9b20472ff51305eeff95997e740afc와 일치합니다.
ZIP CRC, manifest 전체 파일 체크섬, Compose 설정과 실행 스크립트를 정적으로 점검했습니다. Windows에서의 실제 실행과 DEMO 네트워크 연결 성공은 아직 검증하지 않았습니다. Phase 5는 시작하지 않았습니다.
ZIP SHA-256은 SHA256SUMS.txt를 참고하세요.
