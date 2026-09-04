# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

한국 주식(KOSPI) 대상 SFS(Sequential Factor Screening) 퀀트 랭킹 엔진. DART 공시와 KRX 시세를 모아 로컬 JSON DB를 만들고, 팩터 Z-스코어로 종목을 랭킹한다. 코드·주석·출력이 모두 한국어다.

## 실행

가상환경 인터프리터를 직접 지정해서 실행한다 (activate 없이).

```
venv\Scripts\python.exe main.py                                  # 전체 파이프라인 → Top 10 출력
venv\Scripts\python.exe sfs_backtest.py --start_year 2021 --start_quarter 1 --weight equal
venv\Scripts\python.exe sfs_verify_yfinance.py                   # sfs_trades_log.csv를 yfinance 수정주가로 교차검증
venv\Scripts\python.exe sfs_step1_universe.py                    # 각 sfs_step*.py는 __main__ 블록으로 단독 실행 가능
venv\Scripts\python.exe check_finance_data.py                    # 지정 종목·기간의 분기 데이터를 표로 조회 (운영 DB 안 건드림)
venv\Scripts\python.exe sfs_dart_html.py                         # 공시원문 파서 단독 실행 (KB금융 2021Q1~2023Q2)
venv\Scripts\python.exe test_finstate_check.py                   # 원문 파싱 검증 스크립트 (KB금융, 자체 파서 사본 보유)
```

`sfs_backtest.py`의 `--start_quarter`(1~4, 기본 1)는 `start_year`의 몇 번째 리밸런싱 월(4·6·9·12월)부터 시작할지 지정한다. `--start_year`만으로는 항상 그 해 4월부터 시작하며, 첫 리밸런싱의 기준분기는 전년도 Q4로 잡힌다(달력 분기와 다름, `get_latest_available_quarter` 참고). 다만 12분기 수집 창이 해마다 전년도까지 자연스럽게 닿으므로, 과거 분기를 담으려고 `start_year`를 낮출 필요는 보통 없다.

`check_finance_data.py`가 데이터 확인용 주력 도구다. 상단의 `TICKERS`/`FIRST_YQ`/`LAST_YQ`만 고치면 임의 종목·기간을 조회할 수 있고, step2의 수집 로직(API 우선 → 금융업 공백은 원문 대체)을 그대로 재사용하되 결과를 로컬 딕셔너리에만 담아 `raw_finance_data.json`에는 쓰지 않는다.

테스트 프레임워크는 없다. 검증은 위 단독 실행 스크립트의 출력을 눈으로 확인하는 방식이고, 파싱 로직을 고쳤다면 변경 전후 출력을 파일로 저장해 diff하는 것이 유일한 회귀 확인 수단이다. `scratch/`는 gitignore 대상이라 일회성 검증 스크립트를 두기 좋다. DB를 건드리는 수정을 검증할 때는 `S.DB_FILE_PATH`를 `scratch/` 아래 임시 경로로 바꿔서 운영 DB를 보존할 것.

`requirements.txt`가 없다. venv에 pandas / numpy / scipy / OpenDartReader / pykrx / requests / beautifulsoup4 / tqdm / matplotlib / yfinance / python-dotenv가 설치돼 있다. `.env`에는 `DART_API_KEY`, `KRX_API_KEY`가 필요하다.

## 파이프라인

`main.py`가 7단계를 순서대로 호출하고, `sfs_backtest.py`는 같은 순서를 분기별 리밸런싱 날짜마다 반복한다.

1. **step1 유니버스** — KRX 시총 컷(기본 1조) 스크리닝
2. **step2 데이터 빌더** — DART/KRX에서 `raw_finance_data.json` 동기화
3. **step3 quality / value** — 원시 지표 산출 (두 파일로 분리)
4. **step4 펀더멘털 스코어링** — Z-스코어 → 상위 50% 컷오프
5. **step5 price / op momentum** — 생존 종목만 대상으로 원시 지표 산출
6. **step6 모멘텀 스코어링** — Z-스코어 → 상위 50% 컷오프
7. **step7 최종 Re-Z-scoring** — 최종 생존 그룹 안에서 **모든 지표를 다시 Z-스코어화**해 종합 랭킹

컷오프 비율과 계층적 가중치(팩터 → 섹션 → 지표)는 전부 `sfs_config.py` 한 곳에 있다. 스코어링 로직을 바꿀 때 상수를 개별 파일에 흩뿌리지 말 것.

## 데이터 계층

`raw_finance_data.json` (gitignore 대상):

```
{종목코드: {"category": "일반기업|금융기업",
           "<연도>": {"<분기>": {assets, liabilities, equity, op, ni,
                               ocf, capex, dividend, buyback, shares, src}}}}
```

