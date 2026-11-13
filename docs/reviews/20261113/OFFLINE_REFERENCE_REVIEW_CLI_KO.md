# 별도 기기·오프라인 보관 참조의 검토 명령

사용자는 2026-11-13에 **별도 기기·오프라인 보관**을 선택했다. 이 선택을 보관 절차의 기준으로 반영했다. 실제 기기·경로 설정, Windows 참조 반출, 보관 완료, 최신 digest 등록을 수행하거나 인증하지는 않았다. 계획 값은 OFFLINE_REFERENCE_HANDOFF_PLAN.json에 기록하며 런타임 활성화 설정으로 사용하지 않는다.

## 구현한 명령

`scripts/review_wire_restore_reference.py`는 기존 참조 파일 검증·복원 후보 대조 라이브러리를 감싼 읽기 전용 CLI다. 기본 inspect 모드는 DB 환경변수도 사용하지 않고 파일의 정규화 형식·제공한 보관 SHA-256을 검사한다. DB 접속·스키마 생성·참조 생성·파일 복사·복원·원장 변경·주문·자격 증명 재개 기능은 없다.

inspect가 뜻하는 오프라인 검사는 DB에 접속하지 않는다는 뜻이다. 현재 PC에서 inspect를 실행했다는 사실은 별도 기기에 보관했다는 증거가 아니다. 참조의 진위와 최신성은 이 명령의 성공만으로 확인되지 않는다.

현재 코드가 포함된 POSIX 검토 환경에서 사용할 명령 형태는 다음과 같다. 변수에는 운영자가 선택한 실제 참조 파일과 **별도 보관한 digest**를 넣는다. 현재 파일에서 새로 계산한 digest로 보관값을 대체하지 않는다.

```sh
python -m scripts.review_wire_restore_reference \
  --reference-file "$REFERENCE_FILE" \
  --retained-sha256 "$RETAINED_SHA256"
```

참조 파일은 8 KiB 이하의 소유자 전용 0600 일반 파일이고 부모 디렉터리는 0700이어야 한다. 심볼릭 링크·하드 링크·권한 불일치·본문 변경·정규화 형식 오류는 거부한다. 외부 매체가 POSIX 권한을 보존하지 못하면 검토 환경의 보호된 스테이징 사본을 사용하고, digest는 별도 기기의 보관 기록에서 가져온다. Windows 호스트용 런처나 새 컨테이너 설치 패키지는 이 단계에 포함하지 않는다.

## 별도 기기 보관 절차

1. 검토할 snapshot의 writer가 중지된 검토 환경에서 앞 단계의 참조 생성·명시적 사본 내보내기 함수를 사용한다. 이 CLI는 현재 DB에서 신뢰 digest를 자동 생성하지 않는다.
2. 참조 사본만 별도 기기로 옮긴다. 거래소 키·secret·RAM 세션·DB 접속정보는 참조 보관 작업에 포함하지 않는다.
3. 별도 기기에서 최신 참조 digest와 그 참조를 선택한 기록을 관리한다. 여러 참조 파일 중 파일명이나 현재 원본 PC의 표시만으로 최신값을 추정하지 않는다. 이 코드가 외부 기록의 순서·진위를 인증하지는 않는다.
4. 복원 검토 때 보관한 참조를 보호된 검토 디렉터리에 가져오고, 독립 보관 기록의 digest로 inspect한다.
5. inspect 성공 후 필요할 때만 선택한 합성 journal과 읽기 전용 비교를 수행한다. 어떤 단계도 거래 재개를 허용하지 않는다.

이 단계에서는 기기나 클라우드에 파일을 전송하지 않았다. 경로·기기 라벨은 미설정이며, 계획 파일의 미완료 값은 사용자가 보관 방식을 선택했다는 사실과 구분한다. 한 호스트의 다른 폴더를 별도 기기 보관으로 자동 승격하는 기능도 없다.

## DB 비교 입력과 동작

compare는 `--mode compare --enable-synthetic-review`와 source-directory/account/scope/journal을 모두 명시해야 한다. 참조의 바인딩과 대상이 다르면 DB 접속 전에 거부한다. 참조를 source 디렉터리 안에 두고 비교하는 경로도 거부한다.

접속정보는 명령행 인자가 아니라 전용 `WIRE_REVIEW_DATABASE_URL` 환경변수로만 주입한다. 앱의 DATABASE_URL이나 기본 접속값으로 대체하지 않는다. 지원 드라이버는 postgresql+psycopg다. 비밀번호를 포함한 URL을 터미널 인자나 결과에 출력하는 예제는 제공하지 않는다. 테스트에서는 PostgreSQL의 default_transaction_read_only=on 연결로 실제 비교 성공을 확인했다.

```sh
python -m scripts.review_wire_restore_reference \
  --mode compare --enable-synthetic-review \
  --reference-file "$REFERENCE_FILE" \
  --retained-sha256 "$RETAINED_SHA256" \
  --source-directory "$SOURCE_DIRECTORY" \
  --account "$LOCAL_ACCOUNT" \
  --scope "$WIRE_SCOPE" \
  --journal "$WIRE_JOURNAL_UUID"
```

위 명령은 새 합성 journal/보조 테이블이 있는 검토 환경용이다. 기존 Windows 설치에 새 스키마가 있다고 가정하지 않으며, 실제 Windows 실행을 지금 요청하는 명령이 아니다. DB 비교는 앞 단계의 4,096개·512 MiB 카탈로그 한도, 64개 단위 서버 해시 계산, 읽기 전용 스냅샷과 SQL 시간 제한을 그대로 사용한다.

## 출력·종료 코드

일반 실행은 JSON 한 건을 출력한다. 도움말만 정적인 사용법 텍스트를 출력한다. 원래 주문 ID·참조 원문·키·서명·DB URL·비밀번호·원시 예외·traceback을 출력하지 않는다. 인자 해석 실패도 거부된 인자 값을 되풀이하지 않으며, 오류 코드는 정확히 허용한 정적 목록만 공개한다.

- 종료 0: inspect의 파일/digest 검사 또는 compare의 정확한 snapshot 비교가 일치했다는 뜻이다.
- 종료 2: 입력·대상·권한·접속·파일·스키마 오류 또는 snapshot 불일치로 검토를 차단했다는 뜻이다.
- 종료 0도 보관 완료·최신 참조·실제 계정 신원·안전한 복원·주문 준비 완료를 뜻하지 않는다.

외부 참조 진위/최신성, 전체 시스템 롤백 보호, 전체 런타임 백업, 복원 허가·수행, 세션/writer 재개, 실행·주문·LIVE 승인은 false다. compare의 정상 추가 이벤트나 lease 변경도 선택한 참조와 다르면 불일치다. 자동 참조 갱신·현재값 재핀·자동 재시도·수리·주문 재개를 하지 않는다.

## 검증 및 현재 상태

새 프로세스 테스트에서 기본 모드의 DB 미사용, 실제 읽기 전용 PG 비교, 불일치·변조 차단, 전용 환경변수 요구, 접속정보·인자·예외 메시지 비노출을 확인했다. 전체 결과는 OFFLINE_REFERENCE_REVIEW_CLI_VALIDATION.json에 기록한다.

기존 후보 라이브러리와 기존 런타임 파일은 변경하지 않았다. Windows 적용·실제 참조 보관·네 미해결 요청 조정·34개 이력 보관·용량 문제 해결·거래 재개는 수행하지 않았다.
