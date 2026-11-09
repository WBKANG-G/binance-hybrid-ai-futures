# accountConfig 공통 스냅샷 후보 검토

Windows에서 확인한 GET /fapi/v1/accountConfig의 두 bool 필드를 매 스냅샷마다 새로 읽도록 적용한 격리 후보입니다. 이 패키지는 코드와 검증 자료이며 Windows 설치 패키지가 아닙니다. 실행/주문/설치 명령은 없습니다.

기준 설치 소스: 70a854ca677b0a8e3355b7f99ac0e0cce3bc6ba5. 기존 미설치 operational-history parser 후보는 합치지 않았습니다. 생산 코드 변경은 integrations/binance_usdm/demo.py와 demo_snapshot.py 두 파일뿐입니다.

- SDK: accountConfig를 signed GET 허용 목록에 추가합니다. 서명·시계·DEMO host·TLS·응답 decoder·rate-limit cooldown·주문 권한은 그대로입니다. accountConfig POST/PUT/DELETE는 거부됩니다.
- Parser: 기존 positionSide/dual와 multiAssetsMargin 두 GET을 새 GET 하나로 바꿉니다. 두 필드는 모두 정확히 bool False여야 합니다. 누락/null/문자열/정수/True는 중단됩니다.
- 매번 새 GET을 사용하며 캐시, 자동 fallback, 재시도는 없습니다. 418/429는 기존 decoder를 통해 cooldown으로 연결됩니다. 실패 후 과거 두 조회를 덧붙이지 않습니다.
- 운영자·미리보기·DB재시작·worker recovery·API재시작·disconnect 검사·공통 parser의7개 운영 연결을 확인했습니다. 전체 history reader도 동일한 새 mode GET을 사용하며, 그 이후 history와 금융 파싱 코드는 원본과 같습니다.
- 실제 새/원본 parser에 동일 합성 입력을 넣어 전체 snapshot, 금융/transfer, 원래 주문/보호조회가 같은지 대조했습니다. 조회 수는 스냅샷마다1개 감소하며 쌍 기준 기존28→26, readiness의 기존22→20입니다. 기존 readiness history 생략 규칙은 그대로입니다.

공식 계약과 Windows에서 관측한 모델 기준 두 mode 조회60→5로55 감소, 스냅샷 쌍은110 감소합니다. 이는 해당 두 필드 조회의 모델이며, 전체 트래픽91.7% 감소 또는 활성 TP 예산 통과를 뜻하지 않습니다. IP counter에는 다른 활동이 포함될 수 있습니다. 활성 position/private-stream 부하 검증과 Windows 설치는 이번 범위에 포함하지 않았습니다.

STRUCTURE_PROOF.json은 생산 SDK의 허용 목록 외 AST와 parser의 mode 검사 뒤 AST가 원본과 같음을 확인한 결과입니다. 잠금 명세·저장소·리더 lease·TP·보호·기존 설치 가드 등19개 파일은 바이트 동일합니다. 기존 설치 가드 승인 해시는 넓히지 않았으며, readiness/rate 가드는 후보 소스를 키/DB 접근 전에 거부하는 검사를 포함합니다. 과거 read-pool guard의 main은 DB 생성 전에 소스 검사하는 구조가 아니므로 그 속성을 주장하지 않습니다.

기존 private-stability 테스트의 관측 시간 문제는 승인본에서도 재현된 테스트 전용 수정본을 재사용했습니다. 생산25분 갱신 조건은 바꾸지 않았고, genuine renewal과30초 실시간 watchdog을 요구합니다. 별도 TIMING_FIXTURE_NOTE.json을 참고하세요.

검증 수와 제외 파일은 VALIDATION.json, JUnit 원본은 test-results/에 있습니다. 버전 고정 가드의 양성52개는 승인 설치본70a854c에서 변경 없이 별도로 실행합니다. 장시간 자연 lease SIGKILL5개 파일은 재실행하지 않았습니다. 후보에서는 실제 공통 parser가 적용돼 있으며 소비자별 양성 테스트용 parser 주입을 사용하지 않습니다. 승인 parser fixture는 동일 응답의 비교 기준으로만 사용합니다.

현재 Windows source, DB, 일시정지 상태를 변경하지 않았습니다. 주문 없음, LIVE 활성화 없음. main 변경/병합 없음. 현 시점의 일자 기준값과 거래소 position/order 상태는 이번 사용자 결과에서 검사되지 않았습니다.