**연도·분기 키는 문자열**이고, 금액은 **원 단위**, 플로우 항목(op/ni/ocf/capex/dividend/buyback)은 **분기 단독값**이다.

- step2는 이미 있는 키는 건너뛰고 **누락된 키만 핀셋 다운로드**한다. 값이 0이어도 "있는 것"으로 간주한다.
- **`None`은 절대 저장하지 않는다.** `sfs_step2_data_builder.store()`가 구할 수 없는 값(`None`)은 키 자체를 지운다 — 다음 실행에서 `missing_keys`가 다시 잡아 재시도하게 하려는 것이다. 소비부(`sfs_step3_*`)는 필요한 분기 중 하나라도 값이 없으면 그 종목을 건너뛴다(0으로 채우지 않는다).
- `src`는 그 분기를 공시 원문에서 가져왔을 때 `'html'`, 상장 전 등으로 **영구히 구할 수 없다고 확인됐을 때** `'na'`가 붙는다. `'na'`는 최신 2분기(depth<2)에서는 무시하고 매번 재확인한다(공시가 뒤늦게 올라올 수 있어서).
- `sfs_utils._DOC_CACHE`가 (종목, 연도, 보고서코드, fs_div) 단위로 DART 응답을 캐싱하고, `_EMPTY_DOCS`가 "이 보고서는 없다"는 사실만 별도로 기억한다(표 데이터를 안 들고 있어 메모리 부담 없음). `_EMPTY_DOCS`는 종목이 바뀌어도 비우지 않는다 — 없는 보고서는 종목을 다시 순회해도 여전히 없기 때문이다. `clear_finstate_cache()`/`sfs_dart_html.clear_html_cache()`는 종목 하나가 끝날 때마다 호출해 `_DOC_CACHE`만 비운다.
- `krx_cache/*.pkl`은 일자별 전종목 시세, `docs_cache/`는 OpenDartReader가 직접 쓰는 corp_code 캐시다.

### 수집 범위와 Tier

`get_required_quarters(base_y, base_q, total_depth=12)`가 기준 분기에서 12분기를 잡되 **가장 오래된 해는 1분기까지 확장**하고, **오래된 순으로** 반환한다.

Tier는 순회 순번이 아니라 연도 기준으로 정한다. `tier1_start_year()`가 "Tier-1(최근 5분기)이 걸치는 가장 오래된 해"를 구하고, 그 해 전체(1분기부터)를 Tier-1로 승격시킨다 — 밸류 TTM(T-1~T-4)과 퀄리티 환원금인상률(T~T-4)이 모두 그 안에 있어야 하기 때문이다.

| 구분 | 조건 | 수집 키 |
|---|---|---|
| Tier-1 (퀄리티/밸류) | `연도 >= t1_year` | 전체 (금융기업은 ocf/capex 제외) |
| Tier-2 (OP 모멘텀) | 그 외, `depth < total_depth` | `op` |
| 보조 (4분기 계산용) | `depth >= total_depth` | `op`, `ni` |

`shares`는 밸류 지표용이라 Tier-1에만 받는다. **수집 범위를 임의로 넓히지 말 것.** 범위는 리밸런싱 날짜에서 파생되는 설계이고, 과거 분기는 `sfs_backtest.py`가 그 시점 날짜로 돌 때 채워진다.

## API 경로와 공시원문 경로

`finstate_all`은 **금융업의 2023Q3 이전 보고서를 아예 조회하지 못한다**(CFS·OFS 모두 0행). 분기보고서뿐 아니라 **사업보고서(연간, `11011`)도 마찬가지**다 — KB금융은 2021·2022년 연간이 0행이고 2023년부터 정상이다. 그 구간을 메우려고 `sfs_dart_html.py`가 공시 원문 HTML을 직접 파싱한다.

