# 파일명: sfs_step2_data_builder.py
import os
import time
import pandas as pd
from tqdm import tqdm

# 🔥 [궁극의 해결책] OpenDartReader 소켓 폭주 원천 차단 (Session 강제 고정)
# ⚠️ 주의: requests.get을 전역적으로 재할당합니다. 이 모듈을 임포트하면 모든 HTTP GET 요청이 단일 세션을 공유합니다.
import requests
sfs_session = requests.Session()
requests.get = sfs_session.get
# -----------------------------------------------------------------------

from sfs_krx_api import stock
from sfs_config import DB_FILE_PATH
from sfs_utils import (
    get_common_ticker, get_category, get_last_business_day,
    get_stock_snapshot, get_discrete_is, get_discrete_cf,
    clear_finstate_cache, get_latest_available_quarter,
    load_local_db, save_local_db
)

def get_required_quarters(base_y, base_q, total_depth=12):
    """기준 분기로부터 과거 N개 분기의 좌표(연도, 분기) 리스트 생성"""
    base_abs_q = base_y * 4 + base_q
    coords = []
    for i in range(total_depth):
        target_abs = base_abs_q - i
        y = (target_abs - 1) // 4
        q = ((target_abs - 1) % 4) + 1
        coords.append((y, q))
    return coords

