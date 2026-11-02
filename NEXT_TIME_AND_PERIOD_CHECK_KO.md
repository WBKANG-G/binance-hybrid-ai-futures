# 다음 단계: 시각·기간 기준 읽기 전용 확인

기존 windows-demo-alert-pipeline-check 폴더의 CMD에서 아래4개 명령을 순서대로 실행한다. 새 ZIP/빌드/컨테이너 재시작은 필요 없다. 코드 블록은 모두 전체가 실행 명령이며 UUID 단독 명령은 없다.

## 1. Windows 현재 시각

```bat
powershell -NoProfile -Command "Write-Output ('WINDOWS_LOCAL=' + (Get-Date).ToString('o')); Write-Output ('WINDOWS_UTC=' + [DateTime]::UtcNow.ToString('o'))"
```

## 2. API 컨테이너 현재 UTC

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -T api python -c "from datetime import datetime, timezone; print('API_UTC=' + datetime.now(timezone.utc).isoformat())"
```

## 3. DB 현재 UTC와 같은 알림의 저장된 전달 UTC

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -T db psql -X -U dev -d hybrid -v ON_ERROR_STOP=1 -c "BEGIN READ ONLY; SELECT clock_timestamp() AT TIME ZONE 'UTC' AS db_utc, (SELECT delivered_at AT TIME ZONE 'UTC' FROM demo_urgent_alerts WHERE alert_id='4e5d7b91-23a7-4612-b8dc-23542cc63ff0') AS saved_delivery_utc; COMMIT;"
```

saved_delivery_utc가 비어 있으면 과거 행을 다시 생성하지 말고 그대로 결과를 공유한다.

## 4. 저장된 일간·주간 기준일

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -T api python -m scripts.review_demo_recovery --local-periods
```

4개 결과 전체를 공유한다. 마지막 JSON의 period_keys_match, stored/current_day_kst, stored/current_week_kst 및 risk_state/paused를 확인한다. 이 조회의 current_*는 실행 중인 API 컨테이너의 현재 UTC 시각과 저장된 정책으로 계산하며 외부 공인 시각 검증을 의미하지 않는다. false가 나오더라도 기준금을 현재 잔고로 초기화하거나 복구/적용 명령을 실행하지 않는다.

현재 사용자의 새 기간 거래/입출금 여부는 확인 전이다. 과거 활동 없음 확인을 그대로 적용해 attestation 플래그를 사용하지 않는다. 시각 차이는 우선 조회로 좁히고 Windows/WSL/Docker 시계를 임의로 변경하지 않는다.

## 준비 검증과 한계

애플리케이션·migration·Compose·기존 ZIP은 변경하지 않았다. 기존 로컬 기간 조회의 정상/다음날2개 및 적용 결합 거부1개 검사, 총3개가 실제 격리 PG에서0.63초에 통과했다. 키/네트워크 미사용과 장부·잠금 불변을 확인하는 기존 테스트 조건을 유지했다. API용 Python 시각 표현식을 로컬 Python3.12에서 실행했고, 위 SQL을 같은 PG18 이미지의 격리 DB에서 READ ONLY로 실행해 문법과 미등록 ID의 NULL 반환을 확인했다. 이는 사용자 Windows 시각이나 실제 사용자 DB 행의 검증이 아니다. PowerShell은 클라우드에 설치돼 있지 않아 그 명령을 실제 Windows에서 실행하지 않았다.

이 문서는 코드 변경이 아니므로 전체 회귀 검사를 다시 실행하지 않았다. 이전 앱 버전의 단일 전체 실행 실패 및 분리 실행 통과 한계는 원래 ALERT_PIPELINE_VALIDATION_KO.md를 유지한다. main/LIVE/Phase5 또는 Phase4 전체 완료를 변경하지 않는다.
