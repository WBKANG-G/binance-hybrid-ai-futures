# 오래된 주문을 보존하는 일일 기준 검토

현재 사용자는 2026-10-31 일일 기준 검토에서 INVALID_HISTORY_WINDOW로 차단됐습니다. 기존 검토는 가장 오래된 주문 신호부터 현재까지의 전체 기간을 사용하지만 조회 도우미의 총 기간 제한이7일이었습니다. 10월20일부터 보존한 주문이 있어 이 제한을 넘습니다. 실제 오류의 원본 숫자 범위는 출력되지 않았으며 코드와 기존 주문 날짜에 근거한 진단입니다.

새 windows-demo-history-span-fix 폴더에 ZIP을 풀고 기존 Galaxybook에서 탐색기 주소창에 cmd를 입력하세요. 기존 hybrid-windows 데이터베이스와 볼륨을 유지합니다. 삭제·초기화하지 마세요.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait
```

컨테이너 교체로 메모리 키가 초기화되므로 한 번 등록합니다. 키는 터미널의 숨김 입력에만 넣으세요. 같은 컨테이너에서는 최대12시간 재사용합니다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -m scripts.demo_key_session --register
```

사용자가 마지막 실제 검증·청산 이후 수동/다른 봇 거래·입출금이 없다고 확인했습니다. 이 확인과 거래소 서명 응답을 대조하는 검토만 실행합니다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -T api python -m scripts.review_demo_attested_day --attest-no-other-trades-since-exit
```

결과를 공유하세요. 이 명령은 주문·데이터베이스 변경·잠금 해제를 하지 않습니다. 아직 apply-review나 복구 적용을 실행하지 마세요. 최신 검토 토큰과 실제 결과를 확인한 뒤 별도의 적용 명령이 필요합니다. 일일 기준이 적용돼도 비상 상태와 일시 정지, 주간 기준·고점·과거 요청은 보존됩니다.

마지막 확인된 경계 조회는 10월31일0시 이후 USDT 손익·수수료·입출금 기록0건, 현재 지갑4999.35095801 USDT입니다. 현재 잔액만으로 자정 자산을 확정하지 않습니다. 최근 청산·전체 BTC 기록·그 이후 입출금·USDT 자산 범위·계정 정합성·기존 손실 한도를 함께 검증합니다. 계정 전체 export 검증을 완료한 것으로 표시하지 않습니다.

31건 복구 감사 보완도 포함돼 있습니다. 날짜 기준 검토가 성공한 뒤 새 복구 검토가 필요합니다. LIVE/Phase5나 추가 거래를 시작하지 않습니다.
