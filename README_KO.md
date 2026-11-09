# 실제 주문 한도·카운터 제공 여부 확인 — 읽기 전용

소스 `5c47faf8fe071c029caffbdae6dd450f2b96255b` / 현재 설치 기준 `5abc9f6`
실행 파일 SHA256 `d73e1670e28efa19b5a097dce554dc70494e0fd7ec97373042d08f3c877b2f87`

**재설치나 Docker 빌드가 없습니다.** 기존 코드·진입 일시정지·DB·기간 기준값·수동청산 영수증을 변경하지 않습니다. 실제 주문·listen-key·WSS·DB 임대 작업도 없습니다. 새 도구는 현재 설치된55개 파일의 해시를 SDK import 전에 검사한 다음, 기존 키 세션을 사용해 DEMO에 최대4회의 GET만 보냅니다.

## 실행

압축을 바탕화면에 풀고 CMD에서 실행하세요.

```cmd
cd /d "%USERPROFILE%\Desktop\windows-demo-tp-admission-review"
```

아래 한 줄은 실행 파일 해시 확인 후 container의 Python으로 파일을 표준입력합니다. container에 파일을 복사하거나 설치하지 않습니다.

```cmd
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; if((Get-FileHash -Algorithm SHA256 -LiteralPath 'review_tp_entry_admission.py').Hash.ToLower() -ne 'd73e1670e28efa19b5a097dce554dc70494e0fd7ec97373042d08f3c877b2f87'){throw 'ADMISSION_REVIEW_HASH_MISMATCH'}" && docker exec -i hybrid-windows-api-1 python - < review_tp_entry_admission.py
```

전체 JSON 결과를 보존해 공유하세요. 키·서명·원문 응답은 출력하지 않습니다. 키 세션이 만료되거나 제한/오류가 발생하면 중단하고 자동 재시도하지 않습니다. 정상적으로 정보를 받아도 항상 `execution_approved=false`이며 TP 시험이나 진입 재개 명령은 실행하지 않습니다.

## 이번에 추가 확인하는 값

기존 준비 검사는 포지션·주문이 비었는지 확인했지만 계정 주문 횟수 제한을 조회하지 않았습니다. 이번에는 시각과 exchangeInfo, 서명된 `/fapi/v1/rateLimit/order` 2회로 **10초/1분 주문 제한 및 현재 사용량이 실제로 제공되는지**를 확인합니다.

공식 SDK의 해당 응답 모델은 한도 값만 선언하며 현재 `count`를 보장하지 않습니다. 따라서 응답 본문 또는 `X-MBX-ORDER-COUNT-10S`/`1M` 헤더에 명시된 값만 사용합니다. **값이 없으면 사용량은 null이며, 0이라고 가정하지 않습니다.** 일반 SDK 경로 허용 목록이나 metadata 정책을 바꾸지 않고, 고정된 DEMO 호스트의 이3개 GET 경로만 사용하는 전용 경계를 구현했습니다.

- `SAMPLED_HEADROOM_FITS`: 명시된 카운터가 있고6개 모델 슬롯의 표본 여유가 있습니다. 실행 허가는 아닙니다.
- `LIMITS_VERIFIED_USAGE_UNAVAILABLE`: 한도는 받았지만 사용량이 제공되지 않았습니다. `ORDER_COUNTER_NOT_PROVIDED`와 null 값이 표시되며 종료코드2로 마칩니다. 같은 요청을 반복해서0으로 만들려고 하지 않습니다.
- `SAMPLED_CONSTRAINT_BLOCKED`: 카운터 감소/한도 변경/주문 슬롯 부족으로 표본 검사를 차단했습니다.

6개 슬롯은 진입1, SL·TP 생성2, 가능한 algo 실행2, 위험 축소 종료1을 모델링합니다. 두 algo 실행 슬롯은 보수적 가정이며 실제 counter 상한이 증명된 것은 아닙니다. 모든 추가 ORDERS 시간창도 빠짐없이 대조합니다. 공개/계정 한도는 작은 값을 택하고, 두 표본 중 작은 카운터로 여유를 돌려주지 않습니다. 표본 나이는5초 이내만 허용합니다.

이 검사는 준비 상태를 재검사하거나 IP/계정 용량을 예약하지 않습니다. 관측 뒤 다른 프로세스의 작업도 막지 못하므로 실제 진입 허용 조건의 일부 증거입니다. 현재 설치된 실행 거부는 유지됩니다.

## 검증

전체 단위 검증 **956개 통과**, 그 안의 이번 집중 검증은81개입니다(중복 합산하지 않음). Ruff 및 mypy145개 운영 Python 소스가 통과했습니다. bundle과 원본의 전체 AST 동등성을 확인했습니다. 현재 설치 기준 Docker 이미지에서 network=none 및 가짜 HTTP로 본문 카운터·헤더 카운터·카운터 미제공을 각각 검증했습니다. 실제 키 없는 standalone 실행은 source 검사를 통과한 뒤 네트워크 호출 전에 중단하는 것도 확인했습니다.

cloud에서는 실제 DEMO API·주문·Windows 접근을 하지 않았습니다. 공식 SDK revision·URL·hash는 `source/docs/admission_evidence/OFFICIAL_SOURCE_PROOF.json`에 있습니다. 그 source 계약은 실제 DEMO 응답이나 계정별 endpoint 지원을 증명하지 않습니다. `validation/`은 합성 시험이며 본인의 계정 값이 아닙니다. .CMD/.BAT/.PS1/.EXE 파일 및 보안 설정 변경은 없습니다.
