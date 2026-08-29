# -*- coding: utf-8 -*-
# 방안C 종합 테스트: 연결재무제표(BS/IS) + 별도재무제표(CF) + YTD→분기
import os, sys, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from dotenv import load_dotenv
load_dotenv()
import OpenDartReader
import requests
from bs4 import BeautifulSoup

dart = OpenDartReader(os.getenv("DART_API_KEY"))

def find_report_rcp(ticker, year, quarter):
    month_map = {1: f'{year:04d}.03', 2: f'{year:04d}.06', 3: f'{year:04d}.09', 4: f'{year:04d}.12'}
    q_search = {
        1: (f'{year}-04-01', f'{year}-07-31'),
        2: (f'{year}-07-01', f'{year}-11-30'),
        3: (f'{year}-10-01', f'{year+1}-01-31'),
        4: (f'{year+1}-01-01', f'{year+1}-06-30'),
    }
    start, end = q_search[quarter]
    target_month = month_map[quarter]
    reports = dart.list(ticker, start=start, end=end, kind='A', final=False)
    if reports is None or reports.empty: return None, None
    matched = reports[reports['report_nm'].str.contains(target_month, na=False)]
    if not matched.empty:
        orig = matched[~matched['report_nm'].str.contains('기재정정')]
        if not orig.empty: return orig.iloc[0]['rcept_no'], orig.iloc[0]['report_nm']
        return matched.iloc[0]['rcept_no'], matched.iloc[0]['report_nm']
    q_name = {1: '1분기보고서', 2: '반기보고서', 3: '3분기보고서', 4: '사업보고서'}
    matched = reports[reports['report_nm'].str.contains(q_name[quarter])]
    if not matched.empty: return matched.iloc[0]['rcept_no'], matched.iloc[0]['report_nm']
    return None, None

def find_sub_doc_urls(rcp_no):
    """연결재무제표 URL + 별도 재무제표 URL 찾기"""
    sub = dart.sub_docs(rcp_no)
    
    # 연결재무제표 (BS/IS)
    consolidated = sub[sub['title'].str.contains('연결재무제표', na=False)]
    con_url = consolidated.iloc[0]['url'] if not consolidated.empty else None
    
    # 별도 재무제표 (CF - 배당/자사주)
    # '재무제표'를 포함하지만 '연결'은 포함하지 않는 문서
    standalone = sub[
        sub['title'].str.contains('재무제표', na=False) & 
        ~sub['title'].str.contains('연결|주석', na=False)
    ]
    std_url = standalone.iloc[0]['url'] if not standalone.empty else None
    
    return con_url, std_url, sub

def parse_amount(text):
    text = text.replace(',', '').replace(' ', '').replace('\xa0', '').strip()
    if not text or text == '-': return None
    if any(c in text for c in ['단', '위', '백', '만', '원', '억', ':', '주']): return None
    if text.startswith('(') and text.endswith(')'):
        try: return -float(text[1:-1])
        except: return None
    try: return float(text)
    except: return None

def first_valid_amount(cells):
    for cell in cells[1:]:
        amt = parse_amount(cell.get_text(strip=True))
        if amt is not None: return amt
    return None

def fetch_html(url):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    r = requests.get(url, headers=headers)
    r.encoding = 'utf-8'
    return BeautifulSoup(r.text, 'html.parser')

def parse_bs_is(soup):
    """연결재무제표에서 BS/IS 파싱"""
    result = {'assets': None, 'liabilities': None, 'equity': None,
              'op': None, 'ni': None, 'ni_parent': None}
    for table in soup.find_all('table'):
        rows = table.find_all('tr')
        if len(rows) < 3: continue
        for row in rows:
            cells = row.find_all(['td', 'th'])
            if len(cells) < 2: continue
            label = cells[0].get_text(strip=True).replace('\xa0','').replace(' ','').replace('\u3000','')
            amt = first_valid_amount(cells)
            
            if '자산총계' in label and result['assets'] is None:
                result['assets'] = amt
            elif '부채총계' in label and result['liabilities'] is None:
                result['liabilities'] = amt
            elif '자본총계' in label and '부채' not in label and result['equity'] is None:
                result['equity'] = amt
            elif '영업이익' in label and '반영전' not in label and '신용' not in label and result['op'] is None and amt is not None:
                result['op'] = amt
            elif '지배기업주주지분순이익' in label and result['ni_parent'] is None:
                result['ni_parent'] = amt
            elif ('당기순이익' in label or '분기순이익' in label or '반기순이익' in label) and '귀속' not in label:
                if result['ni'] is None and amt is not None:
                    result['ni'] = amt
    return result

