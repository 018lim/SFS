# 파일명: sfs_dart_html.py
"""
DART 공시 원문(HTML) 재무제표 파서.

finstate_all API가 조회하지 못하는 구간(금융업 2023Q3 이전)을 메우기 위해,
공시 원문 문서를 직접 받아 재무제표 표를 파싱한다.

- BS/IS(자산·부채·자본·영업이익·순이익)는 연결재무제표 문서에서
- 배당/자사주는 개별(별도)재무제표 문서의 현금흐름표에서

원문 금액은 대부분 백만원 단위라 parse_unit_scale()로 배수를 읽어
원(KRW) 단위로 환산해서 돌려준다. (로컬 DB는 원 단위)
"""
import re
import time
import requests
from bs4 import BeautifulSoup

# 문서 조회용 세션 (연결 재사용 - 매 요청마다 새 소켓을 열지 않도록)
SESSION = requests.Session()
SESSION.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})

REQUEST_DELAY = 0.3   # 원문 문서 요청 간 간격(초)

# 값 셀이 '-'류 문자면 해당 항목이 없다(0)는 뜻. 문서마다 대시 문자가 섞여 쓰인다.
DASHES = ('-', '‐', '–', '—', '−')

# 긴 단위부터 확인해야 한다 ('십억원'이 '억원'에 먼저 걸리면 안 됨)
UNIT_SCALES = (('십억원', 1_000_000_000), ('백만원', 1_000_000),
               ('억원', 100_000_000), ('천원', 1_000), ('원', 1))

# 보고서 1건에서 뽑은 YTD 값 캐시: (ticker, year, quarter) -> dict | None
_YTD_CACHE = {}


def clear_html_cache():
    """종목 1개의 처리가 끝날 때마다 호출하여 메모리를 비운다."""
    _YTD_CACHE.clear()


# ==========================================
# 보고서 / 문서 찾기
# ==========================================
def load_reports(dart, ticker, first_year, last_year):
    """정기보고서 목록 1회 조회 (분기별 검색창을 모두 포함하는 범위)"""
    return dart.list(ticker, start=f'{first_year}-01-01', end=f'{last_year+1}-06-30',
                     kind='A', final=False)


def find_report_rcp(reports, year, quarter):
    """조회해 둔 보고서 목록에서 해당 분기 보고서의 접수번호를 찾는다. 없으면 None."""
    month_map = {1: f'{year:04d}.03', 2: f'{year:04d}.06', 3: f'{year:04d}.09', 4: f'{year:04d}.12'}
    # 접수일(rcept_dt) 기준 분기별 검색창
    q_search = {
        1: (f'{year}0401', f'{year}0731'),
        2: (f'{year}0701', f'{year}1130'),
        3: (f'{year}1001', f'{year+1}0131'),
        4: (f'{year+1}0101', f'{year+1}0630'),
    }
    start, end = q_search[quarter]
    target_month = month_map[quarter]
    if reports is None or reports.empty: return None
    reports = reports[(reports['rcept_dt'] >= start) & (reports['rcept_dt'] <= end)]
    if reports.empty: return None
    matched = reports[reports['report_nm'].str.contains(target_month, na=False)]
    if not matched.empty:
        orig = matched[~matched['report_nm'].str.contains('기재정정')]
        if not orig.empty: return orig.iloc[0]['rcept_no']
        return matched.iloc[0]['rcept_no']
    q_name = {1: '1분기보고서', 2: '반기보고서', 3: '3분기보고서', 4: '사업보고서'}
    matched = reports[reports['report_nm'].str.contains(q_name[quarter], na=False)]
    if not matched.empty: return matched.iloc[0]['rcept_no']
    return None


def find_sub_doc_urls(dart, rcp_no):
    """연결재무제표 URL + 별도(개별) 재무제표 URL 찾기"""
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


