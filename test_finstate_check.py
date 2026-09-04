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

# 문서 조회용 세션 (연결 재사용 - 매 요청마다 새 소켓을 열지 않도록)
SESSION = requests.Session()
SESSION.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})

TICKER = '105560'
FIRST_YQ, LAST_YQ = (2020, 1), (2023, 2)
PERIODS = [(y, q) for y in range(FIRST_YQ[0], LAST_YQ[0] + 1) for q in (1, 2, 3, 4)
           if FIRST_YQ <= (y, q) <= LAST_YQ]

def load_reports(ticker):
    """정기보고서 목록 1회 조회 (분기별 검색창을 모두 포함하는 범위)"""
    return dart.list(ticker, start=f'{FIRST_YQ[0]}-01-01', end=f'{LAST_YQ[0]+1}-06-30',
                     kind='A', final=False)

def find_report_rcp(reports, year, quarter):
    month_map = {1: f'{year:04d}.03', 2: f'{year:04d}.06', 3: f'{year:04d}.09', 4: f'{year:04d}.12'}
    # 접수일(rcept_dt) 기준 분기별 검색창 - 조회한 목록에서 분기별로 걸러 쓴다
    q_search = {
        1: (f'{year}0401', f'{year}0731'),
        2: (f'{year}0701', f'{year}1130'),
        3: (f'{year}1001', f'{year+1}0131'),
        4: (f'{year+1}0101', f'{year+1}0630'),
    }
    start, end = q_search[quarter]
    target_month = month_map[quarter]
    if reports is None or reports.empty: return None, None
    reports = reports[(reports['rcept_dt'] >= start) & (reports['rcept_dt'] <= end)]
    if reports.empty: return None, None
    matched = reports[reports['report_nm'].str.contains(target_month, na=False)]
    if not matched.empty:
        orig = matched[~matched['report_nm'].str.contains('기재정정')]
        if not orig.empty: return orig.iloc[0]['rcept_no'], orig.iloc[0]['report_nm']
        return matched.iloc[0]['rcept_no'], matched.iloc[0]['report_nm']
    q_name = {1: '1분기보고서', 2: '반기보고서', 3: '3분기보고서', 4: '사업보고서'}
    matched = reports[reports['report_nm'].str.contains(q_name[quarter], na=False)]
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
    
    return con_url, std_url

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
    """값이 들어있는 첫 번째 열(=당기)만 사용. 빈 셀은 레이아웃용이라 건너뛴다.
    (뒤 열까지 훑으면 당기가 비었을 때 전기 값을 가져오게 된다)"""
    for cell in cells[1:]:
        text = cell.get_text(strip=True).replace('\xa0', '').strip()
        if not text: continue
        return parse_amount(text)
    return None

def fetch_html(url):
    r = SESSION.get(url)
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
    """개별(별도)재무제표 현금흐름표에서 배당금/자사주 파싱 (cells[1] 직접)"""
    dividend = 0.0
    buyback = 0.0
    for table in soup.find_all('table'):
        rows = table.find_all('tr')
        if len(rows) < 3: continue
        # 현금흐름표만 대상 (자본변동표의 '자기주식의 취득' 중복 집계 방지)
        if '재무활동' not in table.get_text().replace('\xa0','').replace(' ',''): continue
        for row in rows:
            cells = row.find_all(['td', 'th'])
            if len(cells) < 2: continue
            label = cells[0].get_text(strip=True).replace('\xa0','').replace(' ','').replace('\u3000','')
            cell1_amt = parse_amount(cells[1].get_text(strip=True))
            if cell1_amt is None: continue
            
            # 배당: 표준 계정과목 '배당금의 지급' / '배당금 지급'
            #       신종자본증권 배당, 비지배지분 배당, 배당금 수취는 제외
            if ('배당금의지급' in label or '배당금지급' in label) and \
                    not any(x in label for x in ('신종', '자본증권', '비지배', '수취', '수입')):
                dividend += cell1_amt
            # 자사주: 표준 계정과목 '자기주식의 취득' ('자기주식취득' 표기도 허용)
            elif '자기주식' in label and '취득' in label:
                buyback += cell1_amt
    return dividend, buyback

