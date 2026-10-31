# NORMAL/일시정지 상태의 일간 기준금 검토

11월1일로 날짜가 바뀌었고 장부의 일간 기준일은10월31일입니다. 주간 기준일은10월26일로 동일합니다. 현재 NORMAL/paused를 유지하며, 이번 단계는 읽기 전용 후보 검토까지만 진행합니다. 신규 진입/청산/보호/계정 변경 요청은 없습니다. 날짜를 맞추려고 비상 정리 명령을 실행하지 않습니다.

운영자는 마지막 응답 유실 시험 종료 후 안내한 명령 외 수동 거래/다른 봇/다른 종목 거래/입출금/내부이체/보너스가 없었다고 확인했습니다. 이후 새 활동이 있었다면 이 명령의 --attest-no-other-trades-since-exit을 사용하지 말고 변경 내용을 알려야 합니다. 계좌 전체 거래소 export로 검증된 출처라는 뜻은 아닙니다.

## 1. 빌드

ZIP을 새 windows-demo-paused-day-review 폴더에 풉니다. 기존 폴더에 덮어쓰지 않습니다. 새 폴더의 탐색기 주소창에 cmd를 입력합니다. 같은 hybrid-windows 프로젝트와 기존 볼륨을 유지합니다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait
```

모든 컨테이너 Healthy 확인 뒤 아래 미리보기만 실행합니다. -it가 있으므로 RAM 캐시에 키가 없을 때 DEMO API Key/Secret을 숨김 입력할 수 있습니다. 기존 유효 RAM 키가 있으면 그대로 사용합니다. 키 입력값을 채팅에 보내지 않습니다. 이 숨김 입력 자체는 새 RAM 키 등록 파일을 만들지 않습니다.

## 2. 읽기 전용 미리보기

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -m scripts.review_demo_paused_day --attest-no-other-trades-since-exit
```

정상 미리보기는 PAUSED_NORMAL_DAY_PREVIEW_NO_DATABASE_CHANGE입니다. proposed_day_kst/proposed_daily_base_usdt, 자정 경계·flat 앵커·알려진 종료 시각, 출처·위험 상태, review_token과 read_diagnostic을 포함한 전체 JSON을 공유합니다. 기준금은 아직 변경되지 않습니다. --apply-review, --accept-operator-evidence, --operation-id는 이번 단계에서 사용하지 않습니다.

BLOCKED이면 명령 반복/새 키 삭제/기준금 수동 수정 없이 전체 결과를 공유합니다. 낮은 손실 기준금을 만들거나 잠금을 해제하는 우회는 없습니다. 주간 기준금·고점·과거 평가 시각·이력/미해결 요청은 유지합니다. 원래 비상 상태 도구는 EMERGENCY 전용이고, 새 경로는 NORMAL/paused 전용이라 DAILY/WEEKLY/DRAWDOWN/UNHEALTHY/EMERGENCY 상태를 취급하지 않습니다.

## 검토와 적용의 차이

원래 서명 증거 검사는 자정 전 flat 앵커, 양방향 종료/체결 이력, 이후 소득·거래 없음, USDT 단일 기준/이체0, 현재 flat 및 손실 한도를 대조합니다. 이 조건과 운영자 확인이 충족될 때만 후보를 제시합니다. 자정에 실제 잔고를 직접 관측한 기록으로 바꾸지 않습니다. 출처는 OPERATOR_ATTESTED_FLAT_WITH_SIGNED_USDT_EVIDENCE이며 account_wide_exchange_export_verified는 false입니다.

후속 적용은 실제 후보를 확인한 뒤 검토 토큰·새 UUID·명시적 출처 수락을 함께 지정해야 가능합니다. 적용 직전 전체 검토와 토큰 일치를 다시 확인합니다. 적용은 로컬 일간 기준일/기준금과 영수증/감사 표식만 바꾸고 NORMAL/paused를 유지하며, 신규 진입을 자동 재개하지 않습니다. 이 안내에는 적용 명령이 없습니다.
