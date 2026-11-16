# 저장된 realized와 수수료 처리 검토

사용자는 보존본 읽기 성공 화면을 제공한 뒤 다음 단계 진행을 요청했다.
관찰된 구성: initial=5000, realized=-1.25816392, unrealized/fees/funding/net_deposits=0,
계산 평가액/가용잔고/일간·주간 기준액=4998.74183608, high_water=5000.
이는 날짜13 채택 이전 old_row와 당시 완료 보고서의 기록이며 현재 DB 확인이 아니다.

## 확인한 계산 의미

services/execution/demo.py의 DemoCoordinator._merge는 거래소 평가액 remote.equity에
실현/미실현 손익·수수료·펀딩 등이 이미 반영된 것으로 취급한다. 확인된 순이체만
net_transfers로 분리하고 새 AccountValue를 다음처럼 구성한다.

- initial = 기존 초기값
- realized = remote.equity - initial - net_transfers
- net_deposits = net_transfers
- unrealized/fees/funding = AccountValue 기본값 0

AccountValue.equity는 initial+realized+unrealized-fees+funding+net_deposits이다.
따라서 이 DEMO 경로의 realized는 거래소의 Realized PNL 항목 합계가 아니라,
평가액에서 초기값/확인된 순입금을 뺀 잔차이다. 미실현손익이 있으면 그 영향도
포함될 수 있으며, 미분류/누락 이체가 있다면 출처가 구분되지 않은 영향도 남을 수 있다.
fees=0은 거래소 수수료가 없었다는 증명이 아니다. 같은 수수료를 별도로 한 번 더
차감하면 평가액이 잘못 줄어든다. 코드나 원장은 변경하지 않는다.

관찰 수치의 식은 4998.74183608 - 5000 - 0 = -1.25816392이다.
이는 잔차의 산술적 일치이며, 저장된 주문34건에 관련된 전체 체결·수수료·펀딩·입출금 합계 검증은 아니다.
웹에서 본 일부 수수료·수익 기록을 전체 기간의 완전한 자료로 확대하지 않는다.
초기 5000의 지급 출처와 기준일 자정 평가의 근거도 여기서 입증하지 않는다.

## 검증 범위

현재 _merge의 AST는 기준 소스 e03b6ba683732cd5872c30cf0d245a5b1fb6d325의 _merge와
동일함을 확인했다. 실제 사용자 중지 이미지의 파일을 이번에 다시 읽은 것은 아니다.
운영 메서드를 메모리의 합성 DemoLedger/DemoSnapshot에만 호출했다. 기록 수치 재현,
동일 평가액 반복, +100 이체 분리, 동일 이체 반복 4개 검사가 통과했다.
DB·실제 키·HTTP·원본/사본·USB에는 접근하지 않았다. AccountValue에 설정한 기존 가상
fees도 원격 평가액 기준으로 별도 차감되지 않는 동작을 확인했다.

이번 단계의 결론은 수수료 이중 차감 방지 방식과 realized 필드의 의미 확인이다.
새 ZIP/사용자 실행 명령은 필요하지 않다. 초기 지급/전체 이력 완전성, 손절·종료 요청의
해소, 새 기준액·날짜 채택 또는 거래 재개는 여전히 미확인/비승인 상태이다.
원본/복구 사본 정지와 USB 분리를 유지한다.
