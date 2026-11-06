# 보호 worker 감독 프로세스의 경쟁 리더 검사 수정

이번 패키지는 **코드 설치만** 합니다. 새 진입, 자식 강제 종료, EMERGENCY 승인, 진입 재개를 실행하지 않습니다. 기존 계좌는 EMERGENCY/일시정지를 유지합니다. LIVE 거래는 비활성화 상태를 유지합니다.

Windows 스마트 앱 컨트롤 설정은 그대로 두세요. 이 ZIP에는 `.cmd` 실행기를 넣지 않았습니다. 새 폴더에 압축을 푼 뒤 명령 프롬프트에서 표준 Docker 명령으로 설치합니다. 기존 작업 폴더와 결과 로그는 보존하세요.

## 설치

압축을 바탕화면에 풀었다면:

```cmd
cd /d "%USERPROFILE%\Desktop\windows-demo-protected-worker-probe-fix"
```

아래 파일 무결성 검사가 `TRANSFER_FILES_VERIFIED`로 끝나는지 확인하세요. 오류가 나면 다음 명령을 실행하지 마세요.

```cmd
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; $m=Get-Content -Raw 'TRANSFER_MANIFEST.json' | ConvertFrom-Json; foreach($p in $m.files.PSObject.Properties){if((Get-FileHash -Algorithm SHA256 -LiteralPath $p.Name).Hash.ToLower() -ne $p.Value){throw 'SOURCE_HASH_MISMATCH'}}; Write-Output 'TRANSFER_FILES_VERIFIED'"
```

아래 한 줄은 읽기 전용 사전 검사에 성공해야 API 이미지를 갱신하고, 갱신 후 다시 읽기 전용 검사를 수행합니다. `&&` 때문에 앞 단계가 실패하면 다음 단계로 넘어가지 않습니다.

```cmd
docker exec -i hybrid-windows-api-1 python - --phase pre < guard_serial_fix_install.py && docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --no-deps --wait api && docker exec -i hybrid-windows-api-1 python - --phase post < guard_serial_fix_install.py
```

완료 화면을 보내주세요. 마지막 결과는 `probe_fix_guard: POSTCHECKED_KEEP_PAUSED`여야 합니다. 기존 실패 작업 ID를 재실행하거나, 과거 복구 스크립트를 다시 적용하지 마세요. 설치 이후의 복구 판단은 별도로 현재 증거를 대조해야 합니다.

사전/사후 검사는 최신 실패 작업 `e85a00f2-6b8e-4b39-bac2-b958a001c8c0`의 전체 체결, 보호 확인 기록, 준비/정상 finally 기록, 청산 완료, 현재 로컬 및 거래소 빈 상태, 일시정지와 활성 리더 없음을 요구합니다. 실제 데이터가 다르면 설치를 차단합니다. 검사 중에는 계좌 상태와 owner/fence/lease가 바뀌지 않아야 합니다. 정식 복구 검사의 토큰은 출력하지 않습니다.

## 관측 결과와 수정 범위

사용자 실행에서 준비 마커는 `FULL_PROTECTED_READY`, 자식 실패 기록은 `STREAM_OBSERVATION/NONE`이었습니다. 감독 프로세스는 `DATABASE_LOCK_TIMEOUT`으로 실패했고, `kill_count=0`, 자식 종료 코드 0, 최종 `VERIFIED_FLAT`, EMERGENCY/일시정지였습니다. 이 결과는 강제 종료 검증 성공이 아닙니다.

해당 구간의 경쟁 리더 검사는 실제 `DemoStore.acquire`를 호출합니다. 스트림 처리의 DB 트랜잭션이 행을 잡고 있으면, lease 유효 여부를 판정하기 전에 기존 2초 행 잠금 대기 제한으로 `55P03`이 발생할 수 있습니다. 같은 호출에서 이 조건을 실제 격리 PostgreSQL로 재현했습니다. 실제 Windows 실행의 스택 트레이스는 없으므로 경합이 발생한 정확한 호출을 단정하지 않습니다.

수정본은 `55P03`을 성공으로 처리하지 않습니다. 제한된 관찰 창 안에서 잠금 대기가 끝난 뒤 **실제 활성 lease 거절**을 받아야 경쟁 리더 차단으로 인정합니다. 그 외 DB 오류, 자식 종료, owner/fence 변경, lease 만료는 실패로 유지합니다. 계속 바쁘면 강제 종료하지 않습니다. 관찰 창은 15초이며 이미 시작한 기존 2초 DB 호출과 고정된 재검사 시간이 추가될 수 있습니다. 무제한 재시도는 없습니다.

대기 후 실제 진입·손절·익절·포지션과 동일 ID를 서명 조회로 다시 확인하고, 자식과 lease가 유효해야 강제 종료 단계로 넘어갑니다. 거래소 주문, 진입, 보호 주문은 재시도하지 않습니다. 탐색 시도 수·잠금 시간 초과 수·강제 종료 전 실패 단계를 결과에 추가했습니다.

기존 2초 DB lock timeout, 60초 lease, 자연 만료 대기, fencing, WSS 처리, freshness, 위험 기준과 설정은 변경하지 않았습니다. 잠금이 잡힌 상태를 허용하거나 WSS 처리를 중지하여 검증을 통과시키지 않습니다.

메일을 별도로 생성하거나 전송하는 설치 작업은 없습니다. 기존에 대기 중인 경고가 있다면 이미 실행 중인 알림 worker의 기존 처리에 따라 전송될 수 있습니다.
