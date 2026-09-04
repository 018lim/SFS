# 파일명: check_finance_data.py
"""
지정한 종목·기간의 재무 데이터를 조회해 콘솔에 표로 보여준다.

운영 DB(raw_finance_data.json)는 건드리지 않는다 - 이 실행에서만 쓰는 로컬 딕셔너리에
분기값을 채워가며, sfs_step2_data_builder.py의 검증된 로직(API 우선, 금융업 공백 분기는
공시원문으로 대체, 4분기는 저장된 Q1~Q3로 계산)을 그대로 재사용한다.

종목/기간을 바꾸려면 아래 TICKERS, FIRST_YQ, LAST_YQ만 고치면 된다.
"""
import os
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from dotenv import load_dotenv
load_dotenv()
import OpenDartReader

# sfs_step2_data_builder를 import하면 requests.get이 세션으로 교체된다
# (매 요청 새 소켓을 여는 데서 오는 WinError 10048 포트 고갈 방지)
from sfs_utils import (
    get_category, get_stock_snapshot, get_discrete_is, clear_finstate_cache
)
import sfs_step2_data_builder as S
import sfs_dart_html as dart_html

TICKERS = [('005930', '삼성전자'), ('105560', 'KB금융')]
FIRST_YQ, LAST_YQ = (2021, 1), (2025, 4)


def fmt(v):
    if v is None: return "     N/A"
    return f"{v/1_000_000:>10,.0f}"


def fmt_na(v):
    """일반기업 전용 항목(ocf/capex)을 금융기업 행에서 '해당없음'으로 표시"""
    if v is None: return "       ―"
    return f"{v/1_000_000:>10,.0f}"


def collect_quarter(dart, ticker, category, y, q, db_tk):
    """분기 하나의 값을 API로 채운다. 구할 수 없는 항목은 None으로 남는다.

    현금흐름표 항목(ocf/capex/dividend/buyback)은 S.discrete_cf를 쓴다 - 중간 분기가
    API 공백이면(금융업 2023Q1~Q2 등) 직접 차감이 실패하는데, db_tk에 이미 저장된
    앞 분기 값으로 대신 구할 수 있어서다(운영 파이프라인과 동일한 로직).
    """
    cur = {}
    cur['assets'] = get_stock_snapshot(dart, ticker, y, q, 'assets')
    cur['liabilities'] = get_stock_snapshot(dart, ticker, y, q, 'liabilities')
    cur['equity'] = get_stock_snapshot(dart, ticker, y, q, 'equity')
    cur['op'] = get_discrete_is(dart, ticker, y, q, 'op')
    cur['ni'] = get_discrete_is(dart, ticker, y, q, 'ni')
    if category == '일반기업':
        cur['ocf'] = S.discrete_cf(dart, ticker, y, q, 'ocf', db_tk)
        cur['capex'] = S.discrete_cf(dart, ticker, y, q, 'capex', db_tk)
    cur['dividend'] = S.discrete_cf(dart, ticker, y, q, 'dividend', db_tk)
    cur['buyback'] = S.discrete_cf(dart, ticker, y, q, 'buyback', db_tk)
    return cur


def main():
    dart = OpenDartReader(os.getenv("DART_API_KEY"))
    quarters = S.get_required_quarters(LAST_YQ[0], LAST_YQ[1],
                                       total_depth=S.quarter_depth(LAST_YQ[0], LAST_YQ[1], *FIRST_YQ) + 1)

    for ticker, name in TICKERS:
        category = get_category(dart, ticker)
        print("\n" + "=" * 150)
        print(f"[{ticker} / {name}] category={category}  "
              f"{FIRST_YQ[0]}Q{FIRST_YQ[1]} ~ {LAST_YQ[0]}Q{LAST_YQ[1]}")
        print("=" * 150)
        print(f"{'기간':>8} | {'src':>4} | {'자산총계':>10} | {'부채총계':>10} | {'자본총계':>10} | "
              f"{'OP':>10} | {'NI':>10} | {'OCF':>10} | {'CAPEX':>10} | {'배당':>10} | {'자사주':>10}")
        print("-" * 150)

        db_tk = {}
        html_reports = None
        for y, q in quarters:
            str_y, str_q = str(y), str(q)
            db_tk.setdefault(str_y, {})

            cur = collect_quarter(dart, ticker, category, y, q, db_tk)
            src = 'api'

            # 금융업이 finstate_all 공백 구간(2023Q3 이전)에 걸리면 공시원문으로 대체
            if category == '금융기업' and S.is_api_gap(cur):
                if html_reports is None:
                    html_reports = dart_html.load_reports(dart, ticker, FIRST_YQ[0], LAST_YQ[0])
                html_vals = dart_html.get_html_quarter_values(dart, ticker, y, q, html_reports)
                if html_vals and not S.is_bs_empty(html_vals):
                    for k in S.HTML_KEYS:
                        if html_vals.get(k) is not None:
                            cur[k] = html_vals[k]
                    src = 'html'

            db_tk[str_y][str_q] = cur

            ocf_col = fmt(cur.get('ocf')) if category == '일반기업' else fmt_na(None)
            capex_col = fmt(cur.get('capex')) if category == '일반기업' else fmt_na(None)
            print(f"  {y}Q{q} | {src:>4} | {fmt(cur['assets']):>10} | {fmt(cur['liabilities']):>10} | "
                  f"{fmt(cur['equity']):>10} | {fmt(cur['op']):>10} | {fmt(cur['ni']):>10} | "
                  f"{ocf_col:>10} | {capex_col:>10} | {fmt(cur['dividend']):>10} | {fmt(cur['buyback']):>10}")

        clear_finstate_cache()
        dart_html.clear_html_cache()

    print("\n(단위: 백만원. src=html은 공시 원문에서 가져온 분기)")
    print("[DONE]")


if __name__ == "__main__":
    main()
