# 환경 게시 실패 진단

확인 범위: 현재 설정 초안·실행 환경·설치/앱 상태. 플랫폼 게시 작업 로그/스냅샷 작업 상태를 조회하는 도구는 제공되지 않음.

## 확인된 사실

- 설정 초안 revision: 11
- draft ID: a718fbad-4d1b-495a-b68f-477c1caf6c66~cecfgdraft_6ad5e6a2fe70819e98fb18025a104ca4
- base version ID: a718fbad-4d1b-495a-b68f-477c1caf6c66~cecfgver_6ad5e4c720f8819ea8a4b1dc687a3423
- LIVE_TRADING_ENABLED, BINANCE_DEMO_API_KEY, BINANCE_DEMO_API_SECRET은 vault-backed direct runtime 바인딩으로 등록됨. 바인딩 error_code는 모두 null, pending requirement는 없음. 값의 유효성/인증 성공을 뜻하지 않음.
- 현재 실행 환경에는 DEMO key/secret이 여전히 주입되지 않음. 값은 출력하거나 보관하지 않았음. LIVE toggle은 명시적으로 false.
- DEMO 도메인 2개가 저장된 허용 목록에 있음. 현재 실제 public REST는 proxy CONNECT 403.
- 저장된 install_script의 bash 문법 검사 통과. 전체 설치 스크립트 재실행/게시 시 실행 결과는 검증하지 않음.
- uv sync --frozen --offline 통과(39 packages checked).
- DB/API/web 모두 healthy, 실제 HTTP smoke 통과.
- /workspace 여유 공간 약 5.5 GiB. 디스크가 가득 찬 상태는 아님. 플랫폼 스냅샷 크기 제한/작업 여유 공간/게시 권한은 확인 불가.

## 미확인과 필요한 플랫폼 조사

설정 저장과 게시/런타임 적용은 별개다. 현재 자료로 게시 실패 원인을 확정할 수 없고, Binance proxy 403과 게시 실패가 같은 원인인지도 알 수 없다.

지원팀에 위 draft/base version을 기준으로 최근 publish 작업의 실패 단계, 오류 코드, snapshot/activation 로그와 runtime secret 주입 상태를 확인 요청해야 한다. 추가로 저장된 DEMO egress 규칙이 실제 proxy 정책에 반영됐는지 확인해야 한다. API key/secret 값은 진단 자료에 포함하지 않는다.

이번 진단에서는 환경 설정/키 바인딩을 변경하거나 삭제하지 않았고 게시를 재실행하지 않았다. 게시 작업을 수행/조회하는 도구가 없다. 실제 Binance 인증/주문은 실행하지 않았다.


## 재디버깅 — 2026-10-20 14:57~14:59 KST

사용자가 세 환경변수와 두 DEMO 도메인의 등록을 확인한 뒤 다시 조사했다.

- 설정 draft는 revision 12로 증가했다. 세 변수는 vault-backed direct runtime 바인딩이고 pending 없음/error_code=null이다. 저장된 DEMO 도메인 2개도 확인했다. 값의 유효성은 확인하지 않았다.
- 현재 task 프로세스에는 DEMO key/secret 두 변수가 여전히 없다. LIVE_TRADING_ENABLED는 존재하며 명시적 false다.
- 저장된 install_script가 이전 검토된 설치 명령과 정확히 일치하는지 확인한 뒤 실제로 실행했다. exit 0: pinned uv 0.12.23 확인, frozen Python 39개 패키지 확인, npm ci 223개 설치 성공.
- 설치 이후 프런트엔드 6개 테스트와 production build가 통과했다. DB/API/web healthy와 실제 HTTP smoke도 통과했다.
- 동일한 실행 환경/기존 HTTPS proxy 경로에서 developers.binance.com과 pypi.org는 HTTP 200, demo-fapi.binance.com은 proxy CONNECT 403이다. DEMO TLS/인증 요청 전에 proxy가 터널을 거부한 관측이다.
- 직접 DNS 조회는 세 호스트 모두 실패했지만 proxy를 통한 두 HTTPS 요청은 성공했다. 직접 DNS 실패만을 DEMO 403 원인으로 판정하지 않는다. proxy/TLS 우회나 대체 호스트를 사용하지 않았다.
- 네트워크 증거: `.cache/phase4-publication-network-debug-20261020.json` (키 사용/주문 없음).
- workspace 여유 약 5.45 GiB. 플랫폼 snapshot 한도/게시 서버 권한/activation 상태는 확인할 수 없다.

