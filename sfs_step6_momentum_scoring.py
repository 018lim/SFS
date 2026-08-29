# 파일명: sfs_step6_momentum_scoring.py
import pandas as pd
from scipy.stats import zscore
from sfs_config import WEIGHTS, CUTOFFS

def calculate_momentum_score(df_price_raw, df_op_raw):
    """
    [SFS 6단계] 모멘텀 팩터 스코어링 및 컷오프
    - 주가/OP 모멘텀 원시 지표를 Z-스코어화
    - sfs_config.py에 정의된 지표 가중치 및 섹션 가중치를 적용하여 최종 Momentum_Total_Z 산출
    - 설정된 모멘텀 컷오프 비율(예: 50%)을 적용하여 반환
    """
    cutoff_ratio = CUTOFFS.get('Momentum_Ratio', 0.5)
    cutoff_percent = int(cutoff_ratio * 100)
    print(f"\n⚙️ [SFS 엔진] 모멘텀 통합 및 계층적 가중치 스코어링 가동 중 (컷오프 비율: 상위 {cutoff_percent}%)...")

    # ==========================================
    # 1. 원시 지표 Z-스코어화
    # ==========================================
    df_p = df_price_raw.copy()
    df_p['Z_Price_3M'] = zscore(df_p['Price_3M'], nan_policy='omit')
    df_p['Z_Price_6M'] = zscore(df_p['Price_6M'], nan_policy='omit')
    df_p['Z_Price_9M'] = zscore(df_p['Price_9M'], nan_policy='omit')
    df_p['Z_Price_12M'] = zscore(df_p['Price_12M'], nan_policy='omit')
    
    df_op = df_op_raw.copy()
    df_op['Z_OP_3M'] = zscore(df_op['OP_3M'], nan_policy='omit')
    df_op['Z_OP_6M'] = zscore(df_op['OP_6M'], nan_policy='omit')
    df_op['Z_OP_9M'] = zscore(df_op['OP_9M'], nan_policy='omit')
    df_op['Z_OP_12M'] = zscore(df_op['OP_12M'], nan_policy='omit')

    # 결측치 페널티(-0.5) 부여
    z_cols_p = ['Z_Price_3M', 'Z_Price_6M', 'Z_Price_9M', 'Z_Price_12M']
    df_p[z_cols_p] = df_p[z_cols_p].fillna(-0.5)
    
    z_cols_op = ['Z_OP_3M', 'Z_OP_6M', 'Z_OP_9M', 'Z_OP_12M']
    df_op[z_cols_op] = df_op[z_cols_op].fillna(-0.5)

    # ==========================================
    # 2. 데이터프레임 병합 및 계층적 비중(Weight) 적용
    # ==========================================
    df_merged = pd.merge(df_p, df_op, on='종목코드', how='inner')

    # 가중치 딕셔너리 로드
    w_p_ind = WEIGHTS['Indicators'].get('Price_Momentum', {'3M': 0.25, '6M': 0.25, '9M': 0.25, '12M': 0.25})
    w_op_ind = WEIGHTS['Indicators'].get('OP_Momentum', {'3M': 0.25, '6M': 0.25, '9M': 0.25, '12M': 0.25})
    w_sec = WEIGHTS.get('Momentum_Sections', {'Price': 0.5, 'OP': 0.5})

    def apply_momentum_weights(row):
        # 주가 모멘텀(Price) 섹션 점수
        p_score = (row['Z_Price_3M'] * w_p_ind.get('3M', 0.25) +
                   row['Z_Price_6M'] * w_p_ind.get('6M', 0.25) +
                   row['Z_Price_9M'] * w_p_ind.get('9M', 0.25) +
                   row['Z_Price_12M'] * w_p_ind.get('12M', 0.25))
                   
        # 영업이익 가속도(OP) 섹션 점수
        op_score = (row['Z_OP_3M'] * w_op_ind.get('3M', 0.25) +
                    row['Z_OP_6M'] * w_op_ind.get('6M', 0.25) +
                    row['Z_OP_9M'] * w_op_ind.get('9M', 0.25) +
                    row['Z_OP_12M'] * w_op_ind.get('12M', 0.25))
        
        # 통합 모멘텀 팩터 점수
        total_score = (p_score * w_sec.get('Price', 0.5)) + (op_score * w_sec.get('OP', 0.5))
        return pd.Series([p_score, op_score, total_score])

    df_merged[['Price_Momentum_Z', 'OP_Momentum_Z', 'Momentum_Total_Z']] = df_merged.apply(apply_momentum_weights, axis=1)

    # ==========================================
    # 3. 모멘텀 상위 N% 컷오프 및 정렬
    # ==========================================
    df_sorted = df_merged.sort_values(by='Momentum_Total_Z', ascending=False).reset_index(drop=True)
    
    cutoff_idx = max(1, int(len(df_sorted) * cutoff_ratio))
    df_top = df_sorted.iloc[:cutoff_idx].copy()

    print(f"✅ 모멘텀 연산 대상 {len(df_sorted)}종목 중 상위 {cutoff_percent}% ({len(df_top)}종목) 컷오프 완료!")
    return df_top
