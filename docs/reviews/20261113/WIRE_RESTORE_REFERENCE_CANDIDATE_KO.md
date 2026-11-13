# 보관 참조와 복원 후보 상태의 읽기 전용 대조

선택한 계정·wire journal의 상태를 해시 참조로 만들고, 별도로 제공한 보관 digest와 복원 후보 상태를 대조하는 검증기를 구현했다. DB 백업·복원·수리·주문 전송·세션 재개를 수행하는 기능은 없다. Windows 배포도 하지 않았다.

## 참조의 구성

`services/execution/demo_wire_restore_reference_candidate.py`의 create_reference_manifest는 기존 PG·두 파일·금융 연결 검증을 먼저 수행하고, 같은 READ ONLY/REPEATABLE READ 스냅샷에서 다음 정보를 수집한다.

- scope/로컬 계정/journal/설치 UUID와 binding/root 해시.
- 마지막 PG 체크포인트 sequence/해시와 wire 행 해시.
- wire-contract-v1.log 및 wire-checkpoint-v2.log의 전체 바이트 길이·SHA-256.
- 계정 금융 상태 digest와 owner/fence/lease를 포함한 계정 행 digest.
- 해당 계정의 모든 금융 이벤트·DEMO_AUDIT outbox 목록의 카탈로그 digest.
- 기록·세션·준비·미완료 개수, UNKNOWN 여부, paused 상태와 로컬 미해결 요청 개수.

원래 주문 ID·금융 이벤트 본문·키·서명·자격 증명·잔액 값은 참조에 넣지 않는다. 참조는 8 KiB 이하의 정규화한 JSON 바이트이며, 같은 상태에서는 같은 바이트를 만든다. 별도의 생성 시각을 붙여 최신성을 주장하지 않는다. 로컬 DEMO 계정 라벨은 원격 계정 신원이 아니다.

## 전체 금융 이력의 범위와 한도

wire 체크포인트에 직접 연결되지 않은 이전 금융 이벤트도 카탈로그에 포함한다. 이벤트 UUID·fence·시각·kind·본문 길이/SHA-256·outbox topic을 순서대로 해시한다. 본문에 공백 하나가 추가되거나 같은 길이로 내용만 바뀌어도 digest가 달라진다. outbox가 없으면 불완전한 카탈로그로 거부한다.

이력 본문을 전부 API 클라이언트에 가져오지 않는다. DB가 본문 SHA-256을 계산하고 UUID keyset 순서로 64개씩 메타데이터만 반환한다. 대상 이벤트 수·총 본문 바이트·최대 행 바이트를 먼저 확인하며, 한도는 4,096개·512 MiB·행당 2 MiB다. 카탈로그 단계는 페이지 사이에 10초 예산을 확인하고, 각 SQL의 기존 statement/lock timeout 2초를 유지한다. 계정 상태의 추가 참조 검사에는 2 MiB 한도를 둔다. 한도나 시간이 부족하면 참조를 만들지 않고 거부한다.

이 카탈로그는 원문 바이트 완전성 비교다. 모든 이벤트의 도메인 전이를 다시 재생하거나 실제 거래소 체결을 인증하는 전체 이력 증명이 아니다. 참조와 보고서의 historical_events_replayed는 false다. 기존 앵커에 직접 연결된 금융 이벤트의 내용·원장 표식·outbox 검증은 계속 수행한다.

합성 검증에서 3,802개·최소 285,150,000바이트의 추가 이력을 넣고, 카탈로그 질의가 본문 대신 7개 메타데이터 열만 반환하는 것을 확인했다. 이는 클라우드 합성 데이터 결과이며 실제 Windows 성능이나 512 MiB 최대치의 성공을 보장하지 않는다.

## 보관 digest의 사용

review_restored_reference에는 참조 바이트와 retained_sha256을 모두 제공해야 한다. 참조 파일을 읽는 review_reference_file도 같은 digest를 요구한다. 현재 저장소에서 digest를 새로 계산해 신뢰값으로 대신 사용하는 기본 경로는 없다. 참조 바이트의 digest가 제공값과 다르거나 바인딩이 다른 경우 DB 대조 전에 거부한다.