결론: 설정 입력/저장, 현재 머신의 설치 및 앱 검사는 통과했다. 남은 관측 문제는 (1) 저장된 runtime secret이 현재 task에 전달되지 않음, (2) 저장된 DEMO 도메인으로의 proxy CONNECT 거부다. 두 문제와 게시 실패가 같은 원인인지는 미확인이다. 제공된 도구는 설정 draft 읽기/저장뿐이며 publish job 조회/로그/게시 실행은 지원하지 않는다.

지원팀에 요청할 항목:

1. 위 draft ID의 revision 12에 대한 최근 publish 작업 실패 단계/오류 코드와 snapshot/activation 로그.
2. 두 vault-backed DEMO 환경변수가 현재 실행 task에 주입되는 단계의 성공/실패 상태.
3. 저장된 demo-fapi.binance.com / demo-fstream.binance.com egress 규칙의 실제 proxy 반영 여부와 CONNECT 403 거부 사유.

이번 조사에서는 설정/키를 삭제하거나 도메인 범위를 확대하지 않았다. 설치 재실행은 frozen 선언을 사용했으며 기존 소스/DB volume/미커밋 작업을 보존했다. 키/서명/비밀값을 기록하지 않았다. 실제 DEMO 인증/주문 또는 Phase 5를 실행하지 않았다.

## 설정 수정 적용 — 2026-10-20 15:04 KST

사용자의 직접 해결 요청으로 지원되는 설정 수정 경로를 사용했다.

1. /workspace 전체에서 dependency/cache를 구분하고 Git checkout을 재탐색했다. 실제 프로젝트는 1개이며 접근 오류/서브모듈은 없었다. /workspace/.git은 비어 있는 marker로 git root 조회에 실패하고 실제 checkout이 아니었다. toolchain의 lib64 symlink는 이미 조사한 dependency lib 경로였다.
2. 실제 Git root와 GitHub origin, 현재 HEAD를 검증하고 GitHub read-only ls-remote 접근도 통과했다.
3. 저장된 repository mount_path의 빈 값을 실제 폴더 binance-hybrid-ai-futures로 명시했다. 지원되는 선언 형식의 owner/repo, github.com, 전체 현재 HEAD(64e9e7b3d833824c763a2a0f2df1493300744121)를 저장했다. clone/move/reset/commit/push/파일 삭제는 수행하지 않았다. 이것이 게시 실패의 원인이었다고 확인한 것은 아니다.
4. 직전 draft revision 13의 custom domains는 DEMO 2개만 남아 있었다. 기존 워크플로에 필요했던 developers.binance.com / www.binance.com / fapi.binance.com / fstream.binance.com을 복구했다. DEMO 두 호스트와 package_managers preset은 유지했고 wildcard는 추가하지 않았다.
5. 설정 저장 성공과 revision 14를 재조회 확인했다. 키 3개 바인딩을 유지했고 install_script/start_skill은 이번 수정에서 바꾸지 않았다.

수정 후 실제 HTTP smoke는 통과했다. 새 probe `.cache/phase4-after-config-fix-20261020.json`의 DEMO REST는 NETWORK_OR_PROXY_BLOCKED, WSS는 PROXY_OR_HTTP_403이다. 저장은 runtime 반영/게시가 아니며 실제 게시 성공이나 403 해소는 아직 확인되지 않았다. 설정 도구는 requires_publish=true를 반환했다. 사용자 화면에서 수정된 내용을 검토·저장·게시해야 한다. 게시 작업 자체의 실행/로그 도구는 없다.

기존 개발 코드/미커밋 파일/DB volume은 보존했고 인증/주문/Phase 5는 실행하지 않았다.