def fetch_html(url):
    r = SESSION.get(url)
    r.encoding = 'utf-8'
    return BeautifulSoup(r.text, 'html.parser')


# ==========================================
# 표 파싱
# ==========================================
def parse_amount(text):
    text = text.replace(',', '').replace(' ', '').replace('\xa0', '').strip()
    # 합계 행의 이중 밑줄 표시가 '=' 문자열로 값 뒤에 그대로 붙어 나오는 문서가 있다
    # (예: 삼성생명 재무상태표 '자산총계' 행 -> '317825566==================')
    # 그대로 두면 float() 변환이 실패해 값이 통째로 None 처리된다
    text = text.rstrip('=')
    if not text or text == '-': return None
    if any(c in text for c in ['단', '위', '백', '만', '원', '억', ':', '주']): return None
    if text.startswith('(') and text.endswith(')'):
        try: return -float(text[1:-1])
        except: return None
    try: return float(text)
    except: return None


def parse_unit_scale(soup):
    """'(단위 : 백만원)' 표기를 읽어 원(KRW) 환산 배수를 돌려준다. 못 찾으면 None.

    '[가-힣]*원'으로 잡아야 '(단위:원)'처럼 접두어 없는 표기도 걸린다.
    '+'로 두면 '원' 앞에 한글이 최소 1개 필요해 단독 '원'을 놓친다.

    단위를 못 찾았을 때 기본값(백만원)을 추측하면 안 된다. 이미 원 단위인 문서에
    100만을 곱해 10^6배 틀린 값이 조용히 저장된다(키움증권 2019Q1 영업이익
    2,025억 -> 202,569조). 값을 못 믿는 문서는 아예 쓰지 않는 편이 낫다.
    """
    text = soup.get_text().replace(' ', '').replace('\xa0', '')
    m = re.search(r'단위[\s:：]*([가-힣]*원)', text)
    if m:
        unit = m.group(1)
        for name, scale in UNIT_SCALES:
            if name in unit: return scale
    return None


def first_valid_amount(cells, col=None):
    """값이 들어있는 첫 번째 열(=당기)만 사용.

    col을 주면 그 열을 직접 읽는다. 2023Q3 이후 보고서에서 '3개월 | 누적'이 나뉠 때만
    쓰이고, 그 이전 문서는 col이 None이라 아래 기본 동작 그대로다.

    - 빈 셀: 값 열이 '세부 항목 열 / 소계 열'로 나뉘어 소계 행(Ⅷ.영업이익)은 한 칸
      오른쪽에 값이 들어간다. 레이아웃용 여백이므로 건너뛴다.
    - '-' 셀: 해당 항목이 없다(0)는 뜻이므로 0.0으로 확정한다.
      건너뛰고 뒤 열로 넘어가면 전기 값을 당기 값으로 잘못 읽게 된다.

    참고: 이 모듈이 읽는 2023Q2 이전 보고서는 3개월/누적 구분 없이 누적(YTD) 한 열만
    싣는다. 2023Q3 이후 보고서부터 '3개월'과 '누적'이 나뉘는데, 로컬 DB는 분기 단독값을
    쓰므로 그때는 3개월 열(= 값이 있는 첫 열)을 그대로 읽고 YTD 차감을 하지 않아야 한다.
    """
    if col is not None:
        if col >= len(cells): return None
        text = cells[col].get_text(strip=True).replace('\xa0', '').strip()
        if not text: return None
        return 0.0 if text in DASHES else parse_amount(text)

    for cell in cells[1:]:
        text = cell.get_text(strip=True).replace('\xa0', '').strip()
        if not text: continue
        if text in DASHES: return 0.0
        return parse_amount(text)
    return None