현재 유효 상태가 보관 참조와 다르면 BLOCKED_REFERENCE_MISMATCH_KEEP_PAUSED와 차이가 난 필드 이름을 반환한다. 일치하면 MATCHED_SUPPLIED_REFERENCE_KEEP_PAUSED를 반환하지만 복원·거래·writer 재개 권한은 부여하지 않는다. UNKNOWN/미완료 전송, SUBMITTING 등 로컬 미해결 요청, unpaused 상태도 후속 차단 사유에 남긴다.

대조는 특정 참조 시점과의 **정확한 상태 일치**다. 정상적인 추가 이벤트나 lease 변경도 불일치가 될 수 있다. 오래된 참조가 현재 이력의 조상임을 판정하거나 자동으로 참조를 최신화하는 admission 경로가 아니다.

## 외부 보관과 최신성의 한계

현재 저장소와 참조를 함께 과거로 되돌리면, 오래된 참조와 오래된 상태는 다시 일치할 수 있다. 코드가 호출자의 digest를 실제 외부에서 보관한 최신 값인지 인증할 수는 없다. 다음 값은 일치 여부와 관계없이 false다.

- external_reference_authenticity_verified / reference_is_latest_proven.
- whole_system_rollback_protection_verified / whole_runtime_backup_verified.
- storage_restore_authorized / restore_performed.
- process_restart_resumption_supported / credential_session_resumed.
- 실행·주문·LIVE 승인.

테스트에서는 대상 계정의 금융 상태·이벤트·outbox·wire 체크포인트와 두 파일을 일관된 과거 상태로 되돌렸다. 기존 내부 앵커 검사는 통과해도, 되돌리지 않고 남겨둔 최신 참조로 대조하면 차이를 검출했다. 반대로 오래된 참조를 제공하면 일치할 수 있다는 한계도 테스트에 명시했다. 전체 PostgreSQL 인스턴스나 다른 계정·다른 journal·quota SQLite·설정·자격 증명을 모두 검증하는 런타임 복구 증명은 아니다.

export_reference_copy(..., apply=True)는 별도 소유자 전용 0700 디렉터리에 0600 참조 사본을 저장한다. 기본값은 비활성이며 명시적 호출만 허용한다. source 디렉터리 내부·심볼릭 링크·권한 불일치·기존 다른 내용은 거부하고, O_EXCL 및 파일/디렉터리 fsync를 사용한다. 같은 내용의 재호출은 파일을 덮어쓰지 않는다. 부분 저장이나 fsync 오류는 성공을 반환하지 않고 자동 복구하지 않는다.

이 함수는 사본을 실제 외부 서비스나 별도 장애 영역으로 전송하지 않는다. 같은 호스트의 다른 디렉터리에 둔 사본도 외부 신뢰 앵커라고 주장하지 않는다. 외부로 보관할 장소·권한·최신 digest 등록·서명/공증 계약은 별도 결정이 필요하다. 이 단계에서 실제 사용자 상태나 digest를 외부에 전송하지 않았다.

## 사용 절차의 경계

1. 검토할 snapshot의 writer를 중지한 상태에서 참조를 만든다. 참조 생성은 온라인 전체 백업 기능이 아니다. 동시 writer/스냅샷 경합·시간 제한은 보수적으로 거부될 수 있다.
2. 운영자가 독립 보관한 참조와 digest를 선택한다. 비교 대상에서 새로 만든 digest로 대체하지 않는다.
3. 복원은 별도 격리된 환경에서 별도 절차로 수행한다. 본 구현은 복원 명령이나 DB 변경을 하지 않는다.
4. 새 프로세스의 읽기 전용 대조 결과와 차이 필드·미해결 요청을 검토한다. 일치해도 자격 증명이나 writer를 재개하지 않는다.
5. 원격 계정 신원·신선한 거래소 상태·복구 범위 완전성·재시작 프로토콜이 별도로 검증되기 전에는 paused 상태를 유지한다.

기존 Windows의 네 미해결 요청·34개 주문 이력·기준금·용량 부족은 변경하거나 해결하지 않았다. 설치 패키지나 Windows 실행 명령을 추가하지 않았다. 세부 테스트 결과는 WIRE_RESTORE_REFERENCE_CANDIDATE_VALIDATION.json에 기록한다.
