# 보호 worker 준비 확인의 자체 DB 잠금 경쟁 수정

Windows의5bb5814c-55e0-4b7f-882c-a7a449ec2182 시험은 미수락이다. 실제0.0007 BTC 전량 체결/SL·TP 확인 뒤 ALGO_UPDATE 처리에서 DB_LOCK_TIMEOUT이 기록됐고, 강제 종료는0회였다. 최종 flat/리스 반환은 확인됐으며 EMERGENCY/paused다. 이 결과를 성공으로 바꾸거나 같은 시험을 반복하지 않는다.

새 도구의 바깥 감독 루프가 PAUSE_ENTRIES/보호 확인/ready 기록을 실행하는 동안, private session 소비자가 같은 계정 행을 갱신할 수 있었다. 실제 PostgreSQL에서 pause 쓰기를2.4초 유지하는 회귀 시험을 추가했고, 이전 코드에서 SQLSTATE55P03을 재현했다. 수정 후에는 callback 안에서 준비 확인 쓰기를 끝낸 다음 이벤트 처리를 시작하므로 같은 시험이 통과한다. 기존2초 DB 잠금 제한,60초 리스, 이벤트 freshness 및 거래/보호 위험 게이트를 늘리거나 약화하지 않았다. 실제 사건의33개 전체 타임라인 파일은 아직 제출되지 않았으므로 그 외 외부 경쟁이 없었다고 단정하지 않는다.

수정은 준비 확인을 초기 private-stream on_ready callback으로 옮긴다. 수신 inbox는 계속 동작하지만 이벤트/heartbeat 소비는 callback 완료 후 시작한다. 별도 감독 루프는 시간/제어 파이프만 관찰하고 계정 쓰기를 하지 않는다. callback 시점에 전량 체결/보호가 확인되지 않은 경우에는 늦은/부분 체결의 원래 보호·정리를 유지하되 kill 준비를 선언하지 않는다. 자식 실패 단계/안전한 코드도 세션 drain 후 finally 감사에 남긴다. 원문 예외나 비밀은 남기지 않는다.

별도로 이전 조회 도구 REQUESTS 허용 목록에 정상값 VERIFIED가 빠져 있었다. 해당 코드의 실제 SL/TP 요청 상태는 VERIFIED를 사용할 수 있는데 화면에서는 UNRECOGNIZED로 표시됐다. 수정된 조회 소스와 실제 PostgreSQL 회귀를 포함했다. 이 표시만으로 보호 실패를 판정하지 않으며 DB 값을 변경하지 않는다.

## 이번 설치

ZIP을 새 폴더에 풀고 INSTALL-SERIAL-FIX.cmd가 있는 위치에서 CMD로 실행한다.

```bat
INSTALL-SERIAL-FIX.cmd
```

이 명령은 새 주문/취소/프로세스 종료/위험 복구 승인을 실행하지 않는다. 먼저 이번 종료된 주문과 비활성 리더를 로컬 READ ONLY로 확인하고 기존 canonical 전체 복구 심사를 조회 전용으로 수행해 risk_gates PASS·원격 빈 계정·paused를 확인한다. 심사 토큰은 RAM에서만 받고 출력/적용하지 않는다. 계정 상태가 조회 중 달라져도 중단한다.

그 뒤 api만 빌드/갱신한다. DB/web/alerts/key-session과 키/메일 설정은 재시작/초기화하지 않는다. 소스 해시로 새 도구와 원래 DB/stream/복구 모듈을 확인하고, 같은 조회 전용 심사를 다시 수행한다. 과거 원장/감사/미해결 요청을 변경하지 않는다. 앱 재시작의 원래 Alembic 명령은 그대로이며 새 migration 파일은 없다.

성공은 PRECHECKED_KEEP_PAUSED → SERIAL_FIX_SOURCE_VERIFIED_ORIGINAL_LOCK_AND_STREAM_UNCHANGED → POSTCHECKED_KEEP_PAUSED → SERIAL_FIX_INSTALLED_NO_NEW_TRIAL_ENTRIES_STILL_PAUSED다. 결과의 risk_state·unresolved_count를 공유한다. EMERGENCY를 자동 해제하거나 기준금을 고치거나 새 시험을 실행하지 않는다. 준비 심사가 실패하면 설치 전에 중단하며, 설치 후 심사만 실패한 경우 코드 적용은 이미 끝났을 수 있으므로 출력 그대로 공유한다.

이 패키지의 예전 RUN-ONE-PROTECTED-WORKER.cmd는 실행 차단했다. 기존 결과 로그를 삭제해 재시도하거나 이전 폴더의 실행 파일을 다시 사용하지 않는다. 새 실제 검증은 원인 조치/복구/새 단일 작업 범위를 확인한 뒤 별도로 준비한다. 기존5bb 작업은 재사용하지 않는다.

실제 Windows에서 수정 설치/복구 적격성/다음 시험은 아직 확인되지 않았다. 이번 수정과 합성 회귀로 실제 보호 worker 장애 성공·실제 TP 성공·수정 후 실제 부분체결 성공·Phase4 전체 수락/LIVE/Phase5를 선언하지 않는다.
