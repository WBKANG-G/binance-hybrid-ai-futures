# Windows에서 처음 실행하기

이 묶음은 현재 클라우드 작업 폴더의 개발 파일을 옮긴 것입니다. GitHub에 아직 반영되지 않은 파일도 포함합니다.
클라우드 게시를 고치는 파일이 아닙니다. 사용자 PC에서 별도의 개발 환경을 만듭니다.
Windows에서 실제 실행한 결과는 아직 없습니다. 사용자 PC에서 아래 순서로 확인해야 합니다.

## 1. Docker Desktop 설치

공식 사이트: https://www.docker.com/products/docker-desktop/

- Windows용 Docker Desktop을 설치합니다. 일반 Intel/AMD PC는 AMD64, ARM 기반 PC는 ARM64입니다.
- 설치 과정에서 WSL 2 사용 선택이 나오면 기본 권장 설정을 따릅니다.
- 재부팅 안내가 나오면 재부팅합니다. 그 뒤 Docker Desktop을 열고 Engine running 상태가 될 때까지 기다립니다.
- Docker Desktop이 이미 설치돼 있고 실행 중이면 다시 설치할 필요가 없습니다.
- 설치가 안 되면 표시된 오류를 대화에 알려주세요. 보안 기능을 임의로 끄지 마세요.

별도로 Python, Node.js, Git을 설치할 필요는 없습니다. 필요한 구성은 Docker가 준비합니다.
Docker Desktop의 Windows/WSL 요구사항: https://docs.docker.com/desktop/setup/install/windows-install/

## 2. 압축 풀기

ZIP 파일을 마우스 오른쪽 버튼으로 눌러 '압축 풀기' 또는 '모두 추출'을 선택합니다.
예시 위치는 C:\BinanceDev 입니다. ZIP 내부에서 바로 실행하지 마세요.
압축을 푼 폴더에서 START-WINDOWS.cmd와 infra 폴더가 함께 보이는지 확인합니다.

## 3. 실행

START-WINDOWS.cmd를 더블 클릭합니다.
최초에는 Python/Node/PostgreSQL 이미지와 필요한 패키지를 내려받아 시간이 걸립니다.
'실행 완료'가 나오면 Windows 브라우저 주소창에 http://localhost:5173 을 입력합니다.
API 키는 입력하지 않습니다. 이 단계는 화면과 로컬 DB를 실행할 뿐, 자동매매를 시작하지 않습니다.
웹 화면은 사용자 PC에서만 접근하도록 구성되어 있습니다.

Windows 파일 확장자가 숨겨져 있으면 START-WINDOWS라는 이름의 Windows 명령 파일로 표시될 수 있습니다.
실패하면 창을 닫기 전에 마지막 오류 부분을 복사하거나 캡처하여 대화에 보내주세요.
포트 사용 중 오류가 나면 다른 프로그램을 무작정 종료하지 말고 오류를 알려주세요.

## 4. 키 없는 공개 연결 확인

실행 완료 뒤 CHECK-DEMO.cmd를 더블 클릭합니다.
결과의 rest와 websocket이 모두 PASS인지 확인하고 결과를 대화에 보내주세요.
이 작업은 공개 시간 조회와 공개 시세 수신만 하며, 키·서명·주문을 사용하지 않습니다.
브라우저에서 serverTime이 보인 것만으로 Docker 내부의 REST/WSS 연결까지 검증되는 것은 아닙니다.
연결 실패 시 보안 해제나 VPN 설치 없이 오류부터 확인합니다.

## 5. 종료와 다음 실행

STOP-WINDOWS.cmd를 실행하면 프로그램을 종료하고 개발 DB는 보존합니다.
다음에 다시 쓸 때는 Docker Desktop을 켠 뒤 START-WINDOWS.cmd를 실행합니다.
Docker Desktop의 volume 삭제 또는 docker compose down -v는 실행하지 마세요.
컴퓨터가 절전/종료되면 프로그램은 계속 동작하지 않습니다.

## 보존 범위와 안전 설정

- 기존 소스, 명세, 테스트, 잠금 파일, 마이그레이션을 복사했습니다. 파일 목록과 SHA-256은 TRANSFER_MANIFEST.json에 있습니다.
- API 키, .env 실제 설정, Git 인증 정보, 클라우드 캐시, 의존성 폴더는 옮기지 않습니다.
- 클라우드 PostgreSQL DB 및 .cache의 기존 실행 보고서는 이 묶음에 포함되지 않습니다. 원본 클라우드는 그대로 보존되어 있습니다.
- Windows에는 별도의 새 개발 DB를 만듭니다. 기존 클라우드 환경을 삭제하지 마세요.
- LIVE_TRADING_ENABLED=false, PAPER 모드이며 DEMO/LIVE 거래 worker는 자동 실행하지 않습니다.
- Docker 설정에 있는 dev/dev는 로컬 개발 DB 전용 계정이며 Binance 계정 정보가 아닙니다.
- 환경변수/API 키는 클라우드에서 Windows로 자동 이전되지 않습니다. 지금은 키를 추가하지 마세요.
- 실제 DEMO 인증/주문 수락 검증은 아직 미완료이며 Phase 5는 시작하지 않습니다.
