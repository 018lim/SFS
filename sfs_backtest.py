import os
import pandas as pd
import numpy as np
from datetime import datetime
from dotenv import load_dotenv

# SFS 모듈 임포트
from sfs_krx_api import stock
from sfs_step1_universe import get_sfs_universe
from sfs_step2_data_builder import build_sfs_finance_db

from sfs_step3_quality import calculate_quality_factors
from sfs_step3_value import calculate_value_factors
from sfs_step4_fundamental_scoring import calculate_fundamental_score
from sfs_step5_price_momentum import calculate_price_momentum_factors
from sfs_step5_op_momentum import calculate_op_momentum_factors
from sfs_step6_momentum_scoring import calculate_momentum_score
from sfs_step7_final_scoring import calculate_final_re_zscore

load_dotenv()

def get_first_business_day(year, month):
    """지정된 연, 월의 첫 번째 영업일을 반환합니다."""
    for day in range(1, 15):
        date_str = f"{year}{month:02d}{day:02d}"
        try:
            df = stock._fetch_daily_stock_data(date_str)
            if not df.empty:
                return date_str
        except Exception:
            continue
    return f"{year}{month:02d}01"

def generate_rebalance_dates(start_year, end_year, start_quarter=1):
    """지정된 기간 동안 리밸런싱 날짜(4, 6, 9, 12월의 첫 영업일) 리스트를 생성합니다.

    start_quarter: start_year의 몇 번째 리밸런싱 월부터 시작할지 (1=4월, 2=6월, 3=9월, 4=12월).
    """
    dates = []
    months = [4, 6, 9, 12]
    today_str = datetime.now().strftime('%Y%m%d')

    for y in range(start_year, end_year + 1):
        y_months = months[start_quarter - 1:] if y == start_year else months
        for m in y_months:
            date_str = get_first_business_day(y, m)
            if date_str <= today_str:
                dates.append(date_str)
    return dates

def run_sfs_for_date(target_date):
    """특정 날짜 기준으로 SFS 엔진을 구동하여 Top 20 포트폴리오를 반환합니다."""
    
    import OpenDartReader
    dart = OpenDartReader(os.getenv("DART_API_KEY"))

    df_universe = get_sfs_universe(rebalance_date=target_date, min_market_cap=1_000_000_000_000, market="KOSPI")
    if df_universe.empty: 
        return pd.DataFrame()
    
    target_tickers = df_universe['종목코드'].tolist()
    sfs_db, base_y, base_q = build_sfs_finance_db(dart, target_tickers, target_date)
    
    # 펀더멘털 스코어링
    df_quality_raw = calculate_quality_factors(target_tickers, target_date)
    df_value_raw = calculate_value_factors(target_tickers, target_date)
    df_fundamental_top = calculate_fundamental_score(df_quality_raw, df_value_raw)
    
    surviving_tickers = df_fundamental_top['종목코드'].tolist()
    
    # 모멘텀 스코어링
    df_price_mom_raw = calculate_price_momentum_factors(surviving_tickers, target_date)
    df_op_mom_raw = calculate_op_momentum_factors(surviving_tickers, sfs_db, base_y, base_q)
    df_momentum_top = calculate_momentum_score(df_price_mom_raw, df_op_mom_raw)
    
    # 최종 스코어링
    df_merged = pd.merge(df_fundamental_top, df_momentum_top, on='종목코드', how='inner')
    df_final = calculate_final_re_zscore(df_merged)
    
    # 펀더멘털 시가총액 정보 병합 및 Top 20 컷
    df_final = pd.merge(df_final, df_universe[['종목코드', '종가', '시가총액']], on='종목코드', how='left')
    return df_final.head(20).copy()