def find_period_cols(rows):
    """손익계산서 헤더가 '3개월 | 누적'으로 나뉘어 있으면 (3개월 열, 누적 열) 인덱스.

    2023Q3 이후 보고서부터 이렇게 나뉜다. 그 이전에는 '제15기 반기'처럼 누적 한 열뿐이라
    (None, None)을 돌려주고, 그때는 값이 있는 첫 열이 곧 누적이다.
    헤더 행에는 계정명 셀이 없어 데이터 행보다 셀이 하나 적으므로 그만큼 밀어준다.
    """
    n_data = max((len(r.find_all(['td', 'th'])) for r in rows), default=0)
    for row in rows[:4]:
        cells = row.find_all(['td', 'th'])
        texts = [c.get_text(strip=True).replace(' ', '') for c in cells]
        if '3개월' in texts and '누적' in texts:
            offset = n_data - len(cells)
            return texts.index('3개월') + offset, texts.index('누적') + offset
    return None, None


def parse_bs_is(soup):
    """연결재무제표에서 BS/IS 파싱 (금액은 원문 단위 그대로)

    손익 항목은 3개월(op/ni)과 누적(op_ytd/ni_ytd)을 함께 담는다.
    3개월 열이 없는 보고서에서는 둘이 같은 값(=누적)이고 is_quarterly가 False다.
    재무상태표 항목은 기말 스냅샷이라 항상 값이 있는 첫 열을 쓴다.
    """
    result = {'assets': None, 'liabilities': None, 'equity': None,
              'op': None, 'ni': None, 'ni_parent': None,
              'op_ytd': None, 'ni_ytd': None, 'ni_parent_ytd': None,
              'is_quarterly': False}
    for table in soup.find_all('table'):
        rows = table.find_all('tr')
        if len(rows) < 3: continue
        q_col, cum_col = find_period_cols(rows)
        for row in rows:
            cells = row.find_all(['td', 'th'])
            if len(cells) < 2: continue
            label = cells[0].get_text(strip=True).replace('\xa0','').replace(' ','').replace('　','')
            amt = first_valid_amount(cells)
            amt_q = first_valid_amount(cells, q_col)
            amt_c = first_valid_amount(cells, cum_col)

            if '자산총계' in label and result['assets'] is None:
                result['assets'] = amt
            elif '부채총계' in label and result['liabilities'] is None:
                result['liabilities'] = amt
            elif '자본총계' in label and '부채' not in label and result['equity'] is None:
                result['equity'] = amt
            # '영업이익'/'영업손실' 외에 '영업손익'(이익/손실 구분 없는 합성 표기)을 쓰는
            # 기업도 있다(071050 실측: 'III.영업손익' -1,679억 -> 매칭 실패로 0.0 저장됨).
            # '기타영업손익'처럼 총계가 아닌 하위 세부항목도 '영업손익'을 포함하므로 제외한다
            # (KB금융 실측: 'Ⅴ.기타영업손익'이 진짜 총계 'Ⅸ.영업이익'보다 먼저 나와 잘못 매칭됨).
            elif ('영업이익' in label or '영업손익' in label) and '반영전' not in label and '신용' not in label and '기타' not in label and result['op'] is None and amt is not None:
                result['op'], result['op_ytd'] = amt_q, amt_c
                result['is_quarterly'] = q_col is not None
            # 지배주주지분 순이익: '지배기업주주지분순이익'(2024) / '지배기업소유주지분순이익'(2023)
            # 처럼 표기가 갈려 '지배' + '순이익'으로 잡고 '비지배'만 걸러낸다
            elif '지배' in label and '순이익' in label and '비지배' not in label \
                    and result['ni_parent'] is None:
                result['ni_parent'], result['ni_parent_ytd'] = amt_q, amt_c
            # 총 순이익(폴백). 당기/분기/반기순이익을 '기순이익' 하나로 잡되,
            # '법인세비용차감전분기순이익'(세전)과 값 없는 소제목 행 '순이익의 귀속'은 제외한다
            elif '기순이익' in label and '귀속' not in label and '차감전' not in label:
                if result['ni'] is None and amt is not None:
                    result['ni'], result['ni_ytd'] = amt_q, amt_c
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
            label = cells[0].get_text(strip=True).replace('\xa0','').replace(' ','').replace('　','')
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


