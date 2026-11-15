# 실제 읽기 조회 전 로컬 자격증명 문맥 검토

사용자 Windows 화면에서 저장 영수증 검사 성공을 확인했다. content_review는 객체이고 remaining_requirements는 8개 항목의 배열이며 동반 JSON이 일치한다. 파일 변경·Docker 사용·서비스 시작은 없었다. 외부 해시와의 대조는 false이며 원격 계좌 신원이나 날짜 채택은 확인된 것으로 처리하지 않는다. 새 독립 Windows 도구 접근은 없다.

기존 코드의 review_identity_capability는 현재 계좌 GET 프로토콜이 원격 계좌 신원과 자격증명 세션을 연결할 수 없다고 명시한다. 응답의 uid/accountAlias 또는 로컬 fingerprint를 원격 신원으로 만들지 않는다. 기존 key-session loader는 별도 보호 tmpfs의 만료되는 자격증명을 관리하지만, 이번 단계는 해당 파일을 읽거나 세션을 시작하지 않았다.

LocalReadCredentialContext 후보는 명시적으로 받은 DEMO 자격증명만 RAM에 보관한다. 기본 비활성화이며 copy_only와 enable_local_context를 정확한 true로 요구한다. 환경 변수 fallback·키 파일 로더·비밀 입력 프롬프트·네트워크 sender·원장 변경 경로가 없다. LIVE/일반 키가 섞인 문맥을 거부한다. 원본이나 실제 Windows에 설치하지 않는다.

문맥은 계좌/scope, 임의 UUID, 현재 PID, 시계 함수, UTC/단조 시계와 boot에 묶인다. 수명은 75초이며 시계 후퇴·부팅 변경·drift·문맥/키 교체를 거부한다. 자격증명 fingerprint는 임의 salt를 이용한 RAM 내부 변경 탐지값이며 출력·파일·계좌 신원에 사용하지 않는다. 공개 descriptor에는 비밀 없이 임의 문맥 ID와 DEMO label만 담는다.

한 번만 준비하는 GET /fapi/v3/account의 query를 서명하고, 전송 직전에 동일 capability, 키, HMAC 서명 및 5초 이내 timestamp를 확인한다. 서명된 query/key 자체의 교체도 탐지한다. consume 검증 실패도 문맥을 사용한 것으로 처리하므로 수정 뒤 자동 재시도하지 않는다. 전송 후에는 문맥·서명 일치와 전체 수명을 재확인한다. 이미 보낸 요청의 timestamp가 응답 도착 뒤에도 계속 5초 이내일 필요는 없으므로 정상적인 응답 지연과 전송 전 freshness를 구분한다.

capture_bound_copy_account는 같은 계좌/scope의 V2 journal과 RAM 문맥을 연결하고 그 UUID를 기존 archive intent의 credential_context_id로 사용한다. 전송 함수는 호출자가 명시적으로 주입한다. 합성 테스트에서만 주입했으며 실제 네트워크 연결 경계는 아직 구현·검증되지 않았다. 입력 오류·전송 오류·HTTP 오류·중간 문맥 변경 시 문맥을 닫고 부분 기록을 보존한다. API secret이 응답에 그대로 포함되면 원문 보관 전에 거부하며 기존 archive의 key/signature echo 거부도 유지한다.

close는 자신의 참조를 해제하지만 Python/운영체제 메모리 전체의 안전한 삭제나 같은 권한 프로세스의 메모리 열람 방지를 보장하지 않는다. 문맥과 준비 capability의 복사·pickle 직렬화는 거부한다. 문맥 ID는 원격 계좌 신원이 아니며 자격증명이 어느 보호 세션에서 왔는지 독립적으로 확인하지 않는다. copy_only 역시 실제 컨테이너 격리 증명이 아니다.

local_signature_checked는 이 호출에서 수행한 메모리 내 검사 결과다. 기존 V2 스키마는 opaque UUID와 요청/응답 해시만 보관하므로 재개방 후 서명 검증의 영속 증명으로 승격하지 않는다. 독립 외부 anchor, 보호 tmpfs와의 연결, 실제 계좌 신원은 추가 검토 대상이다. operator 진술·근거 수락·날짜 채택·설치·주문 승인은 계속 false다.

관련 테스트 76개가 통과했다. RAM 문맥·PG/SQLite 보관·기존 archive 회귀 69개(44.29초), 네트워크 없는 기존 이미지 import/기본 거부 7개(25.99초)다. ruff와 후보 모듈 mypy도 통과했다. 키/secret 교체, query/capability 교체, 만료·부팅·시계·PID 변경, 재사용, 복사/직렬화, 민감 정보 출력 방지, 원문 secret echo 거부, 원장 보존과 실패 후 재전송 차단을 검사했다. 모든 자격증명과 sender는 합성 자료다. 실제 키 세션·거래소 HTTP·Windows 변경은 없다.

다음 범위는 보호된 일시적 자격증명과 새 복사본의 격리 경계 검토다. 실제 조회나 기존 컨테이너 시작을 자동 실행하지 않는다. 이번 단계에서 사용자가 실행할 추가 명령은 없다. 기존 사본 정지 및 USB 분리 상태를 유지한다.
