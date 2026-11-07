# 열린 DEMO 주문 중 API 재시작 검증 준비

이번 검증은 별도 컨테이너의 주문 감시자가 계속 살아 있는 동안 API만 재시작하는 범위입니다. 주문 감시자 자체의 강제 종료/재시작과는 별도입니다. 기존 API 컨테이너 안의 주문 감시 명령을 실행한 채 API를 재시작하는 방식은 사용하지 않습니다.

지금은 미리보기까지만 진행합니다. 기존 Galaxybook에서 ZIP을 새 windows-demo-open-api-restart 폴더에 풀고 탐색기 주소창에 cmd를 입력하세요. 기존 hybrid-windows 데이터베이스와 볼륨을 유지합니다. 삭제·초기화하지 마세요.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait
```

컨테이너 교체로 메모리 키가 초기화되므로 한 번 등록합니다. 키는 숨김 입력에만 넣으세요.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -m scripts.demo_key_session --register
```

주문 없는 전용 미리보기를 실행합니다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -T api python -m scripts.check_demo_open_api_restart --preview
```

최종 OPEN_API_RESTART_NO_ENTRY 결과를 공유하세요. 실제 진입/재시작 명령은 미리보기 확인 후 별도의 새 작업 UUID로 진행합니다. 미리보기는 개인 연결과 REST 재대조를 수행하고 세션을 정상 종료합니다. 주문은 제출하지 않으며 진입 정지도 유지합니다. 세션·임대·재대조 감사 기록은 DB에 남으므로 DB 읽기 전용 명령은 아닙니다.

실제 실행은 선택적 infra/docker-compose.restart-check.yml의 demo_restart_guard를 compose run으로 별도 터미널에서 실행하는 방식입니다. 감시자는 API와 같은 빌드 이미지를 사용하지만 독립 컨테이너/프로세스/메모리 공간을 갖습니다. API 안의 exec로 실행하면 PID 검사에서 차단합니다. 새 감시자 컨테이너는 API의 메모리 키를 공유하지 않으므로 그 터미널에서 DEMO 키를 숨김 입력으로 한 번 입력해야 합니다. 키를 명령 인자·디스크·DB·공유 볼륨에 복사하지 않습니다.

검증 주문은 고정된 DEMO 실험값으로 현재 매수 호가보다0.3% 아래의 LIMIT LONG, 거래소 최소 유효 수량, 명목100USDT 이하입니다. 기존 스프레드/슬리피지1%·위험 예산·레버리지2배·시각·필터·수량cap 검사는 그대로 적용합니다. 주문 실험 TTL60초, 재시작 관찰 최대45초이며 무한 대기·재호가·시장가 신규 진입은 없습니다. 이것은 LIVE 또는 전략 기본값이 아닙니다. 가격이 움직이면 여전히 체결될 수 있습니다.

OPEN_ORDER_READY_RESTART_API_NOW가 나왔을 때만 다른 CMD 창에서 API를 재시작합니다. 감시자는 실제 API 기동 식별자 변경, 전후 동일한 미체결 주문, 재대조 전 진입 차단과 동일 의도의 중복 POST0건을 확인합니다. 이후 잔량 취소·최종 포지션0을 확인하고 진입 정지를 유지합니다. API가 재시작되지 않거나 주문이 체결되거나 불확실해지면 이 시나리오 통과로 처리하지 않습니다. 체결 시에는 기존 보호/청산 경로로 정리합니다. 감시자 창은 최종 결과 전 닫지 않습니다.

API 재시작은 API의 메모리 키를 초기화하지만 별도 감시자의 메모리 자격 증명은 유지합니다. 검증 종료 뒤 API에서 다음 조회를 하려면 API 키를 한 번 다시 등록해야 할 수 있습니다. 기존 실패 작업 UUID를 새 진입으로 재사용하지 않습니다. 같은 UUID 재실행은 기존 의도의 복구/정리만 수행합니다.