# ==========================================
# 고수준 조회 (원 단위 환산 + 캐시)
# ==========================================
def get_report_ytd(dart, ticker, year, quarter, reports):
    """보고서 1건에서 YTD 값을 뽑아 원(KRW) 단위로 돌려준다.

    반환: {'assets','liabilities','equity','op','ni','ni_parent','dividend','buyback'}
          또는 보고서를 못 찾으면 None
    - assets/liabilities/equity는 기말 스냅샷, 나머지는 해당 분기까지의 누적(YTD)
    - dividend/buyback은 유출이 음수(원문 괄호 표기)로 들어온다
    """
    cache_key = (ticker, year, quarter)
    if cache_key in _YTD_CACHE:
        return _YTD_CACHE[cache_key]

    rcp_no = find_report_rcp(reports, year, quarter)
    if rcp_no is None:
        _YTD_CACHE[cache_key] = None
        return None

    con_url, std_url = find_sub_doc_urls(dart, rcp_no)
    values = {'assets': None, 'liabilities': None, 'equity': None,
              'op': None, 'ni': None, 'ni_parent': None,
              'op_ytd': None, 'ni_ytd': None, 'ni_parent_ytd': None,
              'is_quarterly': False, 'dividend': None, 'buyback': None}

    # 단위를 못 읽은 문서는 값을 쓰지 않는다. 배수를 추측해 곱하면 10^6배 틀린 값이
    # 조용히 들어간다 (parse_unit_scale 주석 참고).

    # 연결재무제표: BS/IS
    if con_url:
        time.sleep(REQUEST_DELAY)
        soup_con = fetch_html(con_url)
        scale = parse_unit_scale(soup_con)
        if scale is None:
            print(f"  ⚠️ [{ticker}] {year}Q{quarter} 연결재무제표 단위 표기를 찾지 못해 건너뜁니다.")
        else:
            parsed = parse_bs_is(soup_con)
            values['is_quarterly'] = parsed.pop('is_quarterly')   # 배수를 곱하면 안 되는 플래그
            for key, raw in parsed.items():
                values[key] = None if raw is None else raw * scale

    # 개별(별도)재무제표: 현금흐름표의 배당/자사주
    if std_url:
        time.sleep(REQUEST_DELAY)
        soup_std = fetch_html(std_url)
        scale = parse_unit_scale(soup_std)
        if scale is None:
            print(f"  ⚠️ [{ticker}] {year}Q{quarter} 별도재무제표 단위 표기를 찾지 못해 건너뜁니다.")
        else:
            div_raw, buy_raw = parse_cf_return(soup_std)
            values['dividend'] = div_raw * scale
            values['buyback'] = buy_raw * scale

    _YTD_CACHE[cache_key] = values
    return values


