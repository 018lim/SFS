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

def clear_finstate_cache():
    """종목 1개의 처리가 끝날 때마다 호출하여 메모리(RAM) 폭발을 방지합니다."""
    _DOC_CACHE.clear()

def get_finstate_safe(dart, ticker, year, reprt_code):
    """
    [문서 중심 & 에러 방어 호출] 
    1. 해당 분기 재무제표를 1번만 받아 캐시에 저장하고 재사용 (API 호출량 80% 감소)
    2. CFS(연결)가 없으면 OFS(개별)로 자동 재시도 (013 에러 원천 차단)
    3. DART 미등록 종목일 경우 깔끔하게 예외를 던져 프로그램 크래시 방지
    """
    cache_key = f"{ticker}_{year}_{reprt_code}"
    
    # 캐시에 이미 해당 문서가 있으면 API를 찌르지 않고 즉시 반환
    if cache_key in _DOC_CACHE:
        return _DOC_CACHE[cache_key]
        
    try:
        df = dart.finstate_all(ticker, year, reprt_code, 'CFS')
        # 연결재무제표가 비어있으면 개별재무제표로 재시도
        if df is None or df.empty:
            df = dart.finstate_all(ticker, year, reprt_code, 'OFS')
            
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
    """재무상태표 스냅샷 추출 (Stock)"""
    report_map = {1: '11013', 2: '11012', 3: '11014', 4: '11011'}
    
    # 💡 최적화된 안전 호출 함수 사용
    df = get_finstate_safe(dart, ticker, year, report_map[q])
    if df is None or df.empty: return 0.0

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

def extract_is_value(df, acc_type):
    """손익계산서 3개월 단독 실적 추출 (OP, NI)"""
    if df is None or df.empty: return 0.0
    df['clean_acc'] = df['account_nm'].astype(str).str.replace(' ', '')
    df['clean_sj'] = df['sj_nm'].astype(str).str.replace(' ', '')
    
    tgt = pd.DataFrame()
    if acc_type == 'op':
        if 'account_id' in df.columns: tgt = df[df['account_id'] == 'ifrs-full_OperatingProfitLoss']
        if tgt.empty: tgt = df[(df['clean_sj'].str.contains('손익|포괄')) & (df['clean_acc'].isin(['영업이익', '영업이익(손실)', '영업손실']))]
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
            
    if not tgt.empty: return get_clean_value(tgt.iloc[0]['thstrm_amount'])
    return 0.0

def extract_cf_ytd_value(df, acc_type):
    """현금흐름표 누적(YTD) 실적 추출 (OCF, CAPEX, RETURN)"""
    if df is None or df.empty: return 0.0
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
        
    elif acc_type == 'return':
        val_div, val_bb = 0.0, 0.0
        tgt_d = df_cf[df_cf['account_id'].str.contains('DividendsPaid', na=False)] if 'account_id' in df_cf.columns else pd.DataFrame()
        if tgt_d.empty: tgt_d = df_cf[df_cf['clean_acc'].str.contains('배당금') & df_cf['clean_acc'].str.contains('지급')]
        if not tgt_d.empty: val_div = abs(get_clean_value(tgt_d.iloc[0]['thstrm_amount']))
        
        tgt_b = df_cf[df_cf['account_id'].str.contains('PaymentsForSharesRepurchased|PurchaseOfTreasuryShares', na=False)] if 'account_id' in df_cf.columns else pd.DataFrame()
        if tgt_b.empty: tgt_b = df_cf[df_cf['clean_acc'].str.contains('자기주식') & df_cf['clean_acc'].str.contains('취득|매입')]
        if not tgt_b.empty: val_bb = abs(get_clean_value(tgt_b.iloc[0]['thstrm_amount']))
        return val_div + val_bb
        
    return 0.0

def get_discrete_is(dart, ticker, year, q, acc_type):
    """[손익계산서 로직] 1~3분기는 단독 추출, 4분기는 연간에서 1~3분기 차감"""
    report_map = {1: '11013', 2: '11012', 3: '11014'}
    if q in report_map:
        # 💡 최적화된 안전 호출 함수 사용
        return extract_is_value(get_finstate_safe(dart, ticker, year, report_map[q]), acc_type)
    elif q == 4:
        # 💡 최적화된 안전 호출 함수 사용 (4분기는 캐싱 효과가 극대화됨)
        ann = extract_is_value(get_finstate_safe(dart, ticker, year, '11011'), acc_type)
        q1 = extract_is_value(get_finstate_safe(dart, ticker, year, '11013'), acc_type)
        q2 = extract_is_value(get_finstate_safe(dart, ticker, year, '11012'), acc_type)
        q3 = extract_is_value(get_finstate_safe(dart, ticker, year, '11014'), acc_type)
        return ann - (q1 + q2 + q3)
    return 0.0

def get_discrete_cf(dart, ticker, year, q, acc_type):
    """[현금흐름표 로직] 무조건 YTD 추출 후 앞 분기 YTD 차감"""
    report_map = {1: '11013', 2: '11012', 3: '11014', 4: '11011'}
    # 💡 최적화된 안전 호출 함수 사용
    def _ytd(y, rq): return extract_cf_ytd_value(get_finstate_safe(dart, ticker, y, report_map[rq]), acc_type)
        
    if q == 1: return _ytd(year, 1)
    elif q == 2: return _ytd(year, 2) - _ytd(year, 1)
    elif q == 3: return _ytd(year, 3) - _ytd(year, 2)
    elif q == 4: return _ytd(year, 4) - _ytd(year, 3)
    return 0.0