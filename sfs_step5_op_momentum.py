# 파일명: sfs_step5_op_momentum.py
import os
import pandas as pd
import numpy as np
import OpenDartReader
from sfs_utils import get_common_ticker, get_discrete_is

def get_rolling_blocks(base_year, base_q, interval_months):
    """T-2, T-1, T 분기 덩어리(Block) 역산"""
    interval_q = interval_months // 3
    base_abs_q = base_year * 4 + base_q
    blocks = []
    for i in [2, 1, 0]:
        block_end_abs_q = base_abs_q - (i * interval_q)
        block_quarters = []
        for j in range(interval_q):
            target_abs_q = block_end_abs_q - j
            y = (target_abs_q - 1) // 4
            q = ((target_abs_q - 1) % 4) + 1
            block_quarters.append((y, q))
        block_quarters.reverse()
        blocks.append(block_quarters)
    return blocks

def prefetch_op_momentum_data(dart_api, sfs_db, ticker, base_year, base_q, max_quarters=12):
    """과거 12개 분기 OP 및 총 발행주식수 일괄 조회 및 캐싱"""
    actual_ticker = get_common_ticker(ticker)
    cache = {}
    base_abs_q = base_year * 4 + base_q
    db_company = sfs_db.get(actual_ticker, {})
    
    for i in range(max_quarters):
        target_abs = base_abs_q - i
        y = (target_abs - 1) // 4
        q = ((target_abs - 1) % 4) + 1
        y_str, q_str = str(y), str(q)
        
        op_profit = None
        # 1. 로컬 JSON(sfs_db) 우선 확인
        if y_str in db_company and q_str in db_company[y_str]:
            op_profit = db_company[y_str][q_str].get('op')
            
        # 2. 로컬에 없으면 DART API 호출
        if op_profit is None:
            try:
                op_profit = get_discrete_is(dart_api, actual_ticker, y, q, 'op')
            except Exception:
                op_profit = None
                
        # 3. 분기말 주식수 가져오기 (제거됨: 가속도 산출 시 상수로 소거되므로 불필요)
        total_shares = 1.0
            
        cache[(y, q)] = {'profit': op_profit, 'shares': total_shares}
    return cache

def calculate_fundamental_momentum_indicator(data_cache, base_year, base_q, interval_months):
    """메모리 딕셔너리 기반 2차 미분(가속도) 스코어 산출. 결측치 존재 시 np.nan 반환"""
    blocks = get_rolling_blocks(base_year, base_q, interval_months)
    op_eps_values = []
    
    for block in blocks:
        block_profit = 0
        for y, q in block:
            q_data = data_cache.get((y, q), {'profit': None})
            if q_data['profit'] is None:
                return np.nan
                
            block_profit += q_data['profit']
                
        op_eps_values.append(block_profit)
        
    eps_t2, eps_t1, eps_t0 = op_eps_values
    v_t1 = eps_t1 - eps_t2
    v_t0 = eps_t0 - eps_t1
    acceleration = v_t0 - v_t1
    
    base_velocity = abs(v_t1) if abs(v_t1) > 0 else 1e-6
    raw_score = acceleration / base_velocity
    normalized_score = max(min(raw_score, 10.0), -10.0)
    return normalized_score

def calculate_op_momentum_factors(target_tickers, sfs_db, base_year, base_q):
    """
    [SFS 5단계 - 영업이익(OP) 가속도 모멘텀 팩터]
    대상 종목들에 대해 3M, 6M, 9M, 12M 영업이익 가속도 원시 지표를 계산합니다.
    """
    print(f"\n🚀 [OP Momentum] 영업이익 가속도 지표 산출 가동 (총 {len(target_tickers)}종목)")
    dart = OpenDartReader(os.getenv("DART_API_KEY"))
    results = []
    
    for idx, tk in enumerate(target_tickers):
        print(f"[{idx+1}/{len(target_tickers)}] {tk} 12분기 재무 이력 수집 중...")
        cached_data = prefetch_op_momentum_data(dart, sfs_db, tk, base_year, base_q, max_quarters=12)
        
        fm_3m = calculate_fundamental_momentum_indicator(cached_data, base_year, base_q, 3)
        fm_6m = calculate_fundamental_momentum_indicator(cached_data, base_year, base_q, 6)
        fm_9m = calculate_fundamental_momentum_indicator(cached_data, base_year, base_q, 9)
        fm_12m = calculate_fundamental_momentum_indicator(cached_data, base_year, base_q, 12)
        
        results.append({
            '종목코드': tk,
            'OP_3M': fm_3m,
            'OP_6M': fm_6m,
            'OP_9M': fm_9m,
            'OP_12M': fm_12m
        })
        
    df_op_mom = pd.DataFrame(results)
    return df_op_mom
