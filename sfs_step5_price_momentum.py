# 파일명: sfs_step5_price_momentum.py
import pandas as pd
import numpy as np
from datetime import datetime
from dateutil.relativedelta import relativedelta
from sfs_krx_api import stock

def calculate_price_momentum_indicator(price_series, current_date, months):
    """지정된 개월 수(months)에 대한 주가 모멘텀 원시 지표를 계산합니다."""
    current_date = pd.to_datetime(current_date).normalize()
    target_start_date = current_date - relativedelta(months=months)
    
    period_prices = price_series.loc[target_start_date:current_date]
    actual_days = len(period_prices)
    if actual_days < (months * 15): 
        return np.nan
        
    p_start = period_prices.iloc[0]
    p_end = period_prices.iloc[-1]
    period_return = (p_end / p_start) - 1
    
    daily_returns = period_prices.pct_change().dropna()
    vol = daily_returns.std() * np.sqrt(actual_days)
    
    downside_returns = daily_returns[daily_returns < 0]
    down_vol = downside_returns.std() * np.sqrt(actual_days) if len(downside_returns) > 0 else 0.0
        
    risk = (0.5 * vol) + (0.5 * down_vol)
    return period_return / risk if risk > 0 else np.nan

def calculate_price_momentum_factors(target_tickers, target_date):
    """
    [SFS 5단계 - 주가 모멘텀 팩터]
    대상 종목들에 대해 3M, 6M, 9M, 12M 리스크 조정 주가 모멘텀 원시 지표를 계산합니다.
    """
    print(f"\n📈 [Price Momentum] 주가 모멘텀 지표 산출 가동 (총 {len(target_tickers)}종목)")
    
    end_date = pd.to_datetime(target_date)
    start_date_total = end_date - relativedelta(months=14)
    start_str = start_date_total.strftime("%Y%m%d")
    end_str = target_date
    
    results = []
    
    for idx, tk in enumerate(target_tickers):
        print(f"[{idx+1}/{len(target_tickers)}] {tk} 주가 이력 수집 중...")
        pm_3m, pm_6m, pm_9m, pm_12m = 0.0, 0.0, 0.0, 0.0
        try:
            df_price = stock.get_historical_prices(tk, start_str, end_str)
            if df_price is not None and not df_price.empty:
                close_series = df_price['종가']
                close_series.index = pd.to_datetime(close_series.index).normalize()
                
                pm_3m = calculate_price_momentum_indicator(close_series, end_date, 3)
                pm_6m = calculate_price_momentum_indicator(close_series, end_date, 6)
                pm_9m = calculate_price_momentum_indicator(close_series, end_date, 9)
                pm_12m = calculate_price_momentum_indicator(close_series, end_date, 12)
        except Exception as e:
            print(f"  ⚠️ [{tk}] 주가 데이터 조회 에러: {e}")
            
        results.append({
            '종목코드': tk,
            'Price_3M': pm_3m,
            'Price_6M': pm_6m,
            'Price_9M': pm_9m,
            'Price_12M': pm_12m
        })
        
    df_price_mom = pd.DataFrame(results)
    return df_price_mom
