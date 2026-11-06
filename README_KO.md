# 부분 체결 검증 전 공개 호가 관찰

이번 작업은 **공개 DEMO 호가만 조회**합니다. API 키/메일 비밀번호를 읽지 않고, 계좌·DB·private WSS에 접근하지 않으며 주문·신호를 만들지 않습니다. 현재 NORMAL/진입 일시정지 상태를 변경하지 않습니다. 재빌드나 컨테이너 재시작도 없습니다.

Windows 보안 설정은 그대로 두세요. CMD 실행 파일은 없습니다. 바탕화면의 새 폴더에 압축을 푼 뒤 명령 프롬프트에서 실행합니다.

```cmd
cd /d "%USERPROFILE%\Desktop\windows-demo-partial-opportunity-observation"
```

```cmd
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; if((Get-FileHash -Algorithm SHA256 -LiteralPath 'check_public_liquidity.py').Hash.ToLower() -ne '2e52f27bea03e837fd618676fb71015612291df5e1e8cfa98b95cc3c6e8612ad'){throw 'PUBLIC_OBSERVER_HASH_MISMATCH'}" && docker exec -i hybrid-windows-api-1 python - < check_public_liquidity.py
```

약 2분 동안 창과 Docker를 유지하고 완료 화면을 보내주세요. 관찰 창은 최대 120초, 호가 표본 최대 60개, 표본 간 대기 2초입니다. 초기 공개 시각/거래 규칙 조회와 진행 중인 마지막 요청에 걸리는 시간은 추가될 수 있습니다. 오류나 rate limit이면 자동 재시도하지 않고 중단합니다.

상한은 기존 0.0007 BTC/100 USDT입니다. 거래소 최소 수량·명목·lot/tick 규칙, 양수 잔량, 호가 정합성과 5초 이내 freshness를 적용합니다. 새로운 최소 주문 수량을 계산해 최우선 매도 잔량이 그보다 적은 표본을 후보로 표시합니다. 양수 최우선 잔량과 숨은 유동성은 실제 체결을 보장하지 않습니다.

출력은 전체 표본 덤프 대신 첫/마지막 표본, 관측 매도 잔량 최솟값/최댓값, 후보 수와 최대 3개 예시를 담습니다. 모든 수집 표본을 파일로 녹화하는 도구는 아닙니다. 후보가 없어도 관측 성공과 실제 부분 체결 성공은 구분합니다. `partial_fill_guaranteed=false`, `signal_generated=false`, `signed_requests=0`, `order_writes=0`, `next_action=REVIEW_ONLY_DO_NOT_SUBMIT_FROM_THIS_REPORT`를 유지해야 합니다.

후보가 있어도 이 보고서로 주문을 제출하지 마세요. 결과는 다음 실제 검증을 계획하기 위한 자료입니다. 기존 부분 체결 실패를 성공으로 덮어쓰거나 실제 TP 체결이 있었다고 해석하지 않습니다. 현재 수락 범위는 `PHASE4_CURRENT_EVIDENCE_20261106_KO.md`에 정리했습니다.
