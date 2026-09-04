import os
import time
from datetime import datetime
import pandas as pd
import unicodedata
from dotenv import load_dotenv

# 🚨 환경변수 로드 (KRX 및 DART API 키 장전)
load_dotenv()
import OpenDartReader

# 📦 SFS 모듈 임포트
from sfs_step1_universe import get_sfs_universe
from sfs_step2_data_builder import build_sfs_finance_db
from sfs_step3_quality import calculate_quality_factors
from sfs_step3_value import calculate_value_factors
from sfs_step4_fundamental_scoring import calculate_fundamental_score
from sfs_step5_price_momentum import calculate_price_momentum_factors
from sfs_step5_op_momentum import calculate_op_momentum_factors
from sfs_step6_momentum_scoring import calculate_momentum_score
from sfs_step7_final_scoring import calculate_final_re_zscore
from sfs_config import CUTOFFS, WEIGHTS
from sfs_utils import get_common_ticker, get_ttm_coords
from sfs_krx_api import stock

def get_display_width(s):
    """한글(동아시아 문자)은 2칸, 영문/숫자는 1칸으로 계산하여 실제 터미널 출력 폭을 구합니다."""
    return sum(2 if unicodedata.east_asian_width(c) in 'WF' else 1 for c in str(s))

def print_aligned_table(df):
    """Dataframe을 콘솔에 완벽히 정렬하여 출력합니다."""
    cols = df.columns.tolist()
    # 각 컬럼별 최대 폭 계산 (컬럼명 길이 vs 데이터 길이)
    col_widths = [max(get_display_width(c), max(get_display_width(v) for v in df[c])) for c in cols]
    
    # 헤더 출력 (가운데 정렬 느낌)
    header = " | ".join(str(c) + " " * (w - get_display_width(c)) for c, w in zip(cols, col_widths))
    print(header)
    print("-" * len(header))
    
    # 데이터 행 출력
    for _, row in df.iterrows():
        row_str = " | ".join(str(v) + " " * (w - get_display_width(v)) for v, w in zip(row, col_widths))
        print(row_str)

def format_money(value):
    """원단위 숫자를 억 원 단위로 변환하여 보기 좋게 출력"""
    return f"{value / 100_000_000:,.0f}억"

