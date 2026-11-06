# SIGKILL 성공 검증 후 일시정지 유지 복구

실제 DEMO 작업 `c75410e5-cbbb-4a75-bfe9-5272a8b447c6`의 결과는 `PROTECTED_CHILD_SIGKILL_FENCED_RECOVERY_FLAT_VERIFIED`였습니다. 이 패키지는 그 결과를 성공으로 보존하고, 정리 후 남은 EMERGENCY를 현재 증거에 따라 NORMAL로 승인합니다. **진입 일시정지는 유지하며 새 주문·검증·강제 종료는 실행하지 않습니다.**

## 실행

Windows 보안 설정은 유지합니다. `.cmd`, `.bat`, `.ps1`, `.exe` 파일은 없습니다. 바탕화면의 새 폴더에 압축을 푼 뒤 명령 프롬프트에서 실행하세요.

```cmd
cd /d "%USERPROFILE%\Desktop\windows-demo-sigkill-flat-recovery"
```

```cmd
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; if((Get-FileHash -Algorithm SHA256 -LiteralPath 'recover_keep_paused.py').Hash.ToLower() -ne 'e266c2727f20c3f85cfd48b415f2fec931d86703ad58e437bf527b3bf40c3430'){throw 'RECOVERY_RUNNER_HASH_MISMATCH'}" && docker exec -i hybrid-windows-api-1 python - --acknowledge-four-historical-unresolved < recover_keep_paused.py
```

완료까지 창과 Docker를 유지하고 결과 전체를 보내주세요. 성공 결과는 `EMERGENCY_ACKNOWLEDGED_READY_ENTRIES_STILL_PAUSED`, NORMAL/일시정지, 날짜 일치, 거래소 빈 상태입니다. 차단/오류 시 재실행하지 않고 결과를 보존하세요. 적용 후 확인 실패라면 `application_may_have_completed=true`가 표시되며 먼저 읽기 전용 확인이 필요합니다.

이전 복구 폴더·결과·영수증은 지우지 않습니다. 이전 실패용 복구 명령이나 성공한 SIGKILL 검증 명령을 재실행하는 단계가 아닙니다.

## 현재 사례에만 맞춘 증거 대조

설치된 runtime SHA256 `70d9f0a6ec74223e8a7a82ec55c976288c10fb13c01c9b478f49f1a0fd408709`, 정식 복구 소스 4개 해시, 한국 날짜 2026-11-06 및 해당 일/주 기준을 확인합니다.

원본 보고서의 미해결 4건은 이미 사용자가 검토하고 승인한 동일한 역사적 요청이어야 합니다. 원본 보고서의 검증 필드를 그대로 포함했고, 새 canonical 검토에 연결된 digest·요청 참조·작업 ID·역할·상태·수량·미전송 증거를 비교합니다. NOT_FOUND를 미체결/미전송 증거로 해석하거나 미해결 상태를 삭제하지 않습니다.

기존 감사 78건 이후 세 검증 작업의 진입/SL/TP/청산 12건이 추가된 **정확히 90건**을 새 서명 조회로 검토해야 합니다. 세 작업 모두 별개의 요청 ID 4개, 정확한 전체 체결 0.0007 BTC, 1세대 청산 완료, CLOSED/잔량 0, 원래 보호 확인 기록을 가져야 합니다.

- `5bb5814c-55e0-4b7f-882c-a7a449ec2182`: 준비 마커 없음, finally 실행 기록 있음.
- `e85a00f2-6b8e-4b39-bac2-b958a001c8c0`: 준비 및 finally 기록 있음, 자식 STREAM_OBSERVATION/NONE.
- `c75410e5-cbbb-4a75-bfe9-5272a8b447c6`: 준비 기록 있음, finally 및 자식 종료 실패 기록 없음. 준비 시 진입 1회·청산 0회 기록 필요.

세 작업 모두 해당 finish-exit 승인 기록이 필요합니다. 기존 복구 영수증 `0f72dd2e-9c5d-4567-aa46-9f3772f79ea7`와 `53d066a9-56dc-4042-9b64-204eb5048156`도 있어야 하고 그대로 보존합니다. 새 복구 작업 ID는 `14aac5ad-5a13-4720-8d9d-4a1074c4cb65`입니다.

정식 복구 함수는 현재 거래소 포지션/활성 주문 없음과 위험 게이트를 확인하고, lease 아래 새 검토 토큰으로 다시 검증해야 적용합니다. 이후 재무 필드, 주문, 요청 상태, 이전 영수증과 이력이 보존되는지 확인합니다. 위험 상태·세션 신뢰 플래그와 새 복구 승인/영수증 외의 변경은 허용하지 않습니다.

서버에 OS 종료 코드나 전체 터미널 결과가 저장되는 구조는 아니므로, 이 도구가 원래 SIGKILL·자연 대기·모든 continuity probe를 독립적으로 재실행/재입증하지는 않습니다. 해당 실제 결과는 `SIGKILL_EVIDENCE_KO.md`에 출처와 범위를 기록했습니다. 복구에서는 현재 계좌와 영속 마커 및 서명 주문 종료 증거를 별도로 대조합니다.

## 실행기와 정보 보존

실행기에는 별도 동봉한 두 Python 소스와 원본 보고서의 검증 필드가 평문으로 들어 있습니다. 각 해시를 확인한 뒤 작업별 임시 폴더(0700, 파일 0600)에서 한 번 호출하고 정리합니다. 기존 임시 폴더가 있으면 덮어쓰지 않습니다. 하위 프로세스를 자동 재실행하거나 바깥 timeout으로 종료하지 않습니다.

API 키·비밀번호는 패키지에 없고 출력하지 않습니다. 새로운 검토 토큰은 출력/수동 복사하지 않고 메모리로 전달하지만, 정식 복구 영수증에는 해당 토큰이 DB에 기록됩니다. 거래소 요청은 조회 전용이며 신규/취소/청산/보호 주문 쓰기는 없습니다. API/DB 재시작·재빌드도 없습니다.

별도 메일 테스트나 메일 생성을 하지 않습니다. 기존 알림 worker는 이미 대기 중인 경고를 기존 설정대로 처리할 수 있습니다. LIVE 거래는 비활성화 상태입니다. 이번 복구나 앞선 SIGKILL 성공만으로 Phase 4 전체 완료를 선언하지 않습니다.
