# 공식 계약 검토 — 2026-11-07 KST

현재 공식 모듈형 SDK의 고정 커밋 [f75b4a52c5aebf52b1c6ad671b0ce53b96f8e179](https://github.com/binance/binance-connector-python/tree/f75b4a52c5aebf52b1c6ad671b0ce53b96f8e179)에서 확인했습니다.

- [income API 메서드](https://github.com/binance/binance-connector-python/blob/f75b4a52c5aebf52b1c6ad671b0ce53b96f8e179/clients/derivatives_trading_usds_futures/src/binance_sdk_derivatives_trading_usds_futures/rest_api/api/account_api.py#L788): GET `/fapi/v1/income`, optional income_type/start_time/end_time/limit, 시각 포함 경계, IP 가중치 30. 메서드는 page 인자를 노출하지 않습니다. 이것만으로 서버가 page를 무시하거나 거부한다고 단정하지 않습니다.
- [income 유형 enum](https://github.com/binance/binance-connector-python/blob/f75b4a52c5aebf52b1c6ad671b0ce53b96f8e179/clients/derivatives_trading_usds_futures/src/binance_sdk_derivatives_trading_usds_futures/rest_api/models/enums.py#L14): TRANSFER와 WELCOME_BONUS를 모두 포함합니다.
- [이전 SDK](https://github.com/binance/binance-futures-connector-python/blob/a6bfbbf10fe2c1b4eb76fc24ffb82eb94bf9df89/binance/um_futures/account.py#L716)에는 page 설명이 있지만 저장소 README에 DEPRECATED가 명시돼 있습니다. 이를 현재 서버 페이지 동작의 증거로 사용하지 않습니다.

문서 웹사이트 직접 접근은 환경 프록시에서 403으로 거부됐습니다. 공식 SDK 소스를 Git으로 읽었고 설치하거나 실행하지 않았습니다. 각 파일의 전체 해시는 JSON에 있습니다. 이 검토는 현재 사용자의 DEMO 서버에서 필터가 동작한다는 증거가 아닙니다. 그래서 비교 도구는 페이지를 사용하지 않고 1,000건 도달 시 불완전으로 중단합니다.
