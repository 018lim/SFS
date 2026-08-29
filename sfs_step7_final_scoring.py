# 파일명: sfs_step7_final_scoring.py
import pandas as pd
import numpy as np
from scipy.stats import zscore
from sfs_config import WEIGHTS

def calculate_final_re_zscore(df_survivors):
    """
    [SFS 7단계] 생존자 그룹 대상 최종 Re-Z-scoring 및 종합 랭킹 산출
    - 모멘텀 컷오프까지 살아남은 최정예 종목들만을 대상으로 모든 원시 지표를 다시 Z-스코어화합니다.
    - sfs_config.py의 가중치를 적용하여 섹션별, 팩터별, 종합 스코어를 산출합니다.
    """
    print(f"\n🏆 [SFS 엔진] 최종 생존 {len(df_survivors)}종목 대상 Re-Z-scoring 가동 중...")
    
    df = df_survivors.copy()
    
    # ==========================================
    # 1. 펀더멘털 원시 지표 Re-Z-scoring
    # ==========================================
    # Quality
    df['ReZ_OP/A'] = zscore(df['OP/A(%)'], nan_policy='omit')
    df['ReZ_환원금인상률'] = zscore(df['환원금인상률(%)'], nan_policy='omit')
    
    df['ReZ_발생액'] = np.nan
    df['ReZ_FCF/부채'] = np.nan
    df['ReZ_ROE'] = np.nan

    mask_q_gen = df['섹터'] == '일반기업'
    if mask_q_gen.sum() > 0:
        df.loc[mask_q_gen, 'ReZ_발생액'] = -zscore(df.loc[mask_q_gen, '발생액/자산(%)'], nan_policy='omit')
        df.loc[mask_q_gen, 'ReZ_FCF/부채'] = zscore(df.loc[mask_q_gen, 'FCF/총부채(%)'], nan_policy='omit')

    mask_q_fin = df['섹터'] == '금융기업' 
    if mask_q_fin.sum() > 0:
        df.loc[mask_q_fin, 'ReZ_ROE'] = zscore(df.loc[mask_q_fin, 'ROE(%)'], nan_policy='omit')

    # Value
    df['ReZ_PER'] = -zscore(df['PER'], nan_policy='omit')
    df['ReZ_PBR'] = -zscore(df['PBR'], nan_policy='omit')
    df['ReZ_주주수익률'] = zscore(df['주주수익률(%)'], nan_policy='omit')
    
    df['ReZ_PCR'] = np.nan
    if mask_q_gen.sum() > 0:
        df.loc[mask_q_gen, 'ReZ_PCR'] = -zscore(df.loc[mask_q_gen, 'PCR'], nan_policy='omit')

    # ==========================================
    # 2. 모멘텀 원시 지표 Re-Z-scoring
    # ==========================================
    df['ReZ_Price_3M'] = zscore(df['Price_3M'], nan_policy='omit')
    df['ReZ_Price_6M'] = zscore(df['Price_6M'], nan_policy='omit')
    df['ReZ_Price_9M'] = zscore(df['Price_9M'], nan_policy='omit')
    df['ReZ_Price_12M'] = zscore(df['Price_12M'], nan_policy='omit')
    
    df['ReZ_OP_3M'] = zscore(df['OP_3M'], nan_policy='omit')
    df['ReZ_OP_6M'] = zscore(df['OP_6M'], nan_policy='omit')
    df['ReZ_OP_9M'] = zscore(df['OP_9M'], nan_policy='omit')
    df['ReZ_OP_12M'] = zscore(df['OP_12M'], nan_policy='omit')

    # 결측치 페널티(-0.5) 부여
    z_cols = [
        'ReZ_OP/A', 'ReZ_환원금인상률', 'ReZ_발생액', 'ReZ_FCF/부채', 'ReZ_ROE',
        'ReZ_PER', 'ReZ_PBR', 'ReZ_주주수익률', 'ReZ_PCR',
        'ReZ_Price_3M', 'ReZ_Price_6M', 'ReZ_Price_9M', 'ReZ_Price_12M',
        'ReZ_OP_3M', 'ReZ_OP_6M', 'ReZ_OP_9M', 'ReZ_OP_12M'
    ]
    df[z_cols] = df[z_cols].fillna(-0.5)

    # ==========================================
    # 3. 계층적 가중치(Weight) 재적용
    # ==========================================
    f_sections = WEIGHTS.get('Fundamental_Sections', {'Quality': 0.5, 'Value': 0.5})
    w_q_ind = WEIGHTS['Indicators'].get('Quality', {})
    w_v_ind = WEIGHTS['Indicators'].get('Value', {})
    
    w_p_ind = WEIGHTS['Indicators'].get('Price_Momentum', {'3M': 0.25, '6M': 0.25, '9M': 0.25, '12M': 0.25})
    w_op_ind = WEIGHTS['Indicators'].get('OP_Momentum', {'3M': 0.25, '6M': 0.25, '9M': 0.25, '12M': 0.25})
    w_m_sections = WEIGHTS.get('Momentum_Sections', {'Price': 0.5, 'OP': 0.5})
    
    w_factors = WEIGHTS.get('Factors', {'Fundamental': 1.0, 'Momentum': 1.0})

    def apply_final_weights(row):
        sector = row['섹터']
        
        # 펀더멘털 스코어 재산출
        if sector == '일반기업':
            w_q = w_q_ind.get('일반기업', {'Z_OP/A': 0.25, 'Z_환원금인상률': 0.25, 'Z_발생액': 0.25, 'Z_FCF/부채': 0.25})
            q_score = sum(row[k.replace('Z_', 'ReZ_')] * v for k, v in w_q.items())
            
            w_v = w_v_ind.get('일반기업', {'Z_PER': 0.25, 'Z_PBR': 0.25, 'Z_PCR': 0.25, 'Z_주주수익률': 0.25})
            v_score = sum(row[k.replace('Z_', 'ReZ_')] * v for k, v in w_v.items())
        else: 
            w_q = w_q_ind.get('금융기업', {'Z_OP/A': 1/3, 'Z_환원금인상률': 1/3, 'Z_ROE': 1/3})
            q_score = sum(row[k.replace('Z_', 'ReZ_')] * v for k, v in w_q.items())
            
            w_v = w_v_ind.get('금융기업', {'Z_PER': 0.25, 'Z_PBR': 0.25, 'Z_주주수익률': 0.50})
            v_score = sum(row[k.replace('Z_', 'ReZ_')] * v for k, v in w_v.items())

        fund_total = (q_score * f_sections.get('Quality', 0.5)) + (v_score * f_sections.get('Value', 0.5))
        
        # 모멘텀 스코어 재산출
        p_score = (row['ReZ_Price_3M'] * w_p_ind.get('3M', 0.25) +
                   row['ReZ_Price_6M'] * w_p_ind.get('6M', 0.25) +
                   row['ReZ_Price_9M'] * w_p_ind.get('9M', 0.25) +
                   row['ReZ_Price_12M'] * w_p_ind.get('12M', 0.25))
                   
        op_score = (row['ReZ_OP_3M'] * w_op_ind.get('3M', 0.25) +
                    row['ReZ_OP_6M'] * w_op_ind.get('6M', 0.25) +
                    row['ReZ_OP_9M'] * w_op_ind.get('9M', 0.25) +
                    row['ReZ_OP_12M'] * w_op_ind.get('12M', 0.25))
                    
        mom_total = (p_score * w_m_sections.get('Price', 0.5)) + (op_score * w_m_sections.get('OP', 0.5))
        
        # 종합 스코어 재산출
        grand_total = (fund_total * w_factors.get('Fundamental', 1.0)) + (mom_total * w_factors.get('Momentum', 1.0))
        
        return pd.Series([q_score, v_score, fund_total, p_score, op_score, mom_total, grand_total])

    df[['Final_Q_Z', 'Final_V_Z', 'Final_Fund_Z', 'Final_P_Mom_Z', 'Final_OP_Mom_Z', 'Final_Mom_Z', 'Grand_Total_Z']] = df.apply(apply_final_weights, axis=1)

    # 내림차순 정렬
    df_sorted = df.sort_values(by='Grand_Total_Z', ascending=False).reset_index(drop=True)
    return df_sorted
