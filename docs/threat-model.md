# Phase 2 위협모델

자산: 향후 거래 API 키·계좌 자금·위험 설정·명령/체결 감사 이력·빌드 공급망.
신뢰 경계: 브라우저↔API, API↔DB, 미래 실행기↔거래소, AI↔독립 위험 게이트, CI↔패키지 레지스트리.

| 위협 | 현재 방어와 검증 | 남은 작업 |
|---|---|---|
| 실거래 토글 오작동 | 모든 LIVE 시작 거부, DEMO 거부, 주문·계정 호출 없음, public GET transport만 제공; toggle/위험 누락 테스트 | Phase 8 전용 활성화 게이트 |
| AI가 위험 한도 증가 | 위험/신호 frozen·extra forbid, 변경 API 없음; 변조 입력 테스트 | 인증·승인·버전 이력, AI 전용 계약 |
| 시장가 신규 진입 | EntryOrder LIMIT literal, Signal LIMIT_ONLY; MARKET 거부 테스트 | 실행기와 주문 필터 계약 테스트 |
| 키/DB 자격증명 유출 | 브라우저에 상태 필드만 전달, DB URL SecretStr, 설정 오류·DB 오류 입력 미노출 테스트, env/key git/docker ignore | 실제 키 보관·서명·회전·로그 마스킹 검증 |
| 잘못된 금액·시간 | float/NaN/Infinity 거부, Decimal JSON 문자열·UTC 검증 | exchange 단위/반올림·NUMERIC 모델 |
| 보호 완료 오인 | Algo UNKNOWN, 포지션 미보호·대조 필요 기본값, UI 거래 데이터 미표시 | 체결/보호 상태 재대조·T-01~T-18 |
| 중복·재전송 | event ID/버전 계약, raw hash와 candle DB unique, 충돌 거부 | 계정/명령 DB unique·outbox·fencing·거래 재시도 정책 |
| CSRF/권한 우회 | 현재 제어 라우트 전무·CORS 허용 없음 | 제어 라우트 도입 전에 인증·MFA·CSRF·재인증 |
| 외부에 개발 서비스 노출 | Compose host 포트 127.0.0.1 바인딩, 앱 컨테이너 비root | 운영 TLS/WSS·관리 접근 제어·DB 계정 분리 |
| 공급망 변조 | uv/npm lock 무결성, Docker digest·Actions commit 고정, TLS/CA 검증 | 정기 취약점·업데이트 검토 |

개발용 PostgreSQL `dev/dev`는 비밀키가 아니며 운영에서 재사용하면 안 된다. VITE_* 변수나 클라이언트
번들에 거래 키를 넣지 않는다. 이 시스템은 아직 실거래 안전성을 검증하지 않았으며 수익성을 주장하지 않는다.

공개 데이터 경계: 고정 host와 GET allowlist, redirect 금지, 제한된 frame/응답 크기, raw/normal 분리 저장,
재연결 후 REST bridge, stale/future 시각 거부, DB 오류 시 503로 처리한다. private URL은 SecretStr로 감싸지만
실제 연결은 거부한다. 테스트 fixture는 합성 public 데이터이고 실제 계정 식별정보를 포함하지 않는다.

연구 경계: BACKTEST profile/후보는 실행 명령이 아니며 LIVE 설정에 병합하지 않는다. 입력 Candle 연속성·상장시점·가용 시각,
confirmed pivot과 prefix-stable 지표를 검사한다. SL/TP/진입 순서 미상·funding 수취 미확정을 유리하게 처리하지 않는다.
리포트는 SYNTHETIC/DECLARED 출처·config/data/funding/구현 hash와 가정을 포함하고 기존 결과 파일을 덮어쓰지 않는다.
실제 historical revision/queue/margin/청산은 미검증이므로 연구 결과를 실거래 안전성/수익 보장으로 표시하지 않는다.
