# 보호 worker 검사 실패 후 일시정지 유지 복구

이번 패키지는 새 진입이나 장애 주입을 실행하지 않습니다. 이미 검토·승인한 역사적 미해결 4건을 그대로 보존하면서, 현재 계좌와 종료된 두 검증 작업의 증거를 다시 대조한 뒤 정식 복구 함수를 통해 EMERGENCY를 NORMAL로 승인합니다. 진입 일시정지는 유지합니다.

Windows 스마트 앱 컨트롤 설정은 변경하지 않습니다. `.cmd`, `.bat`, `.ps1`, `.exe` 파일이 없습니다. 새 폴더에 압축을 푼 뒤 명령 프롬프트에서 실행합니다. 예전 폴더·로그·복구 영수증은 지우지 마세요.

```cmd
cd /d "%USERPROFILE%\Desktop\windows-demo-probe-flat-recovery"
```

다음 한 줄은 실행기 해시가 맞아야 복구 절차를 한 번 호출합니다.

```cmd
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; if((Get-FileHash -Algorithm SHA256 -LiteralPath 'recover_keep_paused.py').Hash.ToLower() -ne '513dd2f5a4be29a9adb470e7b1a9f239e3a560793d4ee1019fbe16dc974c7324'){throw 'RECOVERY_RUNNER_HASH_MISMATCH'}" && docker exec -i hybrid-windows-api-1 python - --acknowledge-four-historical-unresolved < recover_keep_paused.py
```

Docker와 창을 유지하고 마지막 결과 전체를 보내주세요. 성공 결과는 `EMERGENCY_ACKNOWLEDGED_READY_ENTRIES_STILL_PAUSED`이며 `NORMAL`, `paused=true`, 거래소 빈 상태와 날짜 일치가 함께 표시되어야 합니다. 차단/오류 결과가 나오면 재실행하지 말고 보존하세요. `application_may_have_completed=true`면 이미 상태가 바뀌었을 수 있으므로 먼저 읽기 전용 확인이 필요합니다.

## 적용 전 필수 대조

- 설치된 보호 worker 수정본 SHA256 `70d9f0a6ec74223e8a7a82ec55c976288c10fb13c01c9b478f49f1a0fd408709`와 기존 정식 복구 관련 소스 해시 4개가 일치해야 합니다.
- 한국 날짜 2026-11-06, 해당 일/주 기준, EMERGENCY/일시정지여야 합니다.
- 이전 복구 영수증 `0f72dd2e-9c5d-4567-aa46-9f3772f79ea7`이 있어야 하며 덮어쓰지 않습니다.
- 기존 실패 작업 `5bb5814c-55e0-4b7f-882c-a7a449ec2182`와 최신 실패 작업 `e85a00f2-6b8e-4b39-bac2-b958a001c8c0` 모두 정확한 0.0007 BTC 전체 체결, 종료, 1세대 청산 완료, 기존 SL/TP 보호 기록과 각각 다른 요청 ID 4개를 가져야 합니다.
- 최신 작업의 FULL_PROTECTED_READY, FINALLY_RAN, STREAM_OBSERVATION/NONE 기록이 있어야 합니다. 이전 작업의 준비 마커가 없어야 합니다.
- 사용자가 대화에 제공한 원본 보고서의 검증 필드를 `original-reference-fields.json`에 포함했습니다. 원본 감사 대상 78건 이후 두 작업의 요청 8건이 추가된 정확히 86건을 새 서명 조회로 감사해야 합니다. 새로운 미해결 요청은 인정하지 않습니다.
- 원래 4건의 digest, 요청 참조, 연결된 작업, 역할, 상태, 수량, no-send 증거가 모두 같아야 합니다. NOT_FOUND를 미전송·미체결 증거로 바꾸지 않습니다.
- 거래소 포지션과 활성 주문이 없어야 하고, 실제 적용 시 정식 복구 함수가 lease 아래에서 다시 검증해야 합니다.

새 복구 작업 ID는 `53d066a9-56dc-4042-9b64-204eb5048156`입니다. 위험 상태·세션 신뢰 플래그와 새 복구 영수증/승인 기록 외의 재무 필드, 이전 영수증, 주문, 요청 상태와 이력이 보존되어야 합니다. 미해결 4건의 상태를 삭제하거나 해결 완료로 바꾸지 않습니다.

실행기 안에는 별도 동봉한 두 Python 소스와 승인 보고서의 검증 필드가 **평문**으로 들어 있습니다. 각 파일의 해시를 확인하고 사용자 10001의 작업별 임시 폴더(0700, 파일 0600)에만 쓰며, 같은 폴더가 이미 있으면 중단합니다. 하위 프로세스는 자동 재실행하거나 외부 timeout으로 종료하지 않습니다. 임시 파일은 종료 시 정리합니다. API/DB 재시작이나 재빌드는 없습니다.

새 검토 토큰을 출력하거나 사람이 복사해 입력하지 않습니다. 정식 함수에 메모리로 전달하며 정식 복구 영수증에는 그 토큰이 DB에 기록됩니다. API 키와 메일 비밀번호는 패키지에 없고 출력하지 않습니다. 거래소 요청은 조회 전용이며 신규 진입/취소/청산/보호 주문 쓰기를 하지 않습니다. 이 절차는 메일을 별도로 만들거나 전송하지 않지만 기존 알림 worker는 대기 중 경고를 처리할 수 있습니다.

이 복구는 실제 자식 SIGKILL 검증 성공을 의미하지 않습니다. Phase 4 전체 완료나 LIVE 전환도 아닙니다.