def calculate_weights(df, method='equal'):
    """선택된 방식에 따라 포트폴리오 비중을 계산합니다."""
    if method == 'equal':
        df['Weight'] = 1.0 / len(df)
    elif method == 'market_cap':
        total_cap = df['시가총액'].sum()
        df['Weight'] = df['시가총액'] / total_cap
    elif method == 'z_score':
        # Z-스코어가 음수일 수 있으므로 최소값을 0으로 맞추고 + 1 (안전마진)
        min_z = df['Grand_Total_Z'].min()
        adjusted_z = df['Grand_Total_Z'] - min_z + 1.0
        df['Weight'] = adjusted_z / adjusted_z.sum()
    else:
        df['Weight'] = 1.0 / len(df)
    return df

def calculate_metrics(returns_series, periods_per_year=4):
    """수익률 시리즈(분기별)를 바탕으로 CAGR, MDD, Sharpe 계산"""
    if len(returns_series) == 0: return 0, 0, 0
    
    # 누적 수익률 계산
    cum_returns = (1 + returns_series).cumprod() * 100
    
    # CAGR
    years = len(returns_series) / periods_per_year
    cagr = ((cum_returns.iloc[-1] / 100) ** (1 / years) - 1) if years > 0 else 0
    
    # MDD
    rolling_max = cum_returns.cummax()
    drawdown = (cum_returns - rolling_max) / rolling_max
    mdd = drawdown.min()
    
    # Sharpe Ratio (무위험 수익률 0 가정)
    sharpe = (returns_series.mean() / returns_series.std()) * np.sqrt(periods_per_year) if returns_series.std() > 0 else 0
    
    return cagr, mdd, sharpe, cum_returns.iloc[-1]