def run_sfs_fundamental_pipeline():
    print("=" * 80)
    print("🌟 SFS(Sequential Factor Screening) 펀더멘털 엔진 가동 시작 🌟")
    print("=" * 80)

    # ---------------------------------------------------------
    # [환경 설정] 타겟 날짜 및 파라미터 세팅
    # ---------------------------------------------------------
    today_str = datetime.now().strftime('%Y%m%d') 
    target_date = stock.get_nearest_business_day(today_str) 
    min_cap = CUTOFFS.get('MarketCap_Min', 1_000_000_000_000)
    
    dart = OpenDartReader(os.getenv("DART_API_KEY"))

    # ---------------------------------------------------------
    # Step 1: 유니버스 추출 (시총 1조 이상)
    # ---------------------------------------------------------
    df_universe = get_sfs_universe(rebalance_date=target_date, min_market_cap=min_cap, market="KOSPI")
    
    if df_universe.empty:
        print("🚨 유니버스 추출 실패: 장이 열리지 않은 날이거나 데이터가 없습니다.")
        return None

    target_tickers = df_universe['종목코드'].tolist()

    # ---------------------------------------------------------
    # Step 2: DART/KRX 재무 데이터 동기화
    # ---------------------------------------------------------
    sfs_db, base_y, base_q = build_sfs_finance_db(dart, target_tickers, target_date)

    # ---------------------------------------------------------
    # Step 3: 퀄리티 및 밸류 원시 지표 산출
    # ---------------------------------------------------------
    df_quality_raw = calculate_quality_factors(target_tickers, target_date)
    df_value_raw = calculate_value_factors(target_tickers, target_date)

    # ---------------------------------------------------------
    # Step 4: 펀더멘털 통합 Z-스코어 산출 및 컷오프
    # ---------------------------------------------------------
    df_fundamental_top = calculate_fundamental_score(
        df_quality_raw, 
        df_value_raw
    )

    # ---------------------------------------------------------
    # Step 5: 모멘텀 팩터 원시 지표 산출 (펀더멘털 통과 종목 대상)
    # ---------------------------------------------------------
    surviving_tickers = df_fundamental_top['종목코드'].tolist()
    df_price_mom_raw = calculate_price_momentum_factors(surviving_tickers, target_date)
    df_op_mom_raw = calculate_op_momentum_factors(surviving_tickers, sfs_db, base_y, base_q)
    
    # ---------------------------------------------------------
    # Step 6: 모멘텀 통합 Z-스코어 산출 및 최종 컷오프
    # ---------------------------------------------------------
    df_momentum_top = calculate_momentum_score(df_price_mom_raw, df_op_mom_raw)
    
    # ---------------------------------------------------------
    # Step 7: 생존 종목 대상 Re-Z-scoring 및 최종 랭킹 산출
    # ---------------------------------------------------------
    df_merged = pd.merge(df_fundamental_top, df_momentum_top, on='종목코드', how='inner')
    df_final = calculate_final_re_zscore(df_merged)

    # ---------------------------------------------------------
    # 터미널용 디스플레이 데이터 가공 (원시 데이터 매핑)
    # ---------------------------------------------------------
    top10 = df_final.head(10).copy()
    
    # 밸류 DataFrame에서 PER, PBR 가져오기
    if 'PER' not in top10.columns or 'PBR' not in top10.columns:
        top10 = top10.merge(df_value_raw[['종목코드', 'PER', 'PBR']], on='종목코드', how='left')
    
    cur_qs = get_ttm_coords(base_y, base_q)
    latest_y, latest_q = cur_qs[0]
    
    op_list, assets_list, liab_list = [], [], []
    
    for tk in top10['종목코드']:
        actual_tk = get_common_ticker(tk)
        if actual_tk in sfs_db:
            db = sfs_db[actual_tk]
            ttm_op = sum(db.get(y, {}).get(q, {}).get('op', 0) for y, q in cur_qs)
            assets = db.get(latest_y, {}).get(latest_q, {}).get('assets', 0)
            liab = db.get(latest_y, {}).get(latest_q, {}).get('liabilities', 0)
            
            op_list.append(ttm_op)
            assets_list.append(assets)
            liab_list.append(liab)
        else:
            op_list.append(0), assets_list.append(0), liab_list.append(0)
            
    # 원시 지표 포매팅
    top10['영업이익(TTM)'] = [format_money(x) for x in op_list]
    top10['총자산'] = [format_money(x) for x in assets_list]
    top10['총부채'] = [format_money(x) for x in liab_list]
    top10['PER'] = top10['PER'].apply(lambda x: f"{x:.2f}" if x != 9999.0 else "N/A(적자)")
    top10['PBR'] = top10['PBR'].apply(lambda x: f"{x:.2f}" if x != 9999.0 else "N/A")
    
    # 💡 Z-스코어 포매팅 추가 (종합/팩터/섹션 모두 표시)
    top10['종합점수(Z)'] = top10['Grand_Total_Z'].apply(lambda x: f"{x:.2f}")
    
    top10['펀더(Z)'] = top10['Final_Fund_Z'].apply(lambda x: f"{x:.2f}")
    top10['모멘텀(Z)'] = top10['Final_Mom_Z'].apply(lambda x: f"{x:.2f}")
    
    top10['[퀄리티]'] = top10['Final_Q_Z'].apply(lambda x: f"{x:.2f}")
    top10['[밸류]'] = top10['Final_V_Z'].apply(lambda x: f"{x:.2f}")
    top10['[주가M]'] = top10['Final_P_Mom_Z'].apply(lambda x: f"{x:.2f}")
    top10['[OP가속]'] = top10['Final_OP_Mom_Z'].apply(lambda x: f"{x:.2f}")

    # ---------------------------------------------------------
    # 최종 결과 터미널 출력
    # ---------------------------------------------------------
    print("\n" + "🔥"*50)
    print(f"🏆 [최종 SFS 퀀트 랭킹 탑 10] (기준일: {target_date}, 시총 {min_cap}↑)")
    print("🔥"*50)
    
    # 출력 컬럼 지정
    display_cols = ['종목코드', '섹터', '종합점수(Z)', '펀더(Z)', '모멘텀(Z)', '[퀄리티]', '[밸류]', '[주가M]', '[OP가속]', 'PER', 'PBR']
    print_aligned_table(top10[display_cols])
    
    print("\n✅ SFS 파이프라인(펀더멘털+모멘텀)이 성공적으로 종료되었습니다.")
    return df_final

if __name__ == "__main__":
    start_time = time.time()
    final_df = run_sfs_fundamental_pipeline()
    end_time = time.time()
    print(f"⏱️ 총 소요 시간: {end_time - start_time:.2f} 초")