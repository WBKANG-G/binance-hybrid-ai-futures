# 웹 Codex 작업을 Windows 로컬 Codex로 이어가기

웹 세션은 Linux에 연결되어 있었습니다. Windows에서 Codex CLI를 실행하면 새 로컬 세션이 해당 PC의 터미널과 Docker를 사용할 수 있습니다. 웹 대화가 자동 이전되는 것은 아니므로 LOCAL_CODEX_HANDOFF.md를 읽게 하세요.

공식 Windows 설치 명령(OpenAI Codex README에서 확인):

```bat
powershell -ExecutionPolicy ByPass -c "irm https://chatgpt.com/codex/install.ps1 | iex"
```

설치 뒤 CMD를 닫았다가 새로 열어 프로젝트 폴더에서 codex를 실행하고 Sign in with ChatGPT로 로그인하세요. Codex CLI의 /permissions 명령에서 로컬 작업 권한을 설정할 수 있습니다. 이 첫 설치와 로그인은 PC에서 직접 해야 합니다.

출처:
- https://github.com/openai/codex#installing-and-running-codex-cli
- https://github.com/openai/codex#using-codex-with-your-chatgpt-plan

로컬 세션에 전달할 지시:

> LOCAL_CODEX_HANDOFF.md를 읽고 현재 Windows Docker 상태를 확인한 뒤 완료된 설치를 반복하지 말고 다음 개발을 진행해. 수정·격리 테스트·결과 검토를 자율적으로 이어가고 실제 거래와 주문 차단은 유지해.
