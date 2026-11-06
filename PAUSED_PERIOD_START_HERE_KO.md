# NORMAL/일시정지 일간·주간 후보 미리보기

사용자가 다른 활동 없음으로 답했고, 현재 시각은11월2일이며 저장된 일간11월1일/주간10월26일과 다르다. 기존 EMERGENCY 전용 기간 도구나 NORMAL 전용 일간 도구로 우회하지 않고, NORMAL/paused 상태를 유지하는 별도 읽기 전용 도구를 사용한다. 이번 버전에는 새 경로의 적용 기능이 없다.

## 1. 설치

ZIP을 새 windows-demo-paused-period-preview 폴더에 풀고 해당 폴더 탐색기 주소창에서 cmd를 실행한다. 기존 폴더에 덮어쓰지 않고 같은 hybrid-windows 프로젝트와 DB·RAM 키/메일·export 볼륨을 유지한다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait
```

모든 컨테이너 Healthy 확인 뒤 아래 미리보기만 실행한다.

## 2. 읽기 전용 후보 조회

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -m scripts.review_demo_paused_periods --attest-no-activity-since-anchor
```

거래소 서명 조회를 위해 유효한 DEMO API 키 RAM 설정을 재사용한다. Docker 재시작/12시간 만료 등으로 없으면 API Key/Secret을 숨김 입력한다. 네이버 앱 비밀번호가 아니다. 키 입력값을 채팅에 보내지 않는다. 전송되는 거래소 요청은 GET뿐이며 신규 주문/취소/청산/계정 변경/export 생성은 없다.

정상 미리보기 이름은 PAUSED_NORMAL_PERIOD_PREVIEW_NO_DATABASE_CHANGE다. proposed_day_kst/proposed_week_kst, proposed_daily_base_usdt/proposed_weekly_base_usdt, source, review_token, anchor_utc, 경계 시각, read_diagnostic을 포함한 전체 JSON을 공유한다. 성공해도 DB의 기준금은 아직 바뀌지 않는다. 새 기준금 숫자는 서명 조회 전 추정하지 않는다.

BLOCKED이면 오류 JSON 그대로 공유한다. 같은 명령 반복, 비상 상태 변경, 기준금 수동 초기화, 새 토큰/작업 ID 생성 또는 기존 복구/적용 도구 실행을 하지 않는다. 새 CLI는 --apply-review/--operation-id/--accept-operator-evidence를 지원하지 않는다.

## 수락 범위

후보는 기존 금융 앵커가 두 경계 이전에 있고 양쪽 저장 기준일이 앵커의 기간과 일치하며, 7일 이내·로컬/원격 flat·USDT 잔고 일치·알려진 체결/청산 검증·이후 BTC 체결/소득 없음·이체 기준0·기존 손실 한도 및 고점 보존을 모두 확인할 때만 나온다. 출처는 OPERATOR_ATTESTED_FLAT_PERIODS_WITH_SIGNED_USDT_EVIDENCE다. 자정 잔고 직접 관측이나 계좌 전체 export 검증이 아니다.

DB 계좌/감사/outbox·리스/펜스·기존 UNKNOWN/NOT_SENT 증거를 변경하거나 승인하지 않는다. 메일 TEST도 다시 보내지 않는다. 서명 조회는 기존40초/600 GET/6 clock probe 예산과 검증 HTTPS 재사용을 적용하며 실패를 자동 재시도하지 않는다. 기간이 바뀌거나 서명 이력이 진술과 모순되면 차단한다. 서명 조회 결과가 없는 지금은 실제 Windows 후보 수락 전이다.
