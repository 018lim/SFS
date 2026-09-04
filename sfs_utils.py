# 파일명: sfs_utils.py
import os
import json
import calendar
import pandas as pd
from sfs_config import DB_FILE_PATH
from sfs_krx_api import stock

# ==========================================
# 💡 [핵심 최적화] 문서 중심 추출을 위한 전역 캐시
# ==========================================
_DOC_CACHE = {}

# 💡 [음수 캐시] "이 보고서는 DART에 없다"는 사실만 담는다. 표 데이터를 들고 있지 않아
#    메모리 부담이 없으므로 종목이 바뀌어도 지우지 않는다. 백테스트는 리밸런싱 날짜마다
#    전 종목을 다시 순회하는데, 상장 전이나 금융업 공백 구간처럼 영원히 없는 보고서를
#    날짜마다 다시 묻던 것을 막는다.
_EMPTY_DOCS = set()

def clear_finstate_cache():
    """종목 1개의 처리가 끝날 때마다 호출하여 메모리(RAM) 폭발을 방지합니다.

    부재 사실(_EMPTY_DOCS)은 유지한다 - 없는 보고서는 종목을 바꿔 돌아와도 여전히 없다.
    """
    _DOC_CACHE.clear()

def get_finstate_safe(dart, ticker, year, reprt_code, fs_div='CFS'):
    """
    [문서 중심 & 에러 방어 호출]
    1. 해당 분기 재무제표를 1번만 받아 캐시에 저장하고 재사용 (API 호출량 80% 감소)
    2. 요청한 재무제표가 없으면 반대쪽(CFS<->OFS)으로 자동 재시도 (013 에러 원천 차단)
    3. DART 미등록 종목일 경우 깔끔하게 예외를 던져 프로그램 크래시 방지
    fs_div: 'CFS'(연결) 기본, 주주환원(배당/자사주)은 'OFS'(개별)로 호출한다.
    """
    cache_key = f"{ticker}_{year}_{reprt_code}_{fs_div}"

    # 캐시에 이미 해당 문서가 있으면 API를 찌르지 않고 즉시 반환
    if cache_key in _DOC_CACHE:
        return _DOC_CACHE[cache_key]

    # 없다고 이미 확인한 보고서는 다시 묻지 않는다
    doc_id = (ticker, year, reprt_code)
    if doc_id in _EMPTY_DOCS:
        return None

    try:
        df = dart.finstate_all(ticker, year, reprt_code, fs_div)
        # 요청한 재무제표가 비어있으면 반대쪽으로 재시도
        if df is None or df.empty:
            df = dart.finstate_all(ticker, year, reprt_code, 'OFS' if fs_div == 'CFS' else 'CFS')
            # 양쪽 다 비면 그 보고서는 존재하지 않는다 (CFS/OFS 구분 없이 기억한다 -
            # 그래야 CFS 요청과 OFS 요청이 같은 부재를 두 번 확인하지 않는다)
            if df is None or df.empty:
                _EMPTY_DOCS.add(doc_id)

    except ValueError as e:
        # "0126Z0" 등 DART에 없는 종목일 경우 바깥 루프로 에러를 던져 패스시킴
        if 'could not find' in str(e):
            raise e
        df = None
    except Exception:
        df = None

    # 가져온 표(문서)를 캐시에 저장
    _DOC_CACHE[cache_key] = df
    return df

# ==========================================
# 이하 기존 유틸리티 함수들
# ==========================================
def get_common_ticker(ticker):
    """우선주를 본주 코드로 맵핑"""
    return ticker[:-1] + '0' if ticker[-1] != '0' else ticker

def get_category(dart, actual_ticker):
    """일반기업/금융기업 분류"""
    try:
        induty_code = str(dart.company(actual_ticker).get('induty_code', ''))
        if induty_code.startswith(('64', '65', '66')):
            return "금융기업"
    except Exception:
        pass
    return "일반기업"

def get_clean_value(val_str):
    """(괄호) 형태의 음수를 정상 처리하고 float 변환"""
    if pd.isna(val_str) or str(val_str).strip() == '': return 0.0
    val_str = str(val_str).replace(',', '').strip()
    if val_str.startswith('(') and val_str.endswith(')'): return -float(val_str[1:-1])
    try: return float(val_str)
    except Exception: return 0.0

