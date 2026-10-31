# 기존 종료 확인도 일반 오류로 차단됨

사용자 캡처: 동일 windows-demo-entry-response-loss 폴더에서 run_demo --config configs/demo-isolated.example.yaml --account DEMO-WINDOWS-ISOLATED-BTC-V1 --enable-demo --confirm-flat 실행.

출력: DEMO operation blocked or unresolved; inspect sanitized ledger/status, network and runtime credential bindings

이 출력은 run_demo가 DemoFailure/RiskRejected/ValueError/KeyError/OSError를 일반 문구로 처리하는 경로다. 어느 단계/구체적 예외인지 확정할 수 없다. 포지션 잔존·flat·종료 체결 어느 것도 입증하지 않는다. 이전 응답 유실 시험의 종료 POST1회/ACKNOWLEDGED/UNKNOWN flat 및 EMERGENCY/paused 상태를 보존하며, 새 종료/진입/시험/복구 승인을 실행하지 않는다. Docker Gordon 문구를 Compose 고장으로 단정하지 않는다.

다음은 현재 BTCUSDT positionRisk의 signed GET1회와, 기존 diagnose_demo_ledger의 OPERATION 상수를 현재 UUID로 메모리에서만 지정한 읽기 전용 DB 조회다. 코드 파일·DB 상태·주문은 변경하지 않는다. 키는 기존 private tmpfs에서 읽고 포지션의 허용 필드만 출력한다. 원격 응답과 로컬 장부를 분리해 판단한다.