def parse_cf_return(soup):
    """현금흐름표에서 배당금/자사주 파싱 (cells[1] 직접)"""
    dividend = 0.0
    buyback = 0.0
    for table in soup.find_all('table'):
        rows = table.find_all('tr')
        if len(rows) < 3: continue
        for row in rows:
            cells = row.find_all(['td', 'th'])
            if len(cells) < 2: continue
            label = cells[0].get_text(strip=True).replace('\xa0','').replace(' ','').replace('\u3000','')
            cell1_amt = parse_amount(cells[1].get_text(strip=True))
            
            # 배당: '배당' + '지급' 포함, '신종' 제외
            if '배당' in label and '지급' in label and '신종' not in label:
                if cell1_amt is not None:
                    dividend += cell1_amt
            # 자기주식 취득: '자기주식' + '취득' 포함, '처분'/'소각' 제외
            elif '자기주식' in label and '취득' in label and '처분' not in label and '소각' not in label:
                if cell1_amt is not None:
                    buyback += cell1_amt
    return dividend, buyback

def fmt(v):
    if v is None: return "       N/A"
    return f"{v:>10,.0f}"

# ============================================================
# STEP 1: YTD 데이터 수집
# ============================================================
print("=" * 170)
print("[STEP 1] KB금융(105560) 2020~2023Q2 - YTD 원본 (연결BS/IS + 별도CF)")
print("=" * 170)
print(f"{'기간':>8} | {'자산총계':>15} | {'부채총계':>15} | {'자본총계':>15} | {'OP(YTD)':>12} | {'NI(YTD)':>12} | {'지배NI(YTD)':>12} | {'배당(YTD)':>12} | {'자사주(YTD)':>12} | {'별도CF':>6}")
print("-" * 170)

ytd_data = {}
for year in [2020, 2021, 2022, 2023]:
    ytd_data[year] = {}
    quarters = [1, 2, 3, 4] if year < 2023 else [1, 2]
    for q in quarters:
        try:
            rcp_no, rname = find_report_rcp('105560', year, q)
            if rcp_no is None:
                print(f"  {year}Q{q} | 보고서 없음"); time.sleep(0.5); continue
            
            time.sleep(0.5)
            con_url, std_url, sub = find_sub_doc_urls(rcp_no)
            
            # 연결재무제표 파싱 (BS/IS)
            d = {}
            if con_url:
                time.sleep(0.3)
                soup_con = fetch_html(con_url)
                d = parse_bs_is(soup_con)
                # 연결재무제표에서도 CF 시도
                div_con, buy_con = parse_cf_return(soup_con)
                d['dividend'] = div_con
                d['buyback'] = buy_con
            
            # 별도 재무제표에서 CF 파싱 (배당/자사주가 0이면)
            std_used = "N"
            if std_url and d.get('dividend', 0) == 0 and d.get('buyback', 0) == 0:
                time.sleep(0.3)
                soup_std = fetch_html(std_url)
                div_std, buy_std = parse_cf_return(soup_std)
                if div_std != 0 or buy_std != 0:
                    d['dividend'] = div_std
                    d['buyback'] = buy_std
                    std_used = "Y"
            
            ytd_data[year][q] = d
            print(f"  {year}Q{q} | {fmt(d.get('assets')):>15} | {fmt(d.get('liabilities')):>15} | {fmt(d.get('equity')):>15} | {fmt(d.get('op')):>12} | {fmt(d.get('ni')):>12} | {fmt(d.get('ni_parent')):>12} | {fmt(d.get('dividend',0)):>12} | {fmt(d.get('buyback',0)):>12} | {std_used:>6}")
        except Exception as e:
            print(f"  {year}Q{q} | 에러: {str(e)[:70]}")
        time.sleep(0.5)

# ============================================================
# STEP 2: YTD → 분기(discrete) 변환
# ============================================================
print(f"\n{'=' * 170}")
print("[STEP 2] YTD → 분기(discrete) 변환")
print("=" * 170)
print(f"{'기간':>8} | {'자산총계':>15} | {'부채총계':>15} | {'자본총계':>15} | {'OP(분기)':>12} | {'NI(분기)':>12} | {'지배NI(분기)':>12} | {'배당(분기)':>12} | {'자사주(분기)':>12} | {'return':>12}")
print("-" * 170)

flow_keys = ['op', 'ni', 'ni_parent', 'dividend', 'buyback']

for year in [2020, 2021, 2022, 2023]:
    quarters = [1, 2, 3, 4] if year < 2023 else [1, 2]
    for q in quarters:
        if q not in ytd_data.get(year, {}): continue
        d = ytd_data[year][q]
        disc = {}
        disc['assets'] = d.get('assets')
        disc['liabilities'] = d.get('liabilities')
        disc['equity'] = d.get('equity')
        
        for key in flow_keys:
            ytd_val = d.get(key, 0) or 0
            if q == 1:
                disc[key] = ytd_val
            else:
                prev_ytd = ytd_data.get(year, {}).get(q-1, {}).get(key, 0) or 0
                disc[key] = ytd_val - prev_ytd
        
        ret = (disc.get('dividend') or 0) + (disc.get('buyback') or 0)
        print(f"  {year}Q{q} | {fmt(disc['assets']):>15} | {fmt(disc['liabilities']):>15} | {fmt(disc['equity']):>15} | {fmt(disc.get('op')):>12} | {fmt(disc.get('ni')):>12} | {fmt(disc.get('ni_parent')):>12} | {fmt(disc.get('dividend')):>12} | {fmt(disc.get('buyback')):>12} | {fmt(ret):>12}")

print("\n[DONE]")