def get_last_business_day(year, month):
    """특정 연월의 마지막 영업일(거래일)을 반환"""
    last_day = calendar.monthrange(year, month)[1]
    end_date = f"{year}{month:02d}{last_day}"
    try:
        b_days = stock.get_previous_business_days(year=int(year), month=int(month))
        if b_days: return b_days[-1].strftime("%Y%m%d")
    except Exception: pass
    return end_date

# ==========================================
# 분기 좌표 및 DB 관련 유틸리티
# ==========================================
def get_latest_available_quarter(rebalance_date_str: str):
    """리밸런싱 당일 기준 직전 분기가 발표되었다고 단순 가정"""
    dt = pd.to_datetime(rebalance_date_str)
    y, m = dt.year, dt.month
    
    if 4 <= m <= 5: 
        return y - 1, 4
    elif 6 <= m <= 8: 
        return y, 1
    elif 9 <= m <= 11: 
        return y, 2
    else: 
        if m <= 3:
            return y - 1, 3
        else:
            return y, 3

def get_ttm_coords(base_y, base_q):
    """현재 분기 기준 최근 4개 분기(TTM) 좌표를 문자열로 반환 (T-1 ~ T-4)"""
    base_abs = base_y * 4 + base_q
    return [(str((base_abs - i - 1) // 4), str(((base_abs - i - 1) % 4) + 1)) for i in range(4)]

def get_dynamic_quarters(base_y, base_q):
    """
    현재 분기(T)를 기준으로 TTM 및 과거 비교 분기 키(Key)를 동적으로 생성합니다.
    - cur_qs: T, T-1, T-2, T-3
    - prev_qs: T-1, T-2, T-3, T-4
    """
    base_abs = base_y * 4 + base_q
    
    def abs_to_str(abs_q):
        return str((abs_q - 1) // 4), str(((abs_q - 1) % 4) + 1)
        
    cur_qs = [abs_to_str(base_abs - i) for i in range(4)]
    prev_qs = [abs_to_str(base_abs - i) for i in range(1, 5)]
    
    curr_key = cur_qs[0]
    prev_key = prev_qs[-1]
    
    return cur_qs, prev_qs, curr_key, prev_key

def load_local_db(file_path=None, must_exist=False):
    """로컬 JSON DB를 읽어옵니다. must_exist=True이면 파일 부재 시 에러를 발생시킵니다."""
    if file_path is None:
        file_path = DB_FILE_PATH
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    if must_exist:
        raise FileNotFoundError(f"🚨 {file_path} 파일이 없습니다. 2단계를 먼저 실행하세요.")
    return {}

def save_local_db(db_data, file_path=None):
    """업데이트된 데이터를 로컬 JSON 파일로 덮어씁니다."""
    if file_path is None:
        file_path = DB_FILE_PATH
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(db_data, f, indent=4, ensure_ascii=False)

# ==========================================
# 💡 하드코딩된 dart.finstate_all 대신 get_finstate_safe 사용
# ==========================================
def get_stock_snapshot(dart, ticker, year, q, acc_type):
    """재무상태표 스냅샷 추출 (Stock). 보고서 자체가 없으면 None."""
    report_map = {1: '11013', 2: '11012', 3: '11014', 4: '11011'}

    # 💡 최적화된 안전 호출 함수 사용
    df = get_finstate_safe(dart, ticker, year, report_map[q])
    # 보고서가 없는 것(상장 전 등)과 값이 0인 것은 다르다. 0을 돌려주면 그 0이
    # 4분기 계산('연간 - Q1~Q3')에 그대로 들어가 없는 분기 실적이 4분기에 얹힌다.
    if df is None or df.empty: return None

    df['clean_acc'] = df['account_nm'].astype(str).str.replace(' ', '')
    df['clean_sj'] = df['sj_nm'].astype(str).str.replace(' ', '')
    
    tgt = pd.DataFrame()
    if acc_type == 'assets':
        if 'account_id' in df.columns: tgt = df[df['account_id'] == 'ifrs-full_Assets']
        if tgt.empty: tgt = df[(df['clean_sj'].str.contains('재무상태')) & (df['clean_acc'].str.contains('자산총계|자산합계'))]
    elif acc_type == 'liabilities':
        if 'account_id' in df.columns: tgt = df[df['account_id'] == 'ifrs-full_Liabilities']
        if tgt.empty: tgt = df[(df['clean_sj'].str.contains('재무상태')) & (df['clean_acc'].str.contains('부채총계|부채합계'))]
    elif acc_type == 'equity':
        if 'account_id' in df.columns: tgt = df[df['account_id'] == 'ifrs-full_EquityAttributableToOwnersOfParent']
        if tgt.empty: tgt = df[(df['clean_sj'].str.contains('재무상태')) & (df['clean_acc'].str.contains('지배기업|지배주주') & df['clean_acc'].str.contains('자본|지분') & ~df['clean_acc'].str.contains('비지배'))]
        if tgt.empty: tgt = df[(df['clean_sj'].str.contains('재무상태')) & (df['clean_acc'].str.contains('자본총계|기말자본'))]
        
    if not tgt.empty: return get_clean_value(tgt.iloc[0]['thstrm_amount'])
    return 0.0

def extract_is_value(df, acc_type, amount_col='thstrm_amount'):
    """손익계산서 실적 추출 (OP, NI). 보고서 자체가 없으면 None.

    amount_col='thstrm_add_amount'를 주면 당기누적(YTD)을 읽는다. 4분기를
    '연간 - Q3누적'으로 구할 때 쓰며, 이러면 앞 분기 보고서가 없어도(연중 상장) 계산된다.
    보고서는 있는데 해당 계정 행만 없는 경우는 0.0을 유지한다(실제로 0인 항목).
    """
    if df is None or df.empty: return None
    df['clean_acc'] = df['account_nm'].astype(str).str.replace(' ', '')
    df['clean_sj'] = df['sj_nm'].astype(str).str.replace(' ', '')
    
    tgt = pd.DataFrame()
    if acc_type == 'op':
        if 'account_id' in df.columns:
            tgt = df[df['account_id'].isin(['ifrs-full_OperatingProfitLoss',
                                            'ifrs-full_ProfitLossFromOperatingActivities',
                                            'dart_OperatingIncomeLoss'])]
        if tgt.empty:
            # 완전일치(isin)는 'Ⅳ.영업이익'처럼 번호가 붙은 표기를 놓친다(카카오뱅크 실측:
            # account_nm='IV. 영업이익' -> 매칭 실패 -> 0.0으로 조용히 빠짐, 실제론 1,275억).
            # '영업손익'(이익/손실 구분 없는 합성 표기, account_id='dart_OperatingIncomeLoss')을
            # 쓰는 기업도 있다(실측: 064350·008770). '반영전'(충당금 반영 전 잠정치)·
            # '신용'(신용손실충당금반영전영업이익)은 계속 제외한다.
            tgt = df[(df['clean_sj'].str.contains('손익|포괄'))
                     & (df['clean_acc'].str.contains('영업이익|영업손실|영업손익'))
                     & (~df['clean_acc'].str.contains('반영전|신용'))]
        # 누적 배제 로직 추가 (모멘텀 무결성)
        if not tgt.empty and 'thstrm_nm' in tgt.columns:
            discrete_tgt = tgt[~tgt['thstrm_nm'].astype(str).str.contains('누적', na=False)]
            if not discrete_tgt.empty: tgt = discrete_tgt

    elif acc_type == 'ni':
        if 'account_id' in df.columns:
            tgt = df[df['account_id'] == 'ifrs-full_ProfitLossAttributableToOwnersOfParent']
            if tgt.empty: tgt = df[df['account_id'] == 'ifrs-full_ProfitLoss']
        if tgt.empty:
            profit_kw = '당기순이익|반기순이익|분기순이익|순이익'
            tgt = df[(df['clean_sj'].str.contains('손익|포괄')) & (df['clean_acc'].str.contains(profit_kw)) & (df['clean_acc'].str.contains('지배')) & (~df['clean_acc'].str.contains('포괄|비지배'))]
            if tgt.empty: tgt = df[(df['clean_sj'].str.contains('손익|포괄')) & (df['clean_acc'].str.contains(profit_kw)) & (~df['clean_acc'].str.contains('포괄|비지배|지분'))]
            
    if not tgt.empty:
        raw = tgt.iloc[0].get(amount_col)
        # 누적 컬럼은 연간보고서 등에서 비어 있다. 빈 값을 0으로 읽으면
        # '연간 - 누적'이 연간 전체가 되어 조용히 틀린다.
        if raw is None or pd.isna(raw) or str(raw).strip() == '': return None
        return get_clean_value(raw)
    return 0.0

def extract_cf_ytd_value(df, acc_type):
    """현금흐름표 누적(YTD) 실적 추출 (ocf, capex, dividend, buyback). 보고서가 없으면 None.

    주주환원금은 배당과 자사주를 따로 저장하고, 쓰는 쪽에서 합산한다.
    보고서는 있는데 해당 계정 행만 없는 경우는 0.0을 유지한다(배당을 안 준 분기 등).
    """
    if df is None or df.empty: return None
    df['clean_acc'] = df['account_nm'].astype(str).str.replace(' ', '')
    df['clean_sj'] = df['sj_nm'].astype(str).str.replace(' ', '')
    df_cf = df[df['clean_sj'].str.contains('현금흐름')]
    if df_cf.empty: return 0.0
    
    if acc_type == 'ocf':
        tgt = df_cf[df_cf['account_id'] == 'ifrs-full_CashFlowsFromUsedInOperatingActivities'] if 'account_id' in df_cf.columns else pd.DataFrame()
        if tgt.empty: tgt = df_cf[df_cf['clean_acc'].str.contains('영업활동') & df_cf['clean_acc'].str.contains('현금흐름')]
        if not tgt.empty: return get_clean_value(tgt.iloc[0]['thstrm_amount'])
        
    elif acc_type == 'capex':
        cpx_p, cpx_i = 0.0, 0.0
        tgt_p = df_cf[df_cf['account_id'] == 'ifrs-full_PurchaseOfPropertyPlantAndEquipment'] if 'account_id' in df_cf.columns else pd.DataFrame()
        if tgt_p.empty: tgt_p = df_cf[df_cf['clean_acc'].str.contains('유형자산') & df_cf['clean_acc'].str.contains('취득|증가|지출') & ~df_cf['clean_acc'].str.contains('처분|감소')]
        if not tgt_p.empty: cpx_p = abs(get_clean_value(tgt_p.iloc[0]['thstrm_amount']))
        
        tgt_i = df_cf[df_cf['account_id'] == 'ifrs-full_PurchaseOfIntangibleAssets'] if 'account_id' in df_cf.columns else pd.DataFrame()
        if tgt_i.empty: tgt_i = df_cf[df_cf['clean_acc'].str.contains('무형자산') & df_cf['clean_acc'].str.contains('취득|증가|지출') & ~df_cf['clean_acc'].str.contains('처분|감소')]
        if not tgt_i.empty: cpx_i = abs(get_clean_value(tgt_i.iloc[0]['thstrm_amount']))
        return cpx_p + cpx_i
        
    elif acc_type == 'dividend':
        # 보통주 배당만. 신종자본증권/비지배지분 배당과 배당금 수취(영업활동 유입)는 제외한다.
        # 표기가 '배당금의 지급'(신한)/'배당금의지급'(삼성)/'보통주 배당 지급'(KB)으로 갈려
        # 표준계정코드를 1순위로 쓰고, 없을 때만 계정명으로 찾는다.
        tgt = df_cf[df_cf['account_id'] == 'ifrs-full_DividendsPaidClassifiedAsFinancingActivities'] if 'account_id' in df_cf.columns else pd.DataFrame()
        if tgt.empty:
            tgt = df_cf[df_cf['clean_acc'].str.contains('배당') & df_cf['clean_acc'].str.contains('지급')
                        & ~df_cf['clean_acc'].str.contains('신종|자본증권|비지배|수취|수입')]
        return sum(abs(get_clean_value(v)) for v in tgt['thstrm_amount']) if not tgt.empty else 0.0

    elif acc_type == 'buyback':
        # 자기주식 취득. 기업마다 표준계정코드가 갈려 세 가지를 모두 본다.
        buyback_ids = ['ifrs-full_PurchaseOfTreasuryShares',
                       'ifrs-full_PaymentsToAcquireOrRedeemEntitysShares',
                       'ifrs-full_PaymentsForSharesRepurchased']
        tgt = df_cf[df_cf['account_id'].isin(buyback_ids)] if 'account_id' in df_cf.columns else pd.DataFrame()
        if tgt.empty:
            tgt = df_cf[df_cf['clean_acc'].str.contains('자기주식') & df_cf['clean_acc'].str.contains('취득|매입')
                        & ~df_cf['clean_acc'].str.contains('처분|소각')]
        return sum(abs(get_clean_value(v)) for v in tgt['thstrm_amount']) if not tgt.empty else 0.0

    return 0.0

def get_discrete_is(dart, ticker, year, q, acc_type):
    """[손익계산서 로직] 1~3분기는 3개월 단독 추출, 4분기는 '연간 - Q3누적'.

    4분기를 '연간 - (Q1+Q2+Q3)'로 구하면 세 분기 보고서가 모두 있어야 하고, 하나라도
    없으면(연중 상장, 금융업 공백) 그 분기 실적이 통째로 4분기에 얹힌다. 3분기보고서의
    당기누적(thstrm_add_amount)을 쓰면 연간·3분기 두 건만으로 정확히 구해진다.
    (크래프톤 2021Q4: 6,396억 - 5,967억 = 429억. Q1이 없어도 계산된다.)
    """
    report_map = {1: '11013', 2: '11012', 3: '11014'}
    if q in report_map:
        # 💡 최적화된 안전 호출 함수 사용
        return extract_is_value(get_finstate_safe(dart, ticker, year, report_map[q]), acc_type)
    elif q == 4:
        ann = extract_is_value(get_finstate_safe(dart, ticker, year, '11011'), acc_type)
        q3_cum = extract_is_value(get_finstate_safe(dart, ticker, year, '11014'),
                                  acc_type, amount_col='thstrm_add_amount')
        if ann is None or q3_cum is None: return None
        return ann - q3_cum
    return None

def get_cf_ytd(dart, ticker, year, q, acc_type):
    """현금흐름표 해당 분기까지의 누적(YTD) 값 하나만 뽑는다. 보고서가 없으면 None.

    현금흐름표는 어느 분기든 그 보고서 자체가 이미 연초부터의 누적이라(손익계산서와
    달리 '3개월 단독' 컬럼이 없다), 분기 단독값은 이 YTD들의 차로 만들어야 한다.
    """
    report_map = {1: '11013', 2: '11012', 3: '11014', 4: '11011'}
    # 💡 주주환원(배당/자사주)은 개별(별도)재무제표 기준.
    #    연결 현금흐름표의 배당금 지급액에는 종속회사가 지급한 배당이 섞여 과대계상된다.
    #    (삼성전자 2024 반기: 연결 5.98조 vs 별도 4.90조)
    fs_div = 'OFS' if acc_type in ('dividend', 'buyback') else 'CFS'
    return extract_cf_ytd_value(get_finstate_safe(dart, ticker, year, report_map[q], fs_div), acc_type)

def get_discrete_cf(dart, ticker, year, q, acc_type):
    """[현금흐름표 로직] 무조건 YTD 추출 후 앞 분기 YTD 차감.

    당분기든 직전 분기든 보고서가 없으면 None (차감의 한쪽이 없으면 계산이 성립하지 않는다).
    중간 분기 하나가 API 공백이면(예: 금융업 2023Q1~Q2) 이 함수만으로는 Q3도 None이 된다 -
    그럴 때는 sfs_step2_data_builder.discrete_cf가 이미 저장된 앞 분기 discrete 값으로 대신 구한다.
    """
    if q == 1: return get_cf_ytd(dart, ticker, year, 1, acc_type)
    if q in (2, 3, 4):
        cur = get_cf_ytd(dart, ticker, year, q, acc_type)
        prev = get_cf_ytd(dart, ticker, year, q - 1, acc_type)
        if cur is None or prev is None: return None
        return cur - prev
    return None