def get_html_quarter_values(dart, ticker, year, quarter, reports):
    """step2가 그대로 저장할 수 있는 분기 단독(discrete) 값을 돌려준다.

    반환: {'assets','liabilities','equity','op','ni','dividend','buyback'} (원 단위) 또는 None
    - assets/liabilities/equity: 해당 분기말 스냅샷
    - op/ni: YTD(q) - YTD(q-1) (1분기는 YTD 그대로). ni는 지배주주지분 우선
    - dividend/buyback: 분기 단독 배당금·자기주식 취득액 (로컬 DB 관행대로 양수).
      주주환원금은 저장하지 않고 쓰는 쪽에서 둘을 합산한다.
    직전 분기 보고서를 못 구하면 해당 플로우 항목은 None (0으로 두면 YTD가 그대로 찍힌다)
    """
    cur = get_report_ytd(dart, ticker, year, quarter, reports)
    if cur is None:
        return None

    result = {key: cur.get(key) for key in ('assets', 'liabilities', 'equity')}
    prev = {} if quarter == 1 else get_report_ytd(dart, ticker, year, quarter - 1, reports)
    if prev is None:
        result.update(dict.fromkeys(('op', 'ni', 'dividend', 'buyback')))
        return result

    def _discrete(key):
        cur_val = cur.get(key)
        if cur_val is None: return None
        return cur_val - (prev.get(key) or 0)

    # 3개월 열이 있는 보고서(2023Q3 이후)는 그 값이 곧 분기값이라 차감하지 않는다.
    # 없으면 누적에서 직전 분기 누적을 뺀다. 4분기는 3개월 열이 없어 '연간 - Q3 누적'이 된다.
    if cur.get('is_quarterly'):
        result['op'] = cur.get('op')
        result['ni'] = cur.get('ni_parent' if cur.get('ni_parent') is not None else 'ni')
    else:
        result['op'] = _discrete('op_ytd')
        result['ni'] = _discrete('ni_parent_ytd' if cur.get('ni_parent_ytd') is not None else 'ni_ytd')

    div = _discrete('dividend')
    buy = _discrete('buyback')
    result['dividend'] = None if div is None else abs(div)
    result['buyback'] = None if buy is None else abs(buy)

    return result


# ==========================================
# 단독 실행: KB금융 2021Q1~2023Q2 (finstate_all이 못 주는 구간)
# ==========================================
if __name__ == "__main__":
    import os, sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    from dotenv import load_dotenv
    load_dotenv()
    import OpenDartReader

    TICKER = '105560'
    FIRST_YQ, LAST_YQ = (2021, 1), (2023, 2)
    PERIODS = [(y, q) for y in range(FIRST_YQ[0], LAST_YQ[0] + 1) for q in (1, 2, 3, 4)
               if FIRST_YQ <= (y, q) <= LAST_YQ]

    def fmt(v):
        if v is None: return "       N/A"
        return f"{v/1_000_000:>10,.0f}"   # 내부 값은 원 단위 -> 백만원으로 표시

    dart = OpenDartReader(os.getenv("DART_API_KEY"))
    reports = load_reports(dart, TICKER, FIRST_YQ[0], LAST_YQ[0])

    print("=" * 150)
    print(f"[sfs_dart_html] KB금융({TICKER}) {FIRST_YQ[0]}Q{FIRST_YQ[1]}~{LAST_YQ[0]}Q{LAST_YQ[1]}"
          f" - DART 공시원문 파싱 (단위: 백만원, 내부 저장은 원)")
    print("=" * 150)
    print(f"{'기간':>8} | {'자산총계':>15} | {'부채총계':>15} | {'자본총계':>15} | "
          f"{'OP(분기)':>12} | {'NI(분기)':>12} | {'배당(분기)':>12} | {'자사주(분기)':>12} | {'주주환원(분기)':>14}")
    print("-" * 150)

    for year, q in PERIODS:
        try:
            d = get_html_quarter_values(dart, TICKER, year, q, reports)
            if d is None:
                print(f"  {year}Q{q} | 보고서 없음")
                continue
            # 주주환원금은 저장 대상이 아니라 표시용으로만 합산한다
            ret = None if d['dividend'] is None and d['buyback'] is None \
                else (d['dividend'] or 0) + (d['buyback'] or 0)
            print(f"  {year}Q{q} | {fmt(d['assets']):>15} | {fmt(d['liabilities']):>15} | "
                  f"{fmt(d['equity']):>15} | {fmt(d['op']):>12} | {fmt(d['ni']):>12} | "
                  f"{fmt(d['dividend']):>12} | {fmt(d['buyback']):>12} | {fmt(ret):>14}")
        except Exception as e:
            print(f"  {year}Q{q} | 에러: {str(e)[:70]}")

    clear_html_cache()
    print("\n[DONE]")