def build_sfs_finance_db(dart_api, target_tickers, rebalance_date):
    """
    [SFS 2단계] 로컬 JSON DB를 확인하고, 부족한 데이터만 DART API로 핀셋 업데이트하는 모듈
    """
    base_y, base_q = get_latest_available_quarter(rebalance_date)
    print(f"\n🚀 [SFS 엔진] {rebalance_date} 기준 재무 데이터 동기화 가동")
    print(f" 🔹 타겟 최신 분기: {base_y}년 {base_q}분기 (공시 마감 룰 적용)")
    
    # 1. 기존 로컬 DB 로드
    sfs_db = load_local_db(DB_FILE_PATH)
    print(f" 🔹 로컬 DB 로드 완료: {DB_FILE_PATH}")
    
    required_coords = get_required_quarters(base_y, base_q, total_depth=12)
    is_updated = False 
    
    # 💡 [핵심] 타겟 유니버스에 우선주가 섞여 있어도 본주로 통일하여 중복 제거 (Set 활용)
    common_tickers = list(set(get_common_ticker(tk) for tk in target_tickers))
    
    # tqdm을 적용하여 깔끔한 진행 상태 확인
    for actual_tk in tqdm(common_tickers, desc="데이터 동기화 진행률"):
        # 🛡️ [에러 방어 블록 시작] 특정 종목에서 에러가 발생해도 전체가 멈추지 않음
        try:
            name = stock.get_market_ticker_name(actual_tk)
            
            # 신규 종목 추가 처리
            if actual_tk not in sfs_db:
                sfs_db[actual_tk] = {}
                sfs_db[actual_tk]["category"] = get_category(dart_api, actual_tk)
                time.sleep(0.1)
                is_updated = True
                
            # tqdm 내부에서는 print 대신 tqdm.write를 써야 출력이 깨지지 않습니다.
            tqdm.write(f"\n[{actual_tk} / {name}] 데이터 무결성 검사 중...")
            
            for idx, (y, q) in enumerate(required_coords):
                str_y = str(y) 
                str_q = str(q)
                
                # Key 껍데기 세팅
                if str_y not in sfs_db[actual_tk]:
                    sfs_db[actual_tk][str_y] = {}
                if str_q not in sfs_db[actual_tk][str_y]:
                    sfs_db[actual_tk][str_y][str_q] = {}
                    
                current_data = sfs_db[actual_tk][str_y][str_q]
                q_data = sfs_db[actual_tk][str_y].get(str_q)
                category = sfs_db[actual_tk].get("category", "일반기업")
                
                # [Tier 1] 최근 5분기 : 퀄리티/밸류 풀세트
                if idx < 5:
                    if category == "금융기업":
                        required_keys = ['assets', 'liabilities', 'equity', 'ni', 'return', 'op', 'shares']
                    else:
                        required_keys = ['assets', 'liabilities', 'equity', 'ni', 'ocf', 'capex', 'return', 'op', 'shares']
                # [Tier 2] 과거 7분기 : 모멘텀 전용
                else:
                    required_keys = ['op', 'shares']
                    
                if q_data is None:
                    missing_keys = required_keys
                else:
                    # 💡 값이 0이어도 데이터가 있는 것으로 간주하여 불필요한 반복 다운로드를 방지합니다.
                    missing_keys = [
                        k for k in required_keys 
                        if k not in q_data 
                        or q_data.get(k) is None 
                    ]
                
                # 누락된 데이터가 없으면 즉시 통과 (속도 극대화)
                if not missing_keys:
                    continue
                    
                # (조기 종료 방어 로직 제거됨 - 무조건 끝까지 탐색하여 최대한 데이터 확보)
                    
                tqdm.write(f"  ➔ {y}년 {q}분기 누락 데이터 다운로드: {missing_keys}")
                
                # 핀셋 다운로드 로직 (이제 API가 아니라 메모리 캐시에서 1초 만에 가져옵니다!)
                if 'assets' in missing_keys: current_data['assets'] = get_stock_snapshot(dart_api, actual_tk, y, q, 'assets')
                if 'liabilities' in missing_keys: current_data['liabilities'] = get_stock_snapshot(dart_api, actual_tk, y, q, 'liabilities')
                if 'equity' in missing_keys: current_data['equity'] = get_stock_snapshot(dart_api, actual_tk, y, q, 'equity')
                
                if 'ni' in missing_keys: current_data['ni'] = get_discrete_is(dart_api, actual_tk, y, q, 'ni')
                if 'op' in missing_keys: current_data['op'] = get_discrete_is(dart_api, actual_tk, y, q, 'op')
                
                if 'ocf' in missing_keys: current_data['ocf'] = get_discrete_cf(dart_api, actual_tk, y, q, 'ocf')
                if 'capex' in missing_keys: current_data['capex'] = get_discrete_cf(dart_api, actual_tk, y, q, 'capex')
                if 'return' in missing_keys: current_data['return'] = get_discrete_cf(dart_api, actual_tk, y, q, 'return')
                
                if 'shares' in missing_keys:
                    month_map = {1: 3, 2: 6, 3: 9, 4: 12}
                    target_date_raw = get_last_business_day(y, month_map[q])
                    target_date = stock.get_nearest_business_day(target_date_raw)
                    try:
                        hist_cap = stock.get_market_cap(target_date)
                        prefix = actual_tk[:-1]
                        matching_tks = [t for t in hist_cap.index if t.startswith(prefix)]
                        current_data['shares'] = sum(float(hist_cap.loc[t, '상장주식수']) for t in matching_tks)
                    except Exception:
                        current_data['shares'] = 0.0
                        
                is_updated = True
                
                # 속도가 비약적으로 빨라졌으므로 딜레이를 0.1초로 줄임
                time.sleep(0.1)

            # 💡 [주식수 후처리] shares가 0인 경우 앞뒤 분기 데이터로 채우기 (Forward & Backward Fill)
            if actual_tk in sfs_db:
                # 연도와 분기를 시계열 순서로 정렬 (과거 -> 최신)
                quarters_data = []
                for y_str, q_dict in sfs_db[actual_tk].items():
                    if not isinstance(q_dict, dict):
                        continue
                    for q_str, q_data in q_dict.items():
                        if not isinstance(q_data, dict):
                            continue
                        quarters_data.append((int(y_str), int(q_str), q_data))
                quarters_data.sort(key=lambda x: (x[0], x[1]))
                
                # 1. Forward Fill (과거 -> 최신) - 전 분기 값을 가져옴
                last_valid_shares = 0.0
                for y_int, q_int, q_data in quarters_data:
                    current_shares = float(q_data.get('shares', 0.0))
                    if current_shares > 0.0:
                        last_valid_shares = current_shares
                    elif last_valid_shares > 0.0:
                        q_data['shares'] = last_valid_shares
                        is_updated = True
                        
                # 2. Backward Fill (최신 -> 과거) - 혹시 가장 과거 분기가 0이었다면 최근 분기 값으로 채움
                last_valid_shares = 0.0
                for y_int, q_int, q_data in reversed(quarters_data):
                    current_shares = float(q_data.get('shares', 0.0))
                    if current_shares > 0.0:
                        last_valid_shares = current_shares
                    elif last_valid_shares > 0.0:
                        q_data['shares'] = last_valid_shares
                        is_updated = True

            # 💡 [메모리 최적화] 해당 기업의 12분기 순회가 끝나면 캐시(RAM)를 깔끔하게 비움
            clear_finstate_cache()

        # 🛡️ [에러 방어 1] DART 미등록 기업 (0126Z0 등) 처리
        except ValueError as e:
            if 'could not find' in str(e):
                tqdm.write(f" ⚠️ [{actual_tk}] DART 공시 미등록 종목 ➔ 자동 패스")
            else:
                tqdm.write(f" 🚨 [{actual_tk}] 데이터 에러: {e}")
            clear_finstate_cache()
            continue
            
        # 🛡️ [에러 방어 2] 기타 알 수 없는 에러 처리
        except Exception as e:
            tqdm.write(f" 🚨 [{actual_tk}] 알 수 없는 예외 발생: {e}")
            clear_finstate_cache()
            continue

    # 2. 업데이트된 내역이 있다면 로컬 DB 덮어쓰기
    if is_updated:
        save_local_db(sfs_db, DB_FILE_PATH)
        print(f"\n✅ [동기화 완료] 누락된 데이터를 보완하여 {DB_FILE_PATH} 에 저장했습니다.")
    else:
        print(f"\n✅ [동기화 완료] 로컬 DB가 최신 상태입니다. (API 호출 0회)")
        
    return sfs_db, base_y, base_q

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    import OpenDartReader

    dart = OpenDartReader(os.getenv("DART_API_KEY"))
    sample_tickers = ['005930', '005935', '105560', '0126Z0']  # 에러 유발 종목(0126Z0) 포함 테스트
    test_date = '20260826'

    finance_db, b_y, b_q = build_sfs_finance_db(dart, sample_tickers, test_date)