- 판정(`is_api_gap`): 금융기업이면서 `op`가 **키는 있는데 0**이면 API 공백으로 보고 원문으로 간다. 한 번 원문에서 가져온 분기는 `src='html'`로 기억해 이후 API를 건너뛴다. **이 "영구 신뢰"는 파싱 로직을 고친 뒤에는 함정이 된다** — 예전 버그로 잘못 확정된 `src='html'` 레코드는 코드를 고쳐도 재검증되지 않는다. 파싱 규칙을 바꿨다면 `op==0`(또는 다른 의심 패턴)인 `src='html'` 레코드를 찾아 해당 키와 `src`를 지우고 재수집해야 한다.
- 원문 경로는 그 분기의 **모든 값**을 가져온다(`HTML_KEYS`). `shares`만 KRX 소관이라 제외.
- 일반기업은 원문 경로를 타지 않는다. 파서에 `ocf`/`capex`가 없어 그 두 키가 영구 누락으로 남기 때문이다.
- **4분기 손익은 `연간 - Q3누적`으로 구한다** (`Q1+Q2+Q3` 합이 아니다). `finstate_all`은 같은 행에 3개월 단독값(`thstrm_amount`)과 당기누적값(`thstrm_add_amount`)을 컬럼으로 함께 준다. 3분기보고서의 누적 컬럼을 쓰면 연간·3분기 보고서 두 건만으로 계산되어, Q1·Q2가 없는 경우(연중 상장, 금융업 공백)에도 정확하다. `sfs_utils.get_discrete_is`가 이 방식이다.
- **계산된 값으로 공백을 판정하지 말 것.** 보고서 자체가 없으면 `extract_is_value`/`extract_cf_ytd_value`/`get_stock_snapshot`는 `None`을 돌려준다(과거 `0.0`을 돌려주던 것을 고침 — 없는 분기가 0으로 취급돼 계산에 섞여 들어갔었다). **보고서는 있는데 특정 계정 행만 없는 경우는 여전히 `0.0`**이다(그 분기에 배당을 안 줬으면 실제로 0이다). 이 구분이 핵심이다.

### 원문 표 파싱 규칙 (`sfs_dart_html.py`)

- **값 열이 '세부 항목 열 / 소계 열'로 나뉜다.** `Ⅷ.영업이익` 같은 소계 행은 한 칸 오른쪽에 값이 있다. `first_valid_amount()`가 빈 셀을 건너뛰어 두 경우를 모두 처리한다. 빈 셀은 레이아웃, `-`는 값 0으로 구분한다.
- **2023Q3부터 손익계산서 헤더가 `3개월 | 누적`으로 나뉜다.** 그 이전에는 `제15기 반기`처럼 누적 한 열뿐이다. `find_period_cols()`가 두 열 인덱스를 찾고, **3개월 열이 있으면 그대로 분기값, 없으면 누적에서 직전 분기 누적을 뺀다.** 4분기는 3개월 열이 없어 자동으로 `연간 - Q3 누적`이 된다.
- **현금흐름표는 기간과 무관하게 항상 누적**이므로 배당·자사주는 언제나 차감이 필요하다.
- 금액 단위는 문서의 `(단위: 백만원)` 표기를 `parse_unit_scale()`로 읽어 원으로 환산한다. **정규식은 `[가-힣]*원`이어야 한다**(`+`로 두면 `단위:원`처럼 접두어 없는 단독 표기를 놓치고, 단위를 못 찾았을 때 기본값을 추측해 곱하면 10⁶배 틀린 값이 조용히 저장된다 — 실제로 이 버그로 키움증권 영업이익이 100만 배 부풀려진 적이 있다). 단위를 못 찾으면 그 문서는 쓰지 않는다(`None`).
- **계정명 표기가 시기·기업·계정과목마다 갈린다.** 완전일치(`.isin([...])`)로 잡지 말고 `.str.contains()`로 잡을 것 — 번호 접두어(`Ⅳ.영업이익`, `III.영업손익`)가 붙으면 완전일치가 놓친다. 영업이익은 `영업이익|영업손실|영업손익` 세 표기가 다 쓰이고(`account_id`도 `ifrs-full_OperatingProfitLoss`/`ifrs-full_ProfitLossFromOperatingActivities`/`dart_OperatingIncomeLoss` 세 가지), `반영전`(충당금 반영 전 잠정치)·`신용`(신용손실충당금반영전영업이익)·**`기타`**(`기타영업손익` 같은 소계 아닌 하위 세부항목)는 제외해야 진짜 총계 행만 잡힌다. 지배주주 순이익은 `지배기업주주지분순이익`(2024)/`지배기업소유주지분순이익`(2023) → `지배`+`순이익`(`비지배` 제외)로 잡고, 총 순이익은 `기순이익`으로 잡되 `차감전`(세전)과 `귀속`(소제목)을 뺀다. **API 경로(`sfs_utils.extract_is_value`)와 원문 경로(`sfs_dart_html.parse_bs_is`)는 서로 독립된 코드라 같은 계정과목 버그를 양쪽에 따로 고쳐야 한다.**

## 여러 파일을 읽어야 알 수 있는 규칙

