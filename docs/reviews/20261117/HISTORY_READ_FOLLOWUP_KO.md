# 내보내기 작업 ID 미확인 후 일반 이력 조회

사용자 화면에서 고정 입력 검사 통과, 숨김 입력, 실제 호출 5회와
EXPORT_CREATION_OUTCOME_UNRESOLVED를 관측했다. 기존 코드의 이 분기는 HTTP 200
본문을 JSON 객체로 해석한 후, 문자열 downloadId가 없거나 모양이 맞지 않을 때 발생한다.
데모 미지원 안내 객체, 숫자형 ID, 다른 응답 형태 모두 이 분기로 들어갈 수 있다.
화면만으로 어떤 경우인지 확인하지 못한다. 키가 잘못됐거나 내보내기 미지원이라고 단정하지 않는다.
이 단계까지 갔다는 사실은 코드상 두 anchor 대조를 통과했음을 시사하지만, 직접 응답 파일을
검사한 독립 검증으로 승격하지 않는다.

## 원래 도구의 진단 누락

원래 버전은 ID 확인에 실패하면 생성 응답을 저장하지 않았다. 따라서 사용자 PC의
원래 응답은 이번 화면이나 저장된 guard/anchor에서 복원할 수 없다. guard가 남아 있으므로
다시 생성해 응답을 얻는 방법을 사용하지 않는다. 다음 릴리스용 소스에는 응답 객체를
CREATION_RESPONSE.json으로 먼저 저장하는 수정과 누락 ID/숫자 ID 회귀 검증을 추가했다.
이번 사용자에게 재생성용 수정 패키지를 제공하지 않는다. 현재 시도 상태와 guard를 보존한다.

## 별도 일반 조회 실행기

새 history-read 패키지는 /order/asyn 등의 생성 및 상태 경로와 Invoke-HistoryExport를
포함하지 않는다. READ_TRANSPORT allowlist는 time/exchangeInfo/두 anchor 주문 GET과
UTC 2026-10-20 하루의 BTCUSDT allOrders/allAlgoOrders GET만 허용한다.
시간·호스트·서명·헤더·2건 계정 대조·NTFS 보호 helper를 재사용한다. 기존 내보내기
디렉터리와 guard는 읽지도 수정하지도 않는다. original writer/복사본/DB 시작은 없다.

목록 HTTP 200 본문은 스키마 검사 전에 새 비공개 폴더에 UTF8 바이트로 저장하고 해시를
대조한다. 안내 객체/미지원 JSON이어도 원문은 남고 READ_RESPONSE_NOT_ARRAY_SAVED_PRIVATELY로
중단한다. HTTP 오류는 기존의 고정 status 분류만 출력하고 오류 원문은 저장하지 않는다.
키나 Secret 원문이 성공 응답에 있으면 저장 전에 거부한다. stdout에는 raw ID/원문을 내보내지 않는다.

출력의 exact_id_candidate_count는 요청 ID 해시가 같은 후보 수다. 상태·방향·조건·체결 연결
검증이 끝났거나 원장 해소가 승인됐음을 뜻하지 않는다. 1000행은 포화 표시하고, 1000행 미만도
보관 제한 때문에 전체 이력 완전성이나 미전송 증거로 사용하지 않는다. 기존 내보내기 작업의
접수 여부는 여전히 미확정이다. CSV·자금 이력·기준금액 검토도 완료하지 않는다.

Windows에서는 RUN_CHECK 후 RUN_READ_HISTORY를 한 번 실행한다. 키는 PC의 숨김 입력으로만
받는다. 완료/실패 화면만 확인하고 비공개 원문을 채팅에 요구하지 않는다. 실제 Windows 실행 전에는
성공으로 기록하지 않는다. 원본·기존 복사본 정지와 USB 분리는 유지한다.