def fmt(v):
    if v is None: return "       N/A"
    return f"{v:>10,.0f}"

# ============================================================
# STEP 1: YTD 데이터 수집
# ============================================================
print("=" * 170)
print(f"[STEP 1] KB금융({TICKER}) {FIRST_YQ[0]}Q{FIRST_YQ[1]}~{LAST_YQ[0]}Q{LAST_YQ[1]} - YTD 원본 (연결BS/IS + 별도CF)")
print("=" * 170)
print(f"{'기간':>8} | {'자산총계':>15} | {'부채총계':>15} | {'자본총계':>15} | {'OP(YTD)':>12} | {'NI(YTD)':>12} | {'지배NI(YTD)':>12} | {'배당(YTD)':>12} | {'자사주(YTD)':>12} | {'별도CF':>6}")
print("-" * 170)

ytd_data = {}
reports = load_reports(TICKER)
for year, q in PERIODS:
    try:
        rcp_no, _ = find_report_rcp(reports, year, q)
        if rcp_no is None:
            print(f"  {year}Q{q} | 보고서 없음"); continue
        
        con_url, std_url = find_sub_doc_urls(rcp_no)
        
        # 연결재무제표 파싱 (BS/IS)
        d = {}
        if con_url:
            time.sleep(0.3)
            soup_con = fetch_html(con_url)
            d = parse_bs_is(soup_con)
        d['dividend'] = 0.0
        d['buyback'] = 0.0
        
        # 배당/자사주는 개별(별도) 재무제표 현금흐름표에서 파싱
        std_used = "N"
        if std_url:
            time.sleep(0.3)
            soup_std = fetch_html(std_url)
            d['dividend'], d['buyback'] = parse_cf_return(soup_std)
            std_used = "Y"
        
        ytd_data[(year, q)] = d
        print(f"  {year}Q{q} | {fmt(d.get('assets')):>15} | {fmt(d.get('liabilities')):>15} | {fmt(d.get('equity')):>15} | {fmt(d.get('op')):>12} | {fmt(d.get('ni')):>12} | {fmt(d.get('ni_parent')):>12} | {fmt(d['dividend']):>12} | {fmt(d['buyback']):>12} | {std_used:>6}")
    except Exception as e:
        print(f"  {year}Q{q} | 에러: {str(e)[:70]}")
    time.sleep(0.3)

# ============================================================
# STEP 2: YTD → 분기(discrete) 변환
# ============================================================
print(f"\n{'=' * 170}")
print("[STEP 2] YTD → 분기(discrete) 변환")
print("=" * 170)
print(f"{'기간':>8} | {'자산총계':>15} | {'부채총계':>15} | {'자본총계':>15} | {'OP(분기)':>12} | {'NI(분기)':>12} | {'지배NI(분기)':>12} | {'배당(분기)':>12} | {'자사주(분기)':>12} | {'return':>12}")
print("-" * 170)

flow_keys = ['op', 'ni', 'ni_parent', 'dividend', 'buyback']

for year, q in PERIODS:
    d = ytd_data.get((year, q))
    if d is None: continue
    prev = ytd_data.get((year, q-1))
    disc = {key: d.get(key) for key in ['assets', 'liabilities', 'equity']}
    
    for key in flow_keys:
        ytd_val = d.get(key) or 0
        if q == 1:
            disc[key] = ytd_val
        elif prev is None:
            disc[key] = None  # 직전 분기 데이터가 없으면 분기 환산 불가 (0으로 두면 YTD가 그대로 찍힘)
        else:
            disc[key] = ytd_val - (prev.get(key) or 0)
    
    ret = None if disc['dividend'] is None or disc['buyback'] is None else disc['dividend'] + disc['buyback']
    print(f"  {year}Q{q} | {fmt(disc['assets']):>15} | {fmt(disc['liabilities']):>15} | {fmt(disc['equity']):>15} | {fmt(disc['op']):>12} | {fmt(disc['ni']):>12} | {fmt(disc['ni_parent']):>12} | {fmt(disc['dividend']):>12} | {fmt(disc['buyback']):>12} | {fmt(ret):>12}")

print("\n[DONE]")
