# 기존 TP 체결 이력 읽기 전용 대조

새 DEMO 주문을 만들지 않고, 앱에 기록된 기존 TP 중 실제 체결 증거가 있는지 조회합니다. 원래 SL의 현재 상태도 함께 확인합니다. 과거 TP 체결을 찾는 조회이며, 현재 상태 조회만으로 과거 SL 삭제의 선후 관계를 증명하지 않습니다.

## 실행

Windows 보안 설정을 유지하세요. CMD 실행 파일은 없습니다. 새 폴더에 압축을 풀고 명령 프롬프트에서 실행합니다.

```cmd
cd /d "%USERPROFILE%\Desktop\windows-demo-tp-history-review"
```

```cmd
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; if((Get-FileHash -Algorithm SHA256 -LiteralPath 'review_tp_history.py').Hash.ToLower() -ne '8bf9e9409995d819436989f647d85d5d03bded4a167c093ae752f5f1ddf3d0c7'){throw 'TP_HISTORY_READER_HASH_MISMATCH'}" && docker exec -i hybrid-windows-api-1 python - --review-only < review_tp_history.py
```

결과 화면을 보내주세요. 키가 만료되거나 조회가 차단되면 자동 재시도하지 않습니다. 이 결과에서 주문·복구·진입 재개를 실행하지 마세요.

## 범위

- 로컬 계좌는 NORMAL/일시정지, 활성 리더 없음, 잔여 노출 없음이어야 합니다. SELECT 전용 트랜잭션으로 읽으며 조회 전후 owner/fence/lease와 전체 로컬 계좌 상태가 같아야 합니다.
- 대상은 앱에 저장된 BTCUSDT/LONG/DEMO_VALIDATION 주문 중 체결 수량이 있고 CLOSED/잔량 0이며 원래 SL/TP ID가 있는 주문입니다. 신호 시각 기준 최신 최대 12건만 선택하며 전체 대상 수와 잘림 여부를 출력합니다. 계좌 전체 거래 내역을 내보내는 도구는 아닙니다.
- 해당 정확한 SL/TP ID의 서명 조회만 허용합니다. 검증된 TP가 FINISHED이고 양수 actualOrderId가 있어야 그 체결 주문을 추가 조회합니다. 같은 주문 ID, BTCUSDT/SELL/BOTH/MARKET/FILLED/reduceOnly, 로컬 체결 수량 일치와 양수 평균 가격을 확인합니다.
- 서명 GET 최대 36회, 기존 복구 조회의 40초 예산/시각 갱신·최대 시각 탐색 6회와 TLS/프록시/리다이렉트 금지 정책을 그대로 사용합니다. 진행 중인 제한된 SDK 호출과 초기 DB/키 준비 시간은 추가될 수 있습니다. POST/DELETE/private 세션/거래 내보내기/다른 주문·심볼 경로는 차단합니다. rate limit이나 오류 요청을 재시도하지 않습니다.

등록된 비공개 RAM DEMO 키 세션을 내부에서 읽어 서명 조회에 사용합니다. 키·비밀번호·서명 URL·실제 서버 주문 ID는 출력하지 않습니다. 키를 다시 입력하거나 대화에 붙여넣지 마세요. 출력은 해시된 작업 참조, 신호 시각, 체결 수량, 원래 TP/SL 상태와 체결 검증 분류입니다.

DB 상태 변경, lease 획득, 신규/취소/청산/보호 주문, 복구 승인, 메일 생성·전송, API/DB 재시작·재빌드는 없습니다. 현재 거래소 전체 포지션/활성 주문을 새로 검사하는 기능도 아닙니다(`current_exchange_empty_checked=false`). 현재 상태를 갱신하는 준비 검사로 사용하지 않습니다.

`NOT_FOUND_UNRESOLVED`는 과거 체결·전송이 없었다는 증거가 아닙니다. 표본에 실제 TP 체결 증거가 없다고 해서 계좌 전체·모든 기간에 TP 체결이 없었다거나 DEMO가 TP를 지원하지 않는다고 결론내리지 않습니다.

실제 TP 체결이 확인돼도 `t06_ordering_verified=false`를 유지합니다. 지금 조회한 SL의 CANCELED/EXPIRED 상태만으로 TP 체결 뒤 SL 삭제가 이루어졌다고 단정하지 않습니다. 조회 자체의 완료와 T-06 실제 성공은 별개입니다. 기존 위험 기준·주문 상한·TP/SL 거리도 변경하지 않습니다.
