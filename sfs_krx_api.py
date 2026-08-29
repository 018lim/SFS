import os
import time
import requests
import pandas as pd
from datetime import datetime
import calendar
from dotenv import load_dotenv

load_dotenv()

class KRXOpenAPIWrapper:
    """
    pykrx의 stock 모듈 인터페이스를 KRX Open API(공식)로 매핑하는 어댑터 클래스입니다.
    """
    def __init__(self):
        self.api_key = os.getenv("KRX_API_KEY")
        self.base_url = "http://data-dbg.krx.co.kr/svc/apis"
        self._daily_cache = {}  # 날짜별 데이터 캐시 (메모리)
        
        # 로컬 디스크 캐시 폴더 설정
        self.cache_dir = "krx_cache"
        if not os.path.exists(self.cache_dir):
            os.makedirs(self.cache_dir)
        
    def _fetch_daily_stock_data(self, date_str):
        """특정 일자의 전종목 시세 데이터를 가져옵니다 (로컬 캐시 최우선)."""
        # 1. 메모리 캐시 확인
        if date_str in self._daily_cache:
            return self._daily_cache[date_str]
            
        # 2. 로컬 디스크 캐시 확인 (.pkl 파일)
        cache_file = os.path.join(self.cache_dir, f"{date_str}.pkl")
        if os.path.exists(cache_file):
            try:
                df = pd.read_pickle(cache_file)
                self._daily_cache[date_str] = df
                return df
            except Exception as e:
                print(f"⚠️ 로컬 주가 캐시 읽기 에러 (다시 다운로드합니다): {e}")
                
        # 3. 디스크에 없으면 API 호출
        if not self.api_key:
            raise ValueError("🚨 KRX_API_KEY 환경변수가 설정되지 않았습니다. .env 파일을 확인하세요.")
            
        url = f"{self.base_url}/sto/stk_bydd_trd"
        headers = {'AUTH_KEY': self.api_key}
        params = {'basDd': date_str}
        
        try:
            response = requests.get(url, params=params, headers=headers)
            response.raise_for_status()
            data = response.json()
            
            if 'OutBlock_1' in data:
                df = pd.json_normalize(data['OutBlock_1'])
                
                # ISU_SRT_CD(단축코드)가 있으면 우선 사용, 없으면 ISU_CD 사용
                ticker_col = 'ISU_SRT_CD' if 'ISU_SRT_CD' in df.columns else 'ISU_CD'
                
                df.rename(columns={
                    ticker_col: '종목코드',
                    'ISU_NM': '종목명',
                    'TDD_CLSPRC': '종가',
                    'LIST_SHRS': '상장주식수',
                    'MKTCAP': '시가총액',
                    'MKT_NM': '시장'
                }, inplace=True)
                
                # 종목코드가 'KR7005930003' 처럼 길게 나오는 경우 대비하여 우측 6자리 숫자만 추출
                if '종목코드' in df.columns:
                    df['종목코드'] = df['종목코드'].astype(str).str.extract(r'([0-9A-Z]{6})$', expand=False)
                
                # 문자열로 들어오는 숫자 데이터 형변환
                for col in ['종가', '상장주식수', '시가총액']:
                    if col in df.columns:
                        df[col] = df[col].astype(str).str.replace(',', '').astype(float)
                        
                # 종목코드 컬럼이 있으면 인덱스로 세팅
                if '종목코드' in df.columns:
                    df.set_index('종목코드', inplace=True)
                    
                # 4. 메모리 및 로컬 디스크에 캐싱
                self._daily_cache[date_str] = df
                try:
                    df.to_pickle(cache_file)
                except Exception as e:
                    print(f"⚠️ 로컬 주가 캐시 저장 실패: {e}")
                    
                return df
            else:
                # 에러 메시지나 빈 데이터 반환 처리
                print(f"⚠️ KRX API 응답에 데이터가 없습니다: {data}")
                return pd.DataFrame()
        except Exception as e:
            print(f"🚨 KRX API 호출 에러: {e}")
            return pd.DataFrame()

    def get_market_cap(self, date, market=None):
        """
        특정 날짜의 시가총액 정보 반환
        pykrx: stock.get_market_cap(date) 반환 형식과 유사하게 (인덱스: 종목코드)
        """
        df = self._fetch_daily_stock_data(date)
        if df.empty:
            return pd.DataFrame()
            
        # market 파라미터가 있으면 필터링 (예: KOSPI, KOSDAQ)
        if market:
            df = df[df['시장'] == market]
            
        return df[['종가', '상장주식수', '시가총액']]
        
    def get_market_ticker_list(self, date, market="KOSPI"):
        """특정 시장의 종목코드 리스트 반환"""
        df = self._fetch_daily_stock_data(date)
        if df.empty:
            return []
            
        if market:
            df = df[df['시장'] == market]
            
        return df.index.tolist()
        
    def get_market_ticker_name(self, ticker, date=None):
        """종목 코드로 종목명 반환"""
        if not date:
            # 캐시된 가장 최근 날짜를 찾거나 오늘 날짜로 요청
            available_dates = list(self._daily_cache.keys())
            if available_dates:
                date = available_dates[-1]
            else:
                date = datetime.now().strftime('%Y%m%d')
            
        df = self._fetch_daily_stock_data(date)
        if not df.empty and ticker in df.index:
            return df.loc[ticker, '종목명']
        return "Unknown"
        
    def get_previous_business_days(self, year, month):
        """
        특정 연월의 영업일 리스트를 datetime 객체 리스트로 반환 (임시 구현)
        - 완벽한 영업일/휴장일 API가 따로 필요하나, 임시로 평일 기준 생성
        """
        num_days = calendar.monthrange(year, month)[1]
        days = [datetime(year, month, day) for day in range(1, num_days + 1)]
        
        # 주말(토, 일) 제외 평일만 영업일로 간주 (공휴일 제외 로직은 추가 필요)
        business_days = [d for d in days if d.weekday() < 5]
        return business_days

    def get_nearest_business_day(self, target_date_str=None):
        """오늘(또는 특정일)부터 과거로 7일간 역순 탐색하여 데이터가 존재하는 가장 최근 영업일 반환"""
        if not target_date_str:
            target_date_str = datetime.now().strftime('%Y%m%d')
            
        target_dt = pd.to_datetime(target_date_str)
        for i in range(7):
            check_dt = target_dt - pd.Timedelta(days=i)
            date_str = check_dt.strftime("%Y%m%d")
            df = self._fetch_daily_stock_data(date_str)
            if not df.empty:
                return date_str
        return target_date_str

    def get_historical_prices(self, ticker, start_date_str, end_date_str):
        """
        특정 기간 동안의 개별 종목 주가 이력(종가)을 반환합니다.
        KRX Open API의 일별 전종목 데이터를 반복 조회하여 추출합니다.
        (로컬 캐시가 적용되어 있으므로 최초 1회만 시간이 소요됩니다.)
        """
        start_dt = pd.to_datetime(start_date_str)
        end_dt = pd.to_datetime(end_date_str)
        
        # 평일만 캘린더 생성 (주말 제외)
        date_range = pd.date_range(start=start_dt, end=end_dt, freq='B')
        
        prices = {}
        for dt in date_range:
            d_str = dt.strftime("%Y%m%d")
            df = self._fetch_daily_stock_data(d_str)
            if not df.empty and ticker in df.index:
                prices[dt] = df.loc[ticker, '종가']
                
        df_hist = pd.DataFrame(list(prices.items()), columns=['날짜', '종가'])
        if not df_hist.empty:
            df_hist.set_index('날짜', inplace=True)
            
        return df_hist

# 싱글톤 인스턴스를 생성하여 pykrx의 stock 모듈처럼 사용할 수 있게 함
stock = KRXOpenAPIWrapper()
