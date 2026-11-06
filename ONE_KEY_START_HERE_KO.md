# API 키 한 번 입력 후 재사용

이 문서가 이전 ZIP의 키 입력 안내보다 우선합니다. 처음 입력한 DEMO API Key와 Secret은 보호된 RAM 세션에 등록되고, 이후 검토·적용·점검 명령에서 다시 입력하지 않습니다. 유효기간은 최초 등록부터 12시간이며 재사용해도 연장되지 않습니다. API 컨테이너를 재생성하는 동안 key-session 컨테이너가 계속 실행되면 키가 유지됩니다. Docker/컴퓨터 재시작, 모든 해당 RAM 마운트 해제 또는 만료 후에는 다시 입력해야 합니다. 영구 저장 기능은 아닙니다.

## 1. 새 ZIP 빌드

새 windows-demo-one-key-session 폴더에 압축을 풉니다. 그 폴더의 탐색기 주소창에서 cmd를 실행합니다. 아래 프로젝트 이름을 그대로 사용해 기존 DB와 export 볼륨을 유지합니다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait
```

api, db, web, key-session이 모두 Healthy인지 확인합니다. 이번 변경은 API에 붙어 있던 기존 임시 메모리를 새 공유 RAM 마운트로 바꾸므로 최초 한 번은 키 입력이 필요할 수 있습니다.

## 2. 한 번 입력

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -m scripts.demo_key_session --ensure
```

DEMO API Key와 Secret을 각각 한 번 숨김 입력합니다. 유효한 키가 이미 등록되어 있으면 입력을 요구하지 않습니다. 정상 출력은 demo_key_session: AVAILABLE이며, 키 값은 출력하지 않습니다. 이 명령은 거래소 요청을 보내지 않습니다.

기존 --register는 등록된 키를 덮어쓰지 않는 명령입니다. 반복 실행에는 --ensure를 사용합니다. BLOCKED라면 error_code를 포함한 결과만 공유하고 키를 보내지 않습니다. 만료·잘못된 파일·부분 환경변수는 자동 교체하거나 우회하지 않습니다.

## 3. 다음 단계: 주문 없는 준비 상태 점검

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -T api python -m scripts.check_demo_session_ready
```

여기서는 키를 다시 묻지 않습니다. 정상 결과는 session_ready: READ_ONLY_READY_ENTRIES_STILL_PAUSED입니다. 현재 일간/주간 기준일 일치, 로컬 NORMAL/paused·포지션 없음, 거래소의 두 번 연속 운영 스냅샷과 비어 있는 상태를 확인합니다. DB를 수정하거나 주문·취소·청산·신규 진입을 실행하지 않습니다. 전체 JSON 결과를 공유합니다.

날짜가 또 바뀌었거나 거래소/장부 상태가 달라지면 BLOCKED가 됩니다. 이때 기준금 재설정이나 거래 재개를 자동 수행하지 않습니다. 최신 적용 결과는 2026-11-01 일간 기준금 4999.03492670 USDT, NORMAL/paused입니다. 주간 기준금과 고점은 보존된 상태입니다.

## 키 수명과 보호 범위

RAM 마운트는 64KiB tmpfs이며 디렉터리 0700/사용자10001, 파일0600, noexec/nosuid/nodev 검사를 유지합니다. key-session은 네트워크와 포트가 없고 RAM을 읽기 전용으로 붙여 API 재생성 동안 마운트를 유지합니다. 키를 .env, DB, 이미지, 호스트의 일반 파일에 저장하지 않습니다. tmpfs 안의 값은 암호화된 금고가 아니며 같은 사용자/root와 호스트의 메모리·스왑·스냅샷 보호에 의존합니다. 12시간 경과 후에는 사용을 거부하지만 만료만으로 파일이 자동 삭제되지는 않습니다.

세션을 수동 종료할 때만 아래 명령으로 RAM 파일을 지울 수 있습니다. 이번 다음 단계 점검에는 실행할 필요가 없습니다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -T api python -m scripts.demo_key_session --clear
```

DB/export 볼륨 삭제나 docker compose down -v를 사용하지 않습니다. 실제 Windows에서 API 재생성 후 재사용은 이번 준비 상태 결과를 확인한 뒤 별도로 검증합니다. 로컬 Docker에서 합성 키로 검증한 결과와 구분합니다.
