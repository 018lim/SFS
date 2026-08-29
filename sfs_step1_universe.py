# 파일명: sfs_step1_universe.py
import pandas as pd
from sfs_krx_api import stock

def get_sfs_universe(rebalance_date: str, min_market_cap: int = 500_000_000_000, market: str = "KOSPI") -> pd.DataFrame:
    """
    [SFS 1단계] 리밸런싱 날짜 기준으로 시가총액 조건을 만족하는 종목을 스크리닝합니다.
    - pykrx 단일 모듈을 사용하여 외부 API 의존성 제거 및 초고속 연산 달성
    
    :param rebalance_date: 리밸런싱 날짜 (예: '20260504' - pykrx 포맷인 YYYYMMDD 사용)
    :param min_market_cap: 최소 시가총액 (기본값: 5,000억 원)
    :param market: 시장 구분 (기본값: KOSPI)
    :return: 필터링된 종목들의 DataFrame
    """
    print(f"🚀 [SFS 엔진] {rebalance_date} 기준 {market} 시가총액 {min_market_cap/100000000:,.0f}억 이상 종목 스크리닝 시작...")
    
    try:
        # 1. 해당 일자의 전체 상장사 시가총액 데이터 단 1회 로드 (초고속)
        df_cap = stock.get_market_cap(rebalance_date)
        
        if df_cap.empty:
            print(f"⚠️ {rebalance_date}의 시장 데이터가 없습니다. 주말이나 휴장일인지 확인하세요.")
            return pd.DataFrame()
            
    except Exception as e:
        print(f"🚨 데이터 수집 에러: {e}")
        return pd.DataFrame()
        
    # 2. 지정된 타겟 시장(예: KOSPI)의 티커 리스트만 추출
    market_tickers = stock.get_market_ticker_list(rebalance_date, market=market)
    
    # 3. KOSPI 종목이면서 동시에 커스텀 시가총액 기준 이상인 교집합 필터링
    df_filtered = df_cap.loc[df_cap.index.intersection(market_tickers)].copy()
    df_filtered = df_filtered[df_filtered['시가총액'] >= min_market_cap].copy()
    
    # 4. 보기 좋게 종목명 매핑 및 데이터프레임 정리
    df_filtered['종목코드'] = df_filtered.index
    df_filtered['종목명'] = df_filtered['종목코드'].apply(lambda x: stock.get_market_ticker_name(x))
    
    # 필요한 컬럼만 남기고 시가총액 내림차순 정렬
    df_filtered = df_filtered[['종목코드', '종목명', '종가', '상장주식수', '시가총액']]
    df_filtered = df_filtered.sort_values(by='시가총액', ascending=False).reset_index(drop=True)
    
    print(f"✅ 스크리닝 완료: 총 {len(df_filtered)}개 기업이 SFS 유니버스로 선정되었습니다.\n")
    return df_filtered

if __name__ == "__main__":
    target_date = "20260820" 
    
    # 💡 [핵심 추가] 시가총액 커스텀 변수 (예: 1조 원 = 1_000_000_000_000)
    custom_cap = 1_000_000_000_000 
    
    # 함수 호출 시 명시적으로 파라미터 전달
    df_filtered = get_sfs_universe(
        rebalance_date=target_date, 
        min_market_cap=custom_cap,
        market="KOSPI" # 코스닥(KOSDAQ)도 여기서 자유롭게 변경 가능
    )
    
    if not df_filtered.empty:
        print(f"[🏆 SFS 유니버스 선정 리스트 상위 5개 (기준: {custom_cap/100000000:,.0f}억 이상)]")
        print(df_filtered.head(5))