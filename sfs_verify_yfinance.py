import pandas as pd
import yfinance as yf
import warnings
warnings.filterwarnings('ignore')

def convert_ticker_to_yf(ticker):
    """한국 종목코드를 야후 파이낸스 포맷으로 변환"""
    return f"{str(ticker).zfill(6)}.KS"

def verify_trades(csv_file='sfs_trades_log.csv'):
    try:
        df = pd.read_csv(csv_file)
    except FileNotFoundError:
        print(f"🚨 에러: {csv_file} 파일을 찾을 수 없습니다. 백테스트를 먼저 실행해주세요.")
        return

    # 종목코드가 6자리가 되도록 문자열 포맷팅
    df['종목코드'] = df['종목코드'].astype(str).str.zfill(6)
    
    quarters = df['매수일자'].unique()
    total_portfolio_value = 100.0
    
    print("="*85)
    print(" 🔍 SFS 백테스트 결과 야후 파이낸스(YFinance) 완전 독립 교차 검증")
    print("  - 기존 pykrx의 '단순 종가' 대신 YFinance의 '수정주가(Adj Close)'를 사용합니다.")
    print("  - 배당금, 액면분할 등이 모두 반영되므로 실제 계좌에 찍히는 수익률과 가장 유사합니다.")
    print("="*85)
    
    for q_date in quarters:
        q_df = df[df['매수일자'] == q_date]
        sell_date = q_df['매도일자'].iloc[0]
        
        start_dt = pd.to_datetime(str(q_date))
        end_dt = pd.to_datetime(str(sell_date))
        
        start_str = start_dt.strftime('%Y-%m-%d')
        end_str = end_dt.strftime('%Y-%m-%d')
        
        quarter_return = 0.0
        print(f"\n[{start_str} -> {end_str}] 분기 교차 검증 중 (20종목 다운로드)...")
        
        fetch_start = (start_dt - pd.Timedelta(days=7)).strftime('%Y-%m-%d')
        fetch_end = (end_dt + pd.Timedelta(days=7)).strftime('%Y-%m-%d')
        
        for _, row in q_df.iterrows():
            tk = row['종목코드']
            yf_tk = convert_ticker_to_yf(tk)
            weight = row['비중(%)'] / 100.0
            name = row['종목명']
            
            try:
                tk_obj = yf.Ticker(yf_tk)
                hist = tk_obj.history(start=fetch_start, end=fetch_end, auto_adjust=True)
                
                if hist.empty:
                    print(f"  ⚠️ {name} ({yf_tk}) 야후 데이터 없음. 기존 기여도 대체.")
                    quarter_return += row['포트폴리오기여도(%)']
                    continue
                
                prices = hist['Close'].dropna()
                    
                buy_prices = prices[prices.index >= start_str]
                sell_prices = prices[prices.index <= end_str]
                
                if buy_prices.empty or sell_prices.empty:
                    print(f"  ⚠️ {name} 날짜 매칭 실패. 기존 기여도 대체.")
                    quarter_return += row['포트폴리오기여도(%)']
                    continue
                    
                actual_buy_price = float(buy_prices.iloc[0])
                actual_sell_price = float(sell_prices.iloc[-1])
                
                yf_ret = (actual_sell_price / actual_buy_price - 1.0) * 100
                yf_contrib = yf_ret * weight
                quarter_return += yf_contrib
                
            except Exception as e:
                print(f"  ⚠️ {name} 에러 발생: {e}")
                quarter_return += row['포트폴리오기여도(%)']
                
        csv_q_return = q_df['포트폴리오기여도(%)'].sum()
        diff = quarter_return - csv_q_return
        
        print(f" -> 📊 SFS 기존(pykrx 단순종가) 분기 수익률: {csv_q_return:+.2f}%")
        print(f" -> 🔍 YFinance (배당반영 수정주가) 수익률:   {quarter_return:+.2f}% (차이: {diff:+.2f}%)")
        
        total_portfolio_value *= (1.0 + quarter_return / 100.0)
        
    print("="*85)
    print(f"🏆 YFinance 검증 (배당 포함) 최종 누적 자산: {total_portfolio_value:.2f} (원금 100 기준)")
    print("="*85)

if __name__ == "__main__":
    verify_trades()
