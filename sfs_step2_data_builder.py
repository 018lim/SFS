# 파일명: sfs_step2_data_builder.py
import os
import time
import pandas as pd
from tqdm import tqdm

# 🔥 [궁극의 해결책] OpenDartReader 소켓 폭주 원천 차단 및 안정성 강화
# ⚠️ 주의: requests.get을 전역적으로 재할당하여 단일 세션과 자동 재시도(Retry)를 공유합니다.
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

sfs_session = requests.Session()
retry_strategy = Retry(
    total=5,  # 최대 5번 재시도
    backoff_factor=1,  # 1초, 2초, 4초, 8초... 대기
    status_forcelist=[429, 500, 502, 503, 504], # 상태 코드에 따른 재시도
    allowed_methods=["HEAD", "GET", "OPTIONS"]
)
adapter = HTTPAdapter(max_retries=retry_strategy)
sfs_session.mount("https://", adapter)
sfs_session.mount("http://", adapter)

# DART 서버가 연결을 끊는 것을 방지하기 위해 User-Agent 추가
sfs_session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})

requests.get = sfs_session.get
# -----------------------------------------------------------------------

from sfs_krx_api import stock
from sfs_config import DB_FILE_PATH
from sfs_utils import (
    get_common_ticker, get_category, get_last_business_day,
    get_stock_snapshot, get_discrete_is, get_discrete_cf, get_cf_ytd,
    clear_finstate_cache, get_latest_available_quarter,
    load_local_db, save_local_db
)
import sfs_dart_html as dart_html

def get_required_quarters(base_y, base_q, total_depth=12):
    """기준 분기로부터 과거 N개 분기의 좌표(연도, 분기) 리스트를 오래된 순으로 생성.

    가장 오래된 해는 1분기까지 확장한다. 4분기 손익 자체는 '연간 - Q3누적'으로 구해
    앞 분기에 의존하지 않지만(get_discrete_is), 현금흐름표 항목(배당/자사주 등)은
    보고서마다 항상 누적이라 중간 분기가 API 공백이면 뒤 분기까지 못 구하게 된다 -
    그 해 1분기부터 받아둬야 discrete_cf가 이미 저장된 앞 분기 값으로 대신 구할 수 있다.
    오래된 순으로 도는 이유도 같다 - 뒤 분기를 계산할 때 앞 분기가 이미 채워져 있어야 한다.
    """
    base_abs_q = base_y * 4 + base_q
    coords = []
    for i in range(total_depth):
        target_abs = base_abs_q - i
        y = (target_abs - 1) // 4
        q = ((target_abs - 1) % 4) + 1
        coords.append((y, q))

    oldest_y, oldest_q = coords[-1]
    coords += [(oldest_y, qq) for qq in range(oldest_q - 1, 0, -1)]
    return list(reversed(coords))

def quarter_depth(base_y, base_q, y, q):
    """기준 분기로부터 몇 분기 과거인가 (0 = 기준 분기)"""
    return (base_y * 4 + base_q) - (y * 4 + q)

def tier1_start_year(base_y, base_q, tier1_depth=5):
    """Tier-1이 걸치는 가장 오래된 해. 그 해는 1분기부터 Tier-1로 수집한다.

    수집 범위를 연 단위로 맞추는 것과 같은 원칙을 Tier 경계에도 적용한 것으로,
    Tier-1이 어느 해에 걸치면 그 해 전체를 같은 키 세트로 채워 데이터가 반쪽으로
    남지 않게 한다. (4분기 손익 자체는 '연간 - Q3누적'으로 구하므로 앞 분기 저장값에
    의존하지 않는다 - sfs_utils.get_discrete_is 참고.)
    """
    oldest_abs = base_y * 4 + base_q - (tier1_depth - 1)
    return (oldest_abs - 1) // 4

def store(data, key, value):
    """None(= 구할 수 없음)은 저장하지 않고, 기존 키가 있으면 지운다.

    None을 그대로 넣으면 소비부가 더하다 TypeError로 터지고, 0으로 채우면 조용히 틀린다.
    키를 비워두면 다음 실행에서 missing_keys가 잡아 다시 시도한다.
    """
    if value is None:
        data.pop(key, None)
    else:
        data[key] = value

