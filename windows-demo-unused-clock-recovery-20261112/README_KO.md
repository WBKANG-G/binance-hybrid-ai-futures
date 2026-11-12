# 미사용 공유 예산 저널의 부팅·시계 복구

확인된 중단 코드 `SHARED_CLOCK_OR_BOOT_CHANGED`를 위한 제한된 복구입니다. 기존 소스와 저장소를 재설치하지 않습니다.

ZIP 안 폴더를 바탕화면에 놓고 CMD에서 실행하세요.

```bat
cd /d "%USERPROFILE%\Desktop\windows-demo-unused-clock-recovery-20261112"
RUN_RECOVER.cmd
```

## 실제 변경

요청 시도와 누적 사용량이 모두 0인 공유 저널에만 적용합니다. 기존 금융 원장·계정 리스·청산 이력·실패 trial·기간 영수증·일간/주간 기준금액·고점·평가 시각·주문 한도 저널은 변경하지 않습니다. 기간 불일치는 그대로 별도 보고하며 새 무활동 확인을 자동 가정하지 않습니다.

1. 설치 소스 55개와 제어 코드 의존 파일 66개, NORMAL·정지 상태·계정 리스 없음, 기존 금융/기간/설정 영수증 및 바인딩을 확인합니다. 계정 DB 행 잠금 → PostgreSQL 공유 범위 잠금 → SQLite 파일 잠금 순서로 협력 쓰기를 막습니다.
2. PostgreSQL에 기록된 최신 fingerprint와 SQLite가 정확히 같은지 검사합니다. 주문 한도 흐름/이벤트가 없고 공유 저널의 attempts 행과 total이 모두 0인지 검사합니다. 멈춤 플래그, 손상·체크포인트 불일치·경로 변조·동시 쓰기·기존 미완료 복구가 있으면 중단합니다.
3. 시계가 실제로 유효하면 변경을 생략합니다. 시계가 달라졌으면 전용 `clock-recovery-<id>` 하위 폴더에 기존 SQLite 원본과 이전/새 메타데이터를 담은 intent.json을 새로 보관하고 디스크에 동기화합니다. 백업 fingerprint도 기존 PostgreSQL 기록과 비교합니다.
4. 공유 저널의 boot/start/wall/last 시계 메타데이터만 갱신하고 PostgreSQL 체크포인트 이력에 새 revision을 추가합니다. 기존 revision·요청 기록·용량·식별자·정책·바인딩은 삭제하거나 덮어쓰지 않습니다.
5. 약 70초를 새로 기다립니다. 대기 중 계정 행 잠금/리스는 보유하지 않고 읽기 전용 상태 확인만 합니다. 대기 완료 후 한 번 더 검증합니다. 주문·writer session·실행 승인을 시작하지 않습니다.

이 명령은 공유 저널 시계와 PostgreSQL 보조 체크포인트를 변경하고 백업/복구 근거 파일을 생성합니다. 단순 읽기 전용 명령이 아닙니다. 금융 계정 DB와 원장 이력은 그대로 유지합니다. 거래소 HTTP 요청과 DEMO 키 세션 접근은 0입니다. 재설치·컨테이너 교체/기동·기간 적용은 포함하지 않습니다.

성공 마지막 문구:

```
SHARED_CLOCK_RECOVERED_KEEP_PAUSED_NO_ORDERS
```

마지막 JSON은 `runtime_clock_recovery: RECOVERED_KEEP_PAUSED`, `startup_ready: true`입니다. `period_keys_match: false`이면 시계 복구와 별개로 새 기간에 대한 근거 검토가 남아 있습니다. 기간 기준값을 자동 바꾸지 않습니다. 실행 승인·주문·실거래는 false를 유지합니다.

## 중단과 보존

사용 이력이 있으면 용량을 환불하거나 저널을 초기화하지 않고 `RECOVERY_SHARED_JOURNAL_USED` 등으로 중단합니다. 이 패키지는 **미사용 저널**에만 한정됩니다.

SQLite와 PostgreSQL은 별도 저장소입니다. SQLite 커밋 후 PostgreSQL 기록 전에 장애가 나면 다음 접근은 체크포인트 불일치로 차단됩니다. 보관된 원본과 intent를 유지하고 자동으로 기록을 추측하거나 원본으로 되돌리지 않습니다. 폴더·저널·체크포인트를 수동 삭제/수정하거나 복구 명령을 반복하지 말고 마지막 JSON을 공유하세요. 현재 시계가 또 바뀌어도 대기나 검증을 우회하지 않습니다.

## 검증

격리 PostgreSQL/실제 SQLite와 PowerShell 흐름 테스트 24개를 검증했습니다(빠른 검사 23개, 실제 시간 검사 1개). 부팅 변경·벽시계 오차·단조 시계 불일치 복구, 원본 바이트/금융 행/이력 보존, 70초 대기, 동일 상태 재실행, 사용 저널 거부, 동시 잠금, 변조/부분 복구/쓰기 실패, SQLite-PG 커밋 사이 장애를 포함합니다. 실제 시간 검사에서는 35초에 대기, 70초 초과 후 준비 상태를 확인했습니다. 복구 후 기존 설치 점검으로 다시 읽어 검증했습니다.

네트워크를 끈 Docker에서 명시적 적용 인자 누락·LIVE 문맥·구버전 코드·DB 미연결의 중단 경로 4개를 확인했습니다. PowerShell 흐름 검사는 합성 Docker 실행 파일을 사용했고 실제 주문을 전송하지 않았습니다. 사용자 Windows PC에서 복구가 완료됐다는 뜻은 아니며 CMD 실행 결과로 확인해야 합니다.

verification은 재현 자료입니다. 실행 CMD는 테스트의 설정/적용 코드를 호출하지 않습니다. 소스 기준 커밋 `e03b6ba683732cd5872c30cf0d245a5b1fb6d325` checkout과 개발 의존성 및 별도 테스트 DB가 있는 환경에서 실행합니다.

```sh
PYTHONPATH="$PWD" TEST_DATABASE_URL=postgresql+psycopg://dev:dev@127.0.0.1:55435/hybrid pytest -q /압축해제경로/verification/runtime-clock-recovery-20261112
```