def run_backtest(start_year=2023, weight_method='equal', start_quarter=1):
    print("=" * 80)
    print(f"[SFS 백테스팅 엔진 가동] (시작: {start_year}년 {start_quarter}번째 리밸런싱, 비중: {weight_method})")
    print("=" * 80)

    current_year = int(datetime.now().strftime('%Y'))
    rebalance_dates = generate_rebalance_dates(start_year, current_year, start_quarter)
    
    # 벤치마크(KOSPI) 데이터 가져오기
    has_kospi = False
    kospi_df = pd.DataFrame()
    try:
        import yfinance as yf
        print("\n[벤치마크] yfinance를 통해 KOSPI 지수(^KS11)를 다운로드합니다...")
        start_str = pd.to_datetime(rebalance_dates[0]).strftime("%Y-%m-%d")
        end_str = (datetime.now() + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        kospi_df = yf.download("^KS11", start=start_str, end=end_str, progress=False)
        if not kospi_df.empty:
            kospi_df.index = kospi_df.index.tz_localize(None)
            has_kospi = True
    except Exception as e:
        print(f"⚠️ KOSPI 지수 다운로드 실패 (벤치마크 제외됨): {e}")
        print("💡 pip install yfinance 를 실행해주세요.")
    
    portfolio_value = 100.0
    kospi_value = 100.0
    
    trade_logs = []
    
    history_sfs_ret = []
    history_kospi_ret = []
    
    daily_sfs_values = [100.0]
    daily_kospi_values = [100.0]
    valid_dates = [pd.to_datetime(rebalance_dates[0])]
    
    for i in range(len(rebalance_dates)):
        t_date = rebalance_dates[i]
        
        df_port = run_sfs_for_date(t_date)
        if df_port.empty: 
            print(f"[{t_date}] 데이터가 없어 건너뜁니다.")
            continue
            
        print(f"\n[{t_date}] SFS 포트폴리오 매수 종목 20개 중 (상위 5개 미리보기):")
        top5 = df_port.head(5)
        for rank, (_, row) in enumerate(top5.iterrows(), 1):
            tk_name = stock.get_market_ticker_name(row['종목코드'])
            z_score = row.get('Grand_Total_Z', 0)
            print(f" {rank}위: {tk_name} ({row['종목코드']}) - Z스코어: {z_score:.2f}")
            
        df_port = calculate_weights(df_port, weight_method)
        next_date = rebalance_dates[i+1] if i+1 < len(rebalance_dates) else datetime.now().strftime('%Y%m%d')
        
        print(f"[{t_date} -> {next_date}] 일간 수익률 평가 중 (차트 세밀도 향상)...")
        
        t_dt = pd.to_datetime(t_date)
        n_dt = pd.to_datetime(next_date)
        b_days = pd.bdate_range(start=t_dt, end=n_dt)
        if len(b_days) > 1:
            b_days = b_days[1:] # 매수 당일 제외
            
        period_start_port_val = portfolio_value
        period_start_kospi_val = kospi_value
        
        for dt in b_days:
            dt_str = dt.strftime('%Y%m%d')
            df_day_prices = stock._fetch_daily_stock_data(dt_str)
            if df_day_prices.empty:
                continue
                
            day_return = 0.0
            for _, row in df_port.iterrows():
                tk = row['종목코드']
                w = row['Weight']
                buy_price = row['종가']
                
                current_price = df_day_prices.loc[tk, '종가'] if tk in df_day_prices.index else buy_price
                ret = (current_price / buy_price) - 1.0 if buy_price > 0 else 0
                day_return += w * ret
                
            current_port_value = period_start_port_val * (1.0 + day_return)
            portfolio_value = current_port_value
            
            if has_kospi:
                try:
                    k_buy = kospi_df['Close'].asof(t_dt)
                    k_current = kospi_df['Close'].asof(dt)
                    if isinstance(k_buy, pd.Series): k_buy = k_buy.iloc[0]
                    if isinstance(k_current, pd.Series): k_current = k_current.iloc[0]
                    
                    k_ret = (k_current / k_buy) - 1.0 if k_buy > 0 else 0
                    current_ksp_value = period_start_kospi_val * (1.0 + k_ret)
                    kospi_value = current_ksp_value
                except Exception:
                    current_ksp_value = kospi_value
                daily_kospi_values.append(current_ksp_value)
                
            daily_sfs_values.append(current_port_value)
            valid_dates.append(dt)
            
        if not df_port.empty:
            print("  [개별 종목 분기 성과]")
            last_dt_str = valid_dates[-1].strftime('%Y%m%d')
            df_last_day = stock._fetch_daily_stock_data(last_dt_str)
            
            for _, row in df_port.iterrows():
                tk = row['종목코드']
                name = stock.get_market_ticker_name(tk, date=t_date)
                w = row['Weight']
                buy_price = row['종가']
                sell_price = df_last_day.loc[tk, '종가'] if not df_last_day.empty and tk in df_last_day.index else buy_price
                stock_ret = (sell_price / buy_price - 1.0) * 100 if buy_price > 0 else 0
                contribution = stock_ret * w
                
                print(f"   - {name} ({tk}): {buy_price:,.0f}원 -> {sell_price:,.0f}원 ({stock_ret:+.2f}%) | 기여도: {contribution:+.2f}%")
                
                trade_logs.append({
                    '매수일자': t_date,
                    '매도일자': next_date,
                    '종목코드': tk,
                    '종목명': name,
                    '비중(%)': round(w * 100, 2),
                    '매수가격': buy_price,
                    '매도가격': sell_price,
                    '개별수익률(%)': round(stock_ret, 2),
                    '포트폴리오기여도(%)': round(contribution, 2)
                })
                
        k_str = f" | KOSPI 누적: {kospi_value:.2f}" if has_kospi else ""
        period_ret = (portfolio_value / period_start_port_val - 1.0) * 100
        print(f"-> 분기 수익률: {period_ret:.2f}% | SFS 누적: {portfolio_value:.2f}{k_str}")
        
    print("\n" + "=" * 80)
    print(f"[백테스팅 완료] 최종 누적 자산: {portfolio_value:.2f} (원금 100 기준)")
    print("-" * 80)
    
    if trade_logs:
        log_df = pd.DataFrame(trade_logs)
        log_df.to_csv('sfs_trades_log.csv', index=False, encoding='utf-8-sig')
        print("💾 매매 내역이 'sfs_trades_log.csv' 파일로 저장되었습니다. (엑셀에서 직접 검증 가능)")
    print("-" * 80)
    
    # 일간 수익률로 변환하여 지표 계산 (periods_per_year=252 적용)
    sfs_series = pd.Series(daily_sfs_values).pct_change().dropna()
    kospi_series = pd.Series(daily_kospi_values).pct_change().dropna() if has_kospi else pd.Series()
    
    sfs_cagr, sfs_mdd, sfs_sharpe, sfs_final = calculate_metrics(sfs_series, periods_per_year=252)
    if has_kospi:
        ksp_cagr, ksp_mdd, ksp_sharpe, ksp_final = calculate_metrics(kospi_series, periods_per_year=252)
    
    print(f"{'지표':<15} | {'SFS 전략':<15} | {'KOSPI 벤치마크':<15}")
    print("-" * 55)
    print(f"{'누적 수익률':<15} | {sfs_final-100:>14.2f}% | {ksp_final-100:>14.2f}%" if has_kospi else f"{'누적 수익률':<15} | {sfs_final-100:>14.2f}%")
    print(f"{'연환산 수익률(CAGR)':<12} | {sfs_cagr*100:>14.2f}% | {ksp_cagr*100:>14.2f}%" if has_kospi else f"{'연환산 수익률(CAGR)':<12} | {sfs_cagr*100:>14.2f}%")
    print(f"{'최대낙폭(MDD)':<15} | {sfs_mdd*100:>14.2f}% | {ksp_mdd*100:>14.2f}%" if has_kospi else f"{'최대낙폭(MDD)':<15} | {sfs_mdd*100:>14.2f}%")
    print(f"{'샤프 지수(Sharpe)':<13} | {sfs_sharpe:>14.2f}x | {ksp_sharpe:>14.2f}x" if has_kospi else f"{'샤프 지수(Sharpe)':<13} | {sfs_sharpe:>14.2f}x")
    print("=" * 80)

    # ---------------------------------------------------------
    # 차트 생성 및 표시
    # ---------------------------------------------------------
    try:
        import matplotlib.pyplot as plt
        
        # 폰트 설정 (맑은 고딕 기준)
        plt.rcParams['font.family'] = 'Malgun Gothic'
        plt.rcParams['axes.unicode_minus'] = False
        
        plt.figure(figsize=(10, 6))
        
        sfs_cum = daily_sfs_values
        dates_dt = pd.to_datetime(valid_dates)
        
        plt.plot(dates_dt, sfs_cum, label='SFS 전략 (Base 100)', color='red', linewidth=1.5)
        
        if has_kospi:
            ksp_cum = daily_kospi_values
            plt.plot(dates_dt, ksp_cum, label='KOSPI 벤치마크', color='blue', linewidth=1.5, linestyle='--')
            
        plt.title('SFS 포트폴리오 vs KOSPI 누적 수익률 비교', fontsize=16)
        plt.xlabel('리밸런싱 일자', fontsize=12)
        plt.ylabel('누적 자산 (원금 100)', fontsize=12)
        plt.grid(True, linestyle=':', alpha=0.7)
        plt.legend(fontsize=12)
        plt.tight_layout()
        
        chart_path = 'sfs_backtest_result.png'
        plt.savefig(chart_path)
        print(f"\n📈 백테스트 결과 차트가 {chart_path} 에 저장되었습니다!")
        plt.show()
    except Exception as e:
        print(f"\n⚠️ 차트 생성 실패 (matplotlib 설치 필요): {e}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='SFS Backtester')
    parser.add_argument('--weight', type=str, default='equal', choices=['equal', 'market_cap', 'z_score'],
                        help='포트폴리오 비중 방식 (equal, market_cap, z_score)')
    parser.add_argument('--start_year', type=int, default=2021, help='백테스트 시작 연도 (기본값: 2021)')
    parser.add_argument('--start_quarter', type=int, default=1, choices=[1, 2, 3, 4],
                        help='start_year의 몇 번째 리밸런싱 월부터 시작할지: 1=4월, 2=6월, 3=9월, 4=12월 (기본값: 1)')
    args = parser.parse_args()

    run_backtest(start_year=args.start_year, weight_method=args.weight, start_quarter=args.start_quarter)