- **분기 좌표계**: `get_latest_available_quarter()`가 리밸런싱 날짜에서 최신 분기 `(base_y, base_q)`를 정한다. 이후 모든 모듈이 `get_ttm_coords()`(T-1~T-4), `get_dynamic_quarters()`(cur_qs=T~T-3, prev_qs=T-1~T-4)로 좌표를 만든다. 연·분기를 직접 계산하지 말고 이 헬퍼를 쓸 것.
- **섹터 이분법**: DART 업종코드가 64/65/66으로 시작하면 `금융기업`. 수집 키 세트, 지표 구성(금융은 발생액·FCF 대신 ROE, PCR 없음), 가중치가 모두 갈린다. Z-스코어도 섹터 마스크별로 따로 계산한다.
- **우선주 처리**: `get_common_ticker()`로 본주 코드에 매핑한다. 퀄리티는 본주끼리 계산한 뒤 우선주에 값을 복사하고, 밸류는 우선주가 본주와 직접 경쟁한다(가격·주식수가 다르므로).
- **주주환원금 = 배당 + 자기주식 취득**. DB에는 `dividend`/`buyback`을 **나눠서 저장**하고 쓰는 쪽(step3 quality/value)에서 합산한다. 규칙 세 가지:
  1. **개별(별도)재무제표 기준**(`fs_div='OFS'`). 연결은 종속회사 배당이 섞여 과대계상된다(삼성전자 2024 반기: 연결 5.98조 vs 별도 4.90조).
  2. 신종자본증권 배당과 비지배지분 배당은 상장사 주주 몫이 아니므로 **제외**한다.
  3. 표준계정코드를 1순위로 쓰고(자사주는 `PurchaseOfTreasuryShares` / `PaymentsToAcquireOrRedeemEntitysShares` / `PaymentsForSharesRepurchased` 세 가지), 없을 때만 계정명으로 찾는다. 표기가 `배당금의 지급`(신한)/`배당금의지급`(삼성)/`보통주 배당 지급`(KB)으로 갈리기 때문이다. 매칭 행은 **전부 합산**하고 부호는 양수로 저장한다(소비부가 양수를 전제).
- **센티널 값**: PER/PBR의 `9999.0`은 적자 또는 결측을 뜻한다. 모멘텀 Z-스코어 결측은 `-0.5` 페널티로 채운다.

## 주의할 부작용

- `sfs_step2_data_builder`를 임포트하면 **전역 `requests.get`이 단일 세션의 get으로 교체된다**(소켓 폭주 방지). 임포트하는 순간 프로세스 전체의 HTTP GET 동작이 바뀐다.
- DART에 요청마다 새 연결을 열면 Windows에서 포트가 고갈된다(`WinError 10048`, TIME_WAIT 소진). `OpenDartReader.list`가 세션을 안 쓰므로, 원문을 많이 긁는 스크립트는 위처럼 `requests.get`을 세션에 물리거나 `sfs_dart_html.SESSION`을 쓸 것. 한 번 고갈되면 자연 회복까지 몇 분 걸린다.
- API 키가 비면 DART가 JSON 대신 HTML 안내 페이지를 돌려주고, OpenDartReader가 이를 `AttributeError: 'NoneType' object has no attribute 'text'`로 터뜨린다. 일일 호출 한도를 넘기면 `{'status': '020', 'message': '사용한도를 초과하였습니다'}`가 온다. `python-dotenv`의 `load_dotenv()`는 cwd가 아니라 **호출한 파일 위치**에서 `.env`를 찾으므로, 프로젝트 밖 스크립트는 경로를 명시해야 한다.
- **`sfs_backtest.py`/`sfs_step1_universe.py`는 출력을 UTF-8로 강제하지 않는다** (`check_finance_data.py`/`sfs_dart_html.py`는 `sys.stdout`을 감싸서 강제함). 대화형 콘솔이 아닌 곳(백그라운드 실행, 파일 리다이렉트)에서 돌리면 이모지(🚀 등)에서 `UnicodeEncodeError: 'cp949' codec can't encode`로 죽는다. `PYTHONIOENCODING=utf-8` 환경변수를 주고 실행할 것.
- `sfs_trades_log.csv`가 엑셀 등 다른 프로그램에 열려 있으면 백테스트 마지막 단계(`to_csv`)에서 `PermissionError`로 죽는다. 이때는 이미 모든 계산이 끝난 뒤라 `raw_finance_data.json`은 정상 저장돼 있다 — 파일을 닫고 재실행하면 캐시 덕분에 빠르게 다시 끝까지 간다.
- `sfs_krx_api.stock`은 pykrx 인터페이스를 흉내 낸 KRX Open API 어댑터 싱글톤이다. pykrx가 설치돼 있어도 실제로는 이 어댑터를 쓴다.
- `sample/`에는 옛 프로토타입 사본이 있다. 현재 파이프라인과 무관하고 규칙도 낡았으니 참고하지 말 것.
- 생성물(`*.json`, `*.csv`, `*.png`)과 캐시 디렉터리는 `.gitignore` 대상이다. DB에 영향을 주는 코드를 고칠 때는 `raw_finance_data.json`을 백업(`scratch/`에 타임스탬프 붙여 복사)한 뒤 작업할 것 — 오염된 값을 지우고 재수집하는 방식으로 여러 번 복구한 전례가 있다.
