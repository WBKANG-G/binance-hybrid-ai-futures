# 2026-11-11 DEMO 일간 기준 적용

확인된 Windows 미리보기와 운영자의 추가 활동 없음 답변을 바탕으로 작성했습니다. 일간 날짜만 2026-11-09 → 2026-11-11로 바꾸고 기준금액 4998.74183608 USDT, 주간 날짜 2026-11-09, 주간 기준 4998.74183608 USDT, 고점 5000 USDT를 유지합니다.

ZIP 안의 `windows-demo-manual-close-day-apply-20261111` 폴더를 바탕화면에 두고 CMD에서 실행하세요.

```bat
cd /d "%USERPROFILE%\Desktop\windows-demo-manual-close-day-apply-20261111"
RUN_APPLY.cmd
```

이 명령은 DB의 일간 기준과 적용 영수증·감사 이벤트를 기록합니다. 55개 설치 소스 해시와 이전 적용 영수증을 확인하고, 현재 전 종목 포지션·미체결 일반/알고 주문, 잔고, 서명된 BTC 거래·청산 및 이후 income을 다시 읽습니다. 검토된 값과 다르면 적용하지 않습니다. 기존 원장과 이전 영수증은 보존하며 일시정지 해제나 주문 생성은 없습니다. 자정 평가금액을 직접 검증한 것은 아니며 운영자 증언을 사용했다는 출처를 영수증에 남깁니다.

성공 코드 `OPERATOR_ATTESTED_DAY_ADOPTED_ENTRIES_STILL_PAUSED`를 확인한 뒤 실행하세요.

```bat
CHECK_READY.cmd
```

기대 결과: `READ_ONLY_READY_ENTRIES_STILL_PAUSED`, `period_keys_match: true`, `paused: true`, `orders_enabled: false`. 두 출력 결과를 공유해 주세요. 중단 코드가 나오면 반복하지 말고 그대로 공유하세요. 적용 여부가 불확실하면 `application_may_have_completed`와 `lease_change_possible`로 표시됩니다. 정상 완료 후 동일 명령을 다시 실행해도 영수증 확인만 하고 재적용하지 않습니다.

등록된 DEMO 세션을 사용합니다. 키나 Secret을 파일에 넣거나 공유하지 마세요. 2026-11-11 전용이며 이후 날짜로 자동 연장하지 않습니다. 전체 소스 설치·컨테이너 재빌드는 필요 없습니다.

검증: 테스트 PostgreSQL과 합성 서명 응답 기반 101개 검사(기존 조회 경로 71개 + 적용 경로 30개), Ruff 통과. 적용·롤백·중복 방지·기준/이력 보존·증거 부족·활동 감지·만료·오류 비노출을 검사했습니다. Docker 네트워크 차단 상태에서 55개 소스 해시와 DB 실패 시 중단 경로도 확인했습니다. 클라우드에서 사용자 Windows DB를 변경하거나 실제 거래소 요청을 실행하지 않았습니다.
