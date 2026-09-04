# 파일명: sfs_step3_value.py
import pandas as pd
import numpy as np
from datetime import datetime

from sfs_krx_api import stock
from sfs_utils import (
    get_common_ticker, load_local_db,
    get_latest_available_quarter, get_ttm_coords
)

def calculate_value_factors(target_tickers, rebalance_date):
    print(f"\n🚀 [SFS 3단계] {rebalance_date} 기준 밸류 팩터 연산 가동")
    
    sfs_db = load_local_db(must_exist=True)
    base_y, base_q = get_latest_available_quarter(rebalance_date)
    cur_qs = get_ttm_coords(base_y, base_q)
    
    actual_market_date = stock.get_nearest_business_day(rebalance_date)
    df_cap_all = stock.get_market_cap(actual_market_date)
    print(f"📡 [KRX 연동] {actual_market_date[:4]}년 {actual_market_date[4:6]}월 {actual_market_date[6:]}일 종가/주식수 기준 데이터 획득")
    results = []
    
    for tk in target_tickers:
        actual_tk = get_common_ticker(tk)
        
        if actual_tk not in sfs_db:
            print(f" ⚠️ [{tk}] 로컬 DB에 펀더멘털 데이터가 없어 연산을 건너뜁니다.")
            continue
            
        db = sfs_db[actual_tk]
        
        # 💡 [수정] 2단계 JSON 구조에 맞춰 깔끔한 카테고리 추출 적용
        category = db.get('category', '일반기업')
        
        total_ni, total_ocf, total_return = 0.0, 0.0, 0.0
        missing_quarter = False

        for y_str, q_str in cur_qs:
            # 💡 [수정] 불필요한 depth 확인 로직 제거
            q_data = db.get(y_str, {}).get(q_str, {})
            ni = q_data.get('ni')
            dividend, buyback = q_data.get('dividend'), q_data.get('buyback')
            # ocf는 PCR용이라 일반기업만 수집한다. 금융기업에 없는 건 정상이다
            ocf = q_data.get('ocf') if category == '일반기업' else 0.0
            # 값이 없는 분기(상장 전 등)를 0으로 더하면 TTM이 조용히 과소 집계된다
            if any(v is None for v in (ni, dividend, buyback, ocf)):
                missing_quarter = True
                break
            total_ni += ni
            total_ocf += ocf
            # 주주환원금 = 배당 + 자사주 취득 (DB에는 나눠서 저장하고 여기서 합산)
            total_return += dividend + buyback

        if missing_quarter:
            print(f" ⚠️ [{tk}] TTM 구간에 없는 분기가 있어 건너뜁니다. (상장 전 등으로 보고서 미제출)")
            continue

        latest_y, latest_q = cur_qs[0]
        try:
            total_equity = db[latest_y][latest_q]['equity']
        except KeyError as e:
            print(f" ⚠️ [{tk}] 자본 스냅샷 데이터 누락으로 건너뜁니다. (누락 원인: {e})")
            continue

        target_price = float(df_cap_all.loc[tk, '종가']) if tk in df_cap_all.index else 0.0
        
        prefix = actual_tk[:-1] 
        matching_tickers = [t for t in df_cap_all.index if t.startswith(prefix)]
        total_shares = sum(float(df_cap_all.loc[t, '상장주식수']) for t in matching_tickers)
        
        if total_shares == 0 or target_price == 0:
            print(f" ⚠️ [{tk}] 주가 또는 주식수 오류로 건너뜁니다.")
            continue

        adj_eps = total_ni / total_shares
        adj_bps = total_equity / total_shares
        adj_cps = total_ocf / total_shares
        adj_return_ps = total_return / total_shares
        
        per = target_price / adj_eps if adj_eps > 0 else 9999.0
        pbr = target_price / adj_bps if adj_bps > 0 else 9999.0
        sh_yield = (adj_return_ps / target_price) * 100 if adj_return_ps > 0 else 0.0
        
        # 💡 [핵심] 분류명을 "금융기업"으로 매칭하여 PCR 원천 배제
        if category == "일반기업":
            pcr = target_price / adj_cps if adj_cps > 0 else 9999.0
        else:
            pcr = np.nan

        is_pref = "우선주" if tk != actual_tk else "본주"
        
        results.append({
            "종목코드": tk,
            "구분": is_pref,
            "섹터": category,
            "주가(원)": target_price,
            "PER": per,
            "PBR": pbr,
            "PCR": pcr,
            "주주수익률(%)": sh_yield
        })

    df_value = pd.DataFrame(results)
    
    print("\n" + "="*80)
    print(f"📊 [SFS 밸류 섹터 지표 산출 완료] (시장 반영일: {actual_market_date})")
    print("="*80)
    return df_value

if __name__ == "__main__":
    test_tickers = ['005930', '005935', '105560'] 
    test_date = datetime.now().strftime('%Y%m%d')
    df_result = calculate_value_factors(test_tickers, test_date)