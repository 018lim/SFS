# 파일명: sfs_step4_fundamental_scoring.py
import pandas as pd
import numpy as np
from scipy.stats import zscore
from sfs_utils import get_common_ticker
from sfs_config import WEIGHTS, CUTOFFS

def calculate_fundamental_score(df_quality_raw, df_value_raw):
    """
    [SFS 4단계] 펀더멘털 통합 및 계층적 가중치 스코어링 엔진
    - 퀄리티와 밸류의 원시 지표를 입력받아 각각 알맞은 Z-스코어로 정규화합니다.
    - sfs_config.py에 지정된 계층적 비중을 적용하여 최종 점수를 산출합니다.
    - CUTOFFS 기준으로 상위 N% 종목을 컷오프하여 반환합니다.
    """
    cutoff_ratio = CUTOFFS.get('Fundamental_Ratio', 0.5)
    cutoff_percent = int(cutoff_ratio * 100)
    print(f"\n⚙️ [SFS 엔진] 펀더멘털 통합 및 계층적 가중치 스코어링 가동 중 (컷오프 비율: 상위 {cutoff_percent}%)...")

    # ==========================================
    # 1. 퀄리티 Z-스코어 연산 (본주 분리 ➔ 연산 ➔ 우선주 매핑)
    # ==========================================
    df_q_common = df_quality_raw[df_quality_raw['구분'] == '본주'].copy()
    
    df_q_common['Z_OP/A'] = zscore(df_q_common['OP/A(%)'], nan_policy='omit')
    df_q_common['Z_환원금인상률'] = zscore(df_q_common['환원금인상률(%)'], nan_policy='omit')
    
    df_q_common['Z_발생액'] = np.nan
    df_q_common['Z_FCF/부채'] = np.nan
    df_q_common['Z_ROE'] = np.nan

    mask_q_gen = df_q_common['섹터'] == '일반기업'
    if mask_q_gen.sum() > 0:
        df_q_common.loc[mask_q_gen, 'Z_발생액'] = -zscore(df_q_common.loc[mask_q_gen, '발생액/자산(%)'], nan_policy='omit')
        df_q_common.loc[mask_q_gen, 'Z_FCF/부채'] = zscore(df_q_common.loc[mask_q_gen, 'FCF/총부채(%)'], nan_policy='omit')

    mask_q_fin = df_q_common['섹터'] == '금융기업' 
    if mask_q_fin.sum() > 0:
        df_q_common.loc[mask_q_fin, 'Z_ROE'] = zscore(df_q_common.loc[mask_q_fin, 'ROE(%)'], nan_policy='omit')

    score_cols_q = ['Z_OP/A', 'Z_환원금인상률', 'Z_발생액', 'Z_FCF/부채', 'Z_ROE']
    q_map = df_q_common.set_index('종목코드')[score_cols_q]
    
    df_q = df_quality_raw.copy()
    df_q['본주코드'] = df_q['종목코드'].apply(get_common_ticker)
    df_q = df_q.merge(q_map, left_on='본주코드', right_index=True, how='left')
    df_q.drop(columns=['본주코드'], inplace=True)

    # ==========================================
    # 2. 밸류 Z-스코어 연산 (우선주 포함 전체 직접 경쟁)
    # ==========================================
    df_v = df_value_raw.copy()
    
    df_v['Z_PER'] = -zscore(df_v['PER'], nan_policy='omit')
    df_v['Z_PBR'] = -zscore(df_v['PBR'], nan_policy='omit')
    df_v['Z_주주수익률'] = zscore(df_v['주주수익률(%)'], nan_policy='omit')
    
    df_v['Z_PCR'] = np.nan
    mask_v_gen = df_v['섹터'] == '일반기업'
    if mask_v_gen.sum() > 0:
        df_v.loc[mask_v_gen, 'Z_PCR'] = -zscore(df_v.loc[mask_v_gen, 'PCR'], nan_policy='omit')

    # ==========================================
    # 2.5 결측치 Z-스코어 페널티(-0.5) 부여
    # ==========================================
    z_cols_q = ['Z_OP/A', 'Z_환원금인상률', 'Z_발생액', 'Z_FCF/부채', 'Z_ROE']
    df_q[z_cols_q] = df_q[z_cols_q].fillna(-0.5)
    
    z_cols_v = ['Z_PER', 'Z_PBR', 'Z_PCR', 'Z_주주수익률']
    df_v[z_cols_v] = df_v[z_cols_v].fillna(-0.5)

    # ==========================================
    # 3. 데이터프레임 병합 및 계층적 비중(Weight) 적용
    # ==========================================
    cols_to_merge = [c for c in df_v.columns if c not in ['구분', '섹터']]
    df_merged = pd.merge(df_q, df_v[cols_to_merge], on='종목코드', how='inner')

    f_sections = WEIGHTS.get('Fundamental_Sections', {'Quality': 0.5, 'Value': 0.5})
    w_q_ind = WEIGHTS['Indicators'].get('Quality', {})
    w_v_ind = WEIGHTS['Indicators'].get('Value', {})

    def apply_hierarchical_weights(row):
        sector = row['섹터']
        
        if sector == '일반기업':
            w_q = w_q_ind.get('일반기업', {'Z_OP/A': 0.25, 'Z_환원금인상률': 0.25, 'Z_발생액': 0.25, 'Z_FCF/부채': 0.25})
            q_score = sum(row[k] * v for k, v in w_q.items())
            
            w_v = w_v_ind.get('일반기업', {'Z_PER': 0.25, 'Z_PBR': 0.25, 'Z_PCR': 0.25, 'Z_주주수익률': 0.25})
            v_score = sum(row[k] * v for k, v in w_v.items())
            
        else: 
            w_q = w_q_ind.get('금융기업', {'Z_OP/A': 1/3, 'Z_환원금인상률': 1/3, 'Z_ROE': 1/3})
            q_score = sum(row[k] * v for k, v in w_q.items())
            
            w_v = w_v_ind.get('금융기업', {'Z_PER': 0.25, 'Z_PBR': 0.25, 'Z_주주수익률': 0.50})
            v_score = sum(row[k] * v for k, v in w_v.items())

        total_score = (q_score * f_sections.get('Quality', 0.5)) + (v_score * f_sections.get('Value', 0.5))
        return pd.Series([q_score, v_score, total_score])

    df_merged[['Quality_Z', 'Value_Z', 'Fundamental_Total_Z']] = df_merged.apply(apply_hierarchical_weights, axis=1)

    # ==========================================
    # 4. 상위 N% 컷오프 및 정렬
    # ==========================================
    df_sorted = df_merged.sort_values(by='Fundamental_Total_Z', ascending=False).reset_index(drop=True)
    
    # 커스텀 컷오프 비율 적용 (최소 1종목 반환 보장)
    cutoff_idx = max(1, int(len(df_sorted) * cutoff_ratio))
    df_top = df_sorted.iloc[:cutoff_idx].copy()

    print(f"✅ 전체 {len(df_sorted)}종목 중 펀더멘털 상위 {cutoff_percent}% ({len(df_top)}종목) 컷오프 완료!")
    return df_top

if __name__ == "__main__":
    from sfs_step3_quality import calculate_quality_factors
    from sfs_step3_value import calculate_value_factors
    from datetime import datetime
    
    test_tickers = ['005930', '005935', '105560'] 
    test_date = datetime.now().strftime('%Y%m%d')
    
    df_raw_q = calculate_quality_factors(test_tickers, test_date)
    df_raw_v = calculate_value_factors(test_tickers, test_date)
    
    df_fundamental_top = calculate_fundamental_score(
        df_raw_q, 
        df_raw_v
    )
    
    print("\n" + "="*90)
    print("🏆 [최종 SFS 펀더멘털 통과 리스트]")
    print("="*90)
    # pass