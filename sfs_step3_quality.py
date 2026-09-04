# 파일명: sfs_step3_quality.py
import pandas as pd
from sfs_utils import (
    get_common_ticker, load_local_db,
    get_latest_available_quarter, get_dynamic_quarters
)

def calculate_quality_factors(target_tickers, rebalance_date):
    """SFS 퀄리티 섹터 핵심 지표를 계산하여 DataFrame으로 반환합니다."""
    print(f"\n🚀 [SFS 3단계] {rebalance_date} 기준 퀄리티 팩터 연산 가동")
    
    sfs_db = load_local_db(must_exist=True)
    base_y, base_q = get_latest_available_quarter(rebalance_date)
    cur_qs, prev_qs, curr_key, prev_key = get_dynamic_quarters(base_y, base_q)
    
    results = []
    
    for tk in target_tickers:
        # 💡 [핵심] 우선주가 들어와도 본주 코드로 맵핑하여 재무 데이터 참조
        actual_tk = get_common_ticker(tk)
        
        if actual_tk not in sfs_db:
            print(f" ⚠️ [{tk}] 로컬 DB에 데이터가 없어 연산을 건너뜁니다.")
            continue
            
        db = sfs_db[actual_tk]
        category = db.get('category', '일반기업')
        
        # --- [A] 동적 TTM 합산 헬퍼 함수 ---
        # 네 분기 중 하나라도 값이 없으면(상장 전 등) None을 돌려준다. 없는 분기를 0으로
        # 더하면 TTM이 과소 집계돼 점수만 조용히 왜곡된다.
        def ttm(key, qs_list):
            total = 0
            for y_str, q_str in qs_list:
                v = db.get(y_str, {}).get(q_str, {}).get(key)
                if v is None: return None
                total += v
            return total

        # --- [B] 펀더멘털 데이터 추출 ---
        ttm_op = ttm('op', cur_qs)
        ttm_ni = ttm('ni', cur_qs)
        # 주주환원금 = 배당 + 자사주 취득 (DB에는 나눠서 저장하고 여기서 합산)
        ttm_div_cur, ttm_bb_cur = ttm('dividend', cur_qs), ttm('buyback', cur_qs)
        ttm_div_prev, ttm_bb_prev = ttm('dividend', prev_qs), ttm('buyback', prev_qs)

        if any(v is None for v in (ttm_op, ttm_ni, ttm_div_cur, ttm_bb_cur, ttm_div_prev, ttm_bb_prev)):
            print(f" ⚠️ [{tk}] TTM 구간에 없는 분기가 있어 건너뜁니다. (상장 전 등으로 보고서 미제출)")
            continue

        ttm_ret_cur = ttm_div_cur + ttm_bb_cur
        ttm_ret_prev = ttm_div_prev + ttm_bb_prev

        try:
            assets_cur = db[curr_key[0]][curr_key[1]]['assets']
            assets_prev = db[prev_key[0]][prev_key[1]]['assets']
            equity_cur = db[curr_key[0]][curr_key[1]]['equity']
            equity_prev = db[prev_key[0]][prev_key[1]]['equity']
            liabilities_cur = db[curr_key[0]][curr_key[1]]['liabilities']
            
            avg_assets = (assets_cur + assets_prev) / 2
            avg_equity = (equity_cur + equity_prev) / 2
        except KeyError as e:
            print(f" ⚠️ [{tk}] 기말/기초 스냅샷 데이터 누락으로 건너뜁니다. (누락 원인: {e})")
            continue

        # --- [C] 퀄리티 지표 산출 (공통) ---
        op_a = (ttm_op / avg_assets) * 100 if avg_assets > 0 else 0
        ret_growth = ((ttm_ret_cur / ttm_ret_prev) - 1) * 100 if ttm_ret_prev > 0 else 0
        
        # --- [D] 퀄리티 지표 산출 (섹터 분기) ---
        accruals_ratio, fcf_debt, roe = None, None, None
        
        if category == "일반기업":
            ttm_ocf = ttm('ocf', cur_qs)
            ttm_capex = ttm('capex', cur_qs)
            
            accruals_ratio = ((ttm_ni - ttm_ocf) / avg_assets) * 100 if avg_assets > 0 else 0
            fcf_debt = ((ttm_ocf - ttm_capex) / liabilities_cur) * 100 if liabilities_cur > 0 else 0
        else:
            roe = (ttm_ni / avg_equity) * 100 if avg_equity > 0 else 0

        # 결과 저장 (우선주 여부 태깅 포함)
        is_pref = "우선주" if tk != actual_tk else "본주"
        
        results.append({
            "종목코드": tk,
            "구분": is_pref,
            "섹터": category,
            "OP/A(%)": op_a,
            "환원금인상률(%)": ret_growth,
            "발생액/자산(%)": accruals_ratio,
            "FCF/총부채(%)": fcf_debt,
            "ROE(%)": roe
        })

    # DataFrame 변환 후 출력
    df_quality = pd.DataFrame(results)
    
    print("\n" + "="*70)
    print("📊 [SFS 퀄리티 섹터 지표 산출 결과]")
    print("="*70)
    
    return df_quality

if __name__ == "__main__":
    
    
    # 💡 1단계 유니버스에서 삼성전자(본주), 삼성전자우(우선주), KB금융이 넘어왔다고 가정
    test_tickers = ['005930', '005935', '105560'] 
    test_date = '20260821'
    
    df_result = calculate_quality_factors(test_tickers, test_date)