def discrete_cf(dart, ticker, y, q, key, db_ticker):
    """분기 단독 현금흐름 항목(ocf/capex/dividend/buyback).

    get_discrete_cf는 항상 'YTD(q) - YTD(q-1)'을 API로 직접 구하는데, 현금흐름표는
    보고서마다 항상 누적이라 중간 분기 하나가 API 공백이면(금융업 2023Q1~Q2 등) 그
    분기의 YTD를 못 구해 뒤 분기까지 전부 None이 된다 - 정작 그 뒤 분기 자체는
    API가 멀쩡히 주는데도 그렇다(KB금융 2023Q3: op은 API로 정상인데 dividend만
    Q2 YTD 공백에 끌려 None이 됨).

    Tier-1은 그 해 1분기부터 받으므로(tier1_start_year), q보다 앞 분기의 discrete
    값은 API든 공시원문 보정이든 이미 db_ticker에 들어있다. 직접 차감이 실패하면
    그 저장값들의 합을 앞 분기 누적 대신 써서 이 분기의 YTD에서 뺀다.
    """
    v = get_discrete_cf(dart, ticker, y, q, key)
    if v is not None or q == 1:
        return v
    prior = [db_ticker.get(str(y), {}).get(str(qq), {}).get(key) for qq in range(1, q)]
    if any(p is None for p in prior):
        return None
    ytd_cur = get_cf_ytd(dart, ticker, y, q, key)
    if ytd_cur is None:
        return None
    return ytd_cur - sum(prior)

BS_KEYS = ('assets', 'liabilities', 'equity')
# 공시 원문(HTML)에서 가져올 수 있는 항목. shares는 KRX 소관이라 빠진다.
HTML_KEYS = ('assets', 'liabilities', 'equity', 'op', 'ni', 'dividend', 'buyback')

def is_bs_empty(q_data):
    """자산·부채·자본 세 값이 모두 0이면 데이터 공백으로 본다.

    finstate_all은 금융업의 2023Q3 이전 분기를 아예 조회하지 못해 이 세 값이 0으로 남는다.
    상장사의 자산·부채·자본이 동시에 0일 수는 없으므로 공백 판정에 쓸 수 있다.
    (op/ni/return은 배당 없는 분기처럼 실제로 0인 경우가 있어 판정에 쓰지 않는다.)

    세 키가 아예 없으면 False - 모멘텀 전용(op/shares)으로만 수집한 과거 분기이지
    수집에 실패한 분기가 아니다.
    """
    if not all(k in q_data for k in BS_KEYS): return False
    return all(float(q_data.get(k) or 0) == 0 for k in BS_KEYS)

def is_api_gap(q_data):
    """API가 이 분기를 통째로 못 준 것으로 보이는가.

    영업이익이 0인지로 판정한다. op는 Tier-1(퀄리티/밸류)과 Tier-2(모멘텀) 어느 쪽에서든
    수집 대상이라 모든 분기에 쓸 수 있고, 상장 금융기업의 영업이익이 정확히 0인 분기는
    사실상 없다. 단 'op 키가 있는데 값이 0'일 때만 참이다 - 아직 받아본 적 없는 분기는
    먼저 API로 시도해야 한다.
    """
    return 'op' in q_data and float(q_data.get('op') or 0) == 0

def build_sfs_finance_db(dart_api, target_tickers, rebalance_date):
    """
    [SFS 2단계] 로컬 JSON DB를 확인하고, 부족한 데이터만 DART API로 핀셋 업데이트하는 모듈
    """
    base_y, base_q = get_latest_available_quarter(rebalance_date)
    print(f"\n🚀 [SFS 엔진] {rebalance_date} 기준 재무 데이터 동기화 가동")
    print(f" 🔹 타겟 최신 분기: {base_y}년 {base_q}분기 (공시 마감 룰 적용)")
    
    # 1. 기존 로컬 DB 로드
    sfs_db = load_local_db(DB_FILE_PATH)
    print(f" 🔹 로컬 DB 로드 완료: {DB_FILE_PATH}")
    
    total_depth = 12
    required_coords = get_required_quarters(base_y, base_q, total_depth)
    t1_year = tier1_start_year(base_y, base_q)   # 이 해부터는 1분기까지 Tier-1로 수집
    is_updated = False 
    
    # 💡 [핵심] 타겟 유니버스에 우선주가 섞여 있어도 본주로 통일하여 중복 제거 (Set 활용)
    common_tickers = list(set(get_common_ticker(tk) for tk in target_tickers))
    
    # tqdm을 적용하여 깔끔한 진행 상태 확인
    for actual_tk in tqdm(common_tickers, desc="데이터 동기화 진행률"):
        # 🛡️ [에러 방어 블록 시작] 특정 종목에서 에러가 발생해도 전체가 멈추지 않음
        try:
            name = stock.get_market_ticker_name(actual_tk)
            html_reports = None   # 공시 원문 폴백이 필요할 때만 종목당 1회 조회

            # 신규 종목 추가 처리
            if actual_tk not in sfs_db:
                sfs_db[actual_tk] = {}
                sfs_db[actual_tk]["category"] = get_category(dart_api, actual_tk)
                time.sleep(0.1)
                is_updated = True
                
            # tqdm 내부에서는 print 대신 tqdm.write를 써야 출력이 깨지지 않습니다.
            tqdm.write(f"\n[{actual_tk} / {name}] 데이터 무결성 검사 중...")
            
            for y, q in required_coords:
                str_y = str(y)
                str_q = str(q)
                # 오래된 순으로 돌기 때문에 순번 대신 기준 분기와의 거리로 Tier를 정한다
                depth = quarter_depth(base_y, base_q, y, q)
                
                # Key 껍데기 세팅
                if str_y not in sfs_db[actual_tk]:
                    sfs_db[actual_tk][str_y] = {}
                if str_q not in sfs_db[actual_tk][str_y]:
                    sfs_db[actual_tk][str_y][str_q] = {}
                    
                current_data = sfs_db[actual_tk][str_y][str_q]
                q_data = sfs_db[actual_tk][str_y].get(str_q)
                category = sfs_db[actual_tk].get("category", "일반기업")
                
                # [Tier 1] 최근 5분기 : 퀄리티/밸류 풀세트
                # Tier-1이 걸치는 해는 1분기까지 Tier-1로 올린다 (tier1_start_year 참고).
                # 현금흐름표 항목은 discrete_cf가 앞 분기 저장값에 의존하므로 그 해 1분기부터
                # 있어야 한다.
                if y >= t1_year:
                    if category == "금융기업":
                        required_keys = ['assets', 'liabilities', 'equity', 'ni', 'dividend', 'buyback', 'op', 'shares']
                    else:
                        required_keys = ['assets', 'liabilities', 'equity', 'ni', 'ocf', 'capex', 'dividend', 'buyback', 'op', 'shares']
                # [Tier 2] 과거 7분기 : 모멘텀 전용
                # shares는 밸류 지표(PER/PBR/PCR/주주수익률)용이라 과거 분기에는 필요 없다
                elif depth < total_depth:
                    required_keys = ['op']
                # [보조] 가장 오래된 해의 앞 분기 : 그 해 4분기 손익을 구하기 위한 값만
                else:
                    required_keys = ['op', 'ni']
                    
                if q_data is None:
                    missing_keys = required_keys
                else:
                    # 💡 값이 0이어도 데이터가 있는 것으로 간주하여 불필요한 반복 다운로드를 방지합니다.
                    missing_keys = [
                        k for k in required_keys
                        if k not in q_data
                        or q_data.get(k) is None
                    ]

                # 💡 [구할 수 없는 분기] 상장 전이라 보고서가 아예 없는 분기는 매 실행마다
                #    전체 다운로드를 다시 시도하게 된다(크래프톤 실측: 2회차 finstate_all 30건).
                #    한 번 확인한 부재는 src='na'로 남겨 건너뛴다. 단 최신 2분기는 공시가
                #    뒤늦게 올라오므로 마커를 무시하고 매번 재확인한다.
                if missing_keys and (q_data or {}).get('src') == 'na' and depth >= 2:
                    continue

                # 💡 [HTML 경로 판정] finstate_all이 2023Q3 이전 금융업을 통째로 못 주는 구간.
                #    한 번 공시 원문에서 가져온 분기는 API에 애초에 데이터가 없는 분기이므로
                #    이후에도 그 분기의 모든 값을 원문에서 가져온다 (src 플래그로 기억).
                #    0으로 저장된 값은 missing_keys에 안 잡히므로 is_api_gap으로 따로 판정한다.
                already_html = (q_data or {}).get('src') == 'html'
                use_html = category == "금융기업" and (already_html or is_api_gap(q_data or {}))

                # 채울 게 없으면 즉시 통과 (속도 극대화).
                # 단 'op==0인데 아직 원문 보정을 못 받은' 분기(use_html True, src!='html')는
                # 건너뛰면 안 된다. missing_keys는 op가 저장돼 있으면(설령 0이어도) 비게 되므로,
                # 여기서 걸러내지 않으면 그 0이 영원히 고쳐지지 않는다(카카오뱅크 323410 실측:
                # 2023Q3~2025Q2 8분기 연속 op=0/src=api로 방치됨 - use_html을 조건에서 뺐던 회귀).
                # 이미 src='html'로 확정된 분기만 안전하게 재수집을 건너뛴다(KB금융 실측:
                # 2회차에 dart.list 1 + sub_docs 12 + 문서 24건이 헛돎 - 이 최적화는 유지).
                if not missing_keys and (already_html or not use_html):
                    continue

                # (조기 종료 방어 로직 제거됨 - 무조건 끝까지 탐색하여 최대한 데이터 확보)

                if missing_keys:
                    tqdm.write(f"  ➔ {y}년 {q}분기 누락 데이터 다운로드: {missing_keys}")

                # 핀셋 다운로드 로직 (이제 API가 아니라 메모리 캐시에서 1초 만에 가져옵니다!)
                # 원문 대상 분기는 API를 찔러야 빈 응답만 오므로 건너뛴다.
                # 구할 수 없는 값(보고서 없음 등)은 store()가 저장하지 않고 키를 비워둔다
                if not use_html:
                    if 'assets' in missing_keys: store(current_data, 'assets', get_stock_snapshot(dart_api, actual_tk, y, q, 'assets'))
                    if 'liabilities' in missing_keys: store(current_data, 'liabilities', get_stock_snapshot(dart_api, actual_tk, y, q, 'liabilities'))
                    if 'equity' in missing_keys: store(current_data, 'equity', get_stock_snapshot(dart_api, actual_tk, y, q, 'equity'))

                    if 'ni' in missing_keys: store(current_data, 'ni', get_discrete_is(dart_api, actual_tk, y, q, 'ni'))
                    if 'op' in missing_keys: store(current_data, 'op', get_discrete_is(dart_api, actual_tk, y, q, 'op'))

                    if 'ocf' in missing_keys: store(current_data, 'ocf', discrete_cf(dart_api, actual_tk, y, q, 'ocf', sfs_db[actual_tk]))
                    if 'capex' in missing_keys: store(current_data, 'capex', discrete_cf(dart_api, actual_tk, y, q, 'capex', sfs_db[actual_tk]))
                    # 주주환원금은 배당/자사주를 나눠 저장하고, 쓰는 쪽에서 합산한다
                    if 'dividend' in missing_keys: store(current_data, 'dividend', discrete_cf(dart_api, actual_tk, y, q, 'dividend', sfs_db[actual_tk]))
                    if 'buyback' in missing_keys: store(current_data, 'buyback', discrete_cf(dart_api, actual_tk, y, q, 'buyback', sfs_db[actual_tk]))

                    # API로 받고도 영업이익이 없거나 0이면 이 분기는 원문 대상이다.
                    # store()가 구할 수 없는 값을 저장하지 않으므로 '키 없음'도 공백 신호다
                    # (여기는 이미 다운로드를 시도한 뒤라 '아직 안 받아봄'과 구분된다).
                    op_now = current_data.get('op')
                    use_html = category == "금융기업" and (op_now is None or float(op_now) == 0)

                # shares는 KRX에서 받는다 (공시 원문 파싱 대상이 아님)

                if 'shares' in missing_keys:
                    month_map = {1: 3, 2: 6, 3: 9, 4: 12}
                    target_date_raw = get_last_business_day(y, month_map[q])
                    target_date = stock.get_nearest_business_day(target_date_raw)
                    try:
                        hist_cap = stock.get_market_cap(target_date)
                        prefix = actual_tk[:-1]
                        matching_tks = [t for t in hist_cap.index if t.startswith(prefix)]
                        current_data['shares'] = sum(float(hist_cap.loc[t, '상장주식수']) for t in matching_tks)
                    except Exception:
                        current_data['shares'] = 0.0

                if missing_keys:
                    is_updated = True

                # 💡 [HTML 경로] 이 분기는 API에 데이터가 없으므로 shares를 뺀 전 항목을 원문에서 가져온다
                if use_html:
                    if html_reports is None:
                        html_reports = dart_html.load_reports(
                            dart_api, actual_tk, min(yy for yy, _ in required_coords), base_y)
                    html_vals = dart_html.get_html_quarter_values(
                        dart_api, actual_tk, y, q, html_reports)
                    if html_vals and not is_bs_empty(html_vals):
                        filled = [k for k in HTML_KEYS if html_vals.get(k) is not None]
                        for k in filled:
                            current_data[k] = html_vals[k]
                        current_data['src'] = 'html'   # 이 분기는 계속 원문에서 가져온다
                        is_updated = True
                        tqdm.write(f"  ➔ {y}년 {q}분기 공시 원문(HTML)에서 수집: {filled}")
                    else:
                        tqdm.write(f"  ⚠️ {y}년 {q}분기 공시 원문에서도 재무제표를 찾지 못했습니다.")

                # 💡 [부재 기록] API도 원문도 아무것도 못 준 분기는 'na'로 남겨 다음 실행에서
                #    건너뛴다. 상장 전이라 보고서가 애초에 없는 구간이 대부분이다.
                #    최신 2분기는 위 판정에서 마커를 무시하므로 뒤늦은 공시도 반영된다.
                if missing_keys and not any(k in current_data for k in required_keys if k != 'shares'):
                    current_data['src'] = 'na'
                    is_updated = True

                # 속도가 비약적으로 빨라졌으므로 딜레이를 0.1초로 줄임
                time.sleep(0.1)

            # 💡 [주식수 후처리] shares가 0인 경우 앞뒤 분기 데이터로 채우기 (Forward & Backward Fill)
            if actual_tk in sfs_db:
                # 연도와 분기를 시계열 순서로 정렬 (과거 -> 최신)
                quarters_data = []
                for y_str, q_dict in sfs_db[actual_tk].items():
                    if not isinstance(q_dict, dict):
                        continue
                    for q_str, q_data in q_dict.items():
                        if not isinstance(q_data, dict):
                            continue
                        quarters_data.append((int(y_str), int(q_str), q_data))
                quarters_data.sort(key=lambda x: (x[0], x[1]))
                
                # shares를 수집하지 않는 과거 분기(모멘텀 전용)는 채우기 대상에서 뺀다.
                # 키가 없는 분기까지 채우면 그 시점과 무관한 최근 주식수가 들어간다.
                quarters_data = [t for t in quarters_data if 'shares' in t[2]]

                # 1. Forward Fill (과거 -> 최신) - 전 분기 값을 가져옴
                last_valid_shares = 0.0
                for y_int, q_int, q_data in quarters_data:
                    current_shares = float(q_data.get('shares', 0.0))
                    if current_shares > 0.0:
                        last_valid_shares = current_shares
                    elif last_valid_shares > 0.0:
                        q_data['shares'] = last_valid_shares
                        is_updated = True
                        
                # 2. Backward Fill (최신 -> 과거) - 혹시 가장 과거 분기가 0이었다면 최근 분기 값으로 채움
                last_valid_shares = 0.0
                for y_int, q_int, q_data in reversed(quarters_data):
                    current_shares = float(q_data.get('shares', 0.0))
                    if current_shares > 0.0:
                        last_valid_shares = current_shares
                    elif last_valid_shares > 0.0:
                        q_data['shares'] = last_valid_shares
                        is_updated = True

            # 💡 [메모리 최적화] 해당 기업의 12분기 순회가 끝나면 캐시(RAM)를 깔끔하게 비움
            clear_finstate_cache()
            dart_html.clear_html_cache()

        # 🛡️ [에러 방어 1] DART 미등록 기업 (0126Z0 등) 처리
        except ValueError as e:
            if 'could not find' in str(e):
                tqdm.write(f" ⚠️ [{actual_tk}] DART 공시 미등록 종목 ➔ 자동 패스")
            else:
                tqdm.write(f" 🚨 [{actual_tk}] 데이터 에러: {e}")
            clear_finstate_cache()
            dart_html.clear_html_cache()
            continue
            
        # 🛡️ [에러 방어 2] 기타 알 수 없는 에러 처리
        except Exception as e:
            tqdm.write(f" 🚨 [{actual_tk}] 알 수 없는 예외 발생: {e}")
            clear_finstate_cache()
            dart_html.clear_html_cache()
            continue

    # 2. 업데이트된 내역이 있다면 로컬 DB 덮어쓰기
    if is_updated:
        save_local_db(sfs_db, DB_FILE_PATH)
        print(f"\n✅ [동기화 완료] 누락된 데이터를 보완하여 {DB_FILE_PATH} 에 저장했습니다.")
    else:
        print(f"\n✅ [동기화 완료] 로컬 DB가 최신 상태입니다. (API 호출 0회)")
        
    return sfs_db, base_y, base_q

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    import OpenDartReader

    dart = OpenDartReader(os.getenv("DART_API_KEY"))
    sample_tickers = ['005930', '005935', '105560', '0126Z0']  # 에러 유발 종목(0126Z0) 포함 테스트
    test_date = '20260826'

    finance_db, b_y, b_q = build_sfs_finance_db(dart, sample_tickers, test_date)