import os
import re
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime

# ============ 配置 ============
TOKEN = os.environ.get('PUSH_TOKEN', '')
DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
HISTORY_FILE = os.path.join(DATA_DIR, 'history.json')

PUSH_BASE = "https://push.showdoc.com.cn/server/api/push"

# 数据源定义（按优先级排列）
DATA_SOURCES = [
    {
        'name': 'feedtrade',
        'label': '饲料行业信息网',
        'parse_article': 'parse_feedtrade_article',
    },
    {
        'name': 'dairyonline',
        'label': '乳业在线',
        'parse_article': 'parse_dairyonline_article',
    },
    {
        'name': 'moa',
        'label': '农业农村部',
        'parse_article': 'parse_moa_article',
    },
]

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}


# ============ 数据源解析 ============

def parse_feedtrade_page(page=1):
    """从饲料行业信息网抓取文章链接"""
    if page == 1:
        url = "https://www.feedtrade.com.cn/whey/milk_market/"
    else:
        url = f"https://www.feedtrade.com.cn/whey/milk_market/?nowpage={page}"
    
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        articles = []
        for link in soup.find_all('a', href=True):
            href = link['href']
            text = link.get_text(strip=True)
            if ('生鲜乳' in text or '原奶' in text) and ('元/公斤' in text or '元/kg' in text or '平均价格' in text):
                full_url = href if href.startswith('http') else 'https://www.feedtrade.com.cn' + href
                articles.append({'title': text, 'url': full_url})
        return articles
    except Exception as e:
        print(f"  [feedtrade] 第{page}页抓取失败: {e}")
        return []


def parse_feedtrade_article(url):
    """解析单篇价格文章"""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, 'html.parser')
        text = soup.get_text()
        
        price_match = re.search(r'(\d+\.?\d*)\s*元/公斤', text)
        if not price_match:
            return None
        price = float(price_match.group(1))
        
        yoy_match = re.search(r'同比([上下]跌?|\S*)\s*(\d+\.?\d*)%', text)
        if yoy_match:
            direction = yoy_match.group(1)
            value = yoy_match.group(2)
            if '下' in direction or '跌' in direction or '降' in direction:
                yoy = f"-{value}%"
            else:
                yoy = f"+{value}%"
        else:
            yoy = 'N/A'
        
        week_match = re.search(r'(\d{1,2})月第(\d{1,2})周', text)
        if week_match:
            month = week_match.group(1)
            week = week_match.group(2)
            period = f'{month}月第{week}周'
            return {'period': period, 'price': price, 'yoy': yoy}
    except Exception as e:
        print(f"  [feedtrade] 解析文章失败: {e}")
    return None


def parse_dairyonline_page(page=1):
    """从乳业在线抓取"""
    try:
        base = "https://www.dairyonline.cn"
        url = f"{base}/category/list-cate-id-25-p-{page}" if page > 1 else f"{base}/tag/%e7%94%9f%e9%b2%9c"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        articles = []
        for link in soup.find_all('a', href=True):
            href = link['href']
            text = link.get_text(strip=True)
            if ('生鲜乳' in text or '原奶' in text) and len(text) < 100:
                full_url = href if href.startswith('http') else base + href
                articles.append({'title': text, 'url': full_url})
        return articles
    except Exception as e:
        print(f"  [dairyonline] 第{page}页抓取失败: {e}")
        return []


def parse_dairyonline_article(url):
    """解析乳业在线文章"""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, 'html.parser')
        text = soup.get_text()
        
        price_match = re.search(r'(\d+\.?\d*)\s*元/公斤', text)
        if not price_match:
            return None
        price = float(price_match.group(1))
        
        yoy_match = re.search(r'同比([上下]跌?|\S*)\s*(\d+\.?\d*)%', text)
        if yoy_match:
            direction = yoy_match.group(1)
            value = yoy_match.group(2)
            if '下' in direction or '跌' in direction or '降' in direction:
                yoy = f"-{value}%"
            else:
                yoy = f"+{value}%"
        else:
            yoy = 'N/A'
        
        week_match = re.search(r'(\d{1,2})月第(\d{1,2})周', text)
        if week_match:
            month = week_match.group(1)
            week = week_match.group(2)
            period = f'{month}月第{week}周'
            return {'period': period, 'price': price, 'yoy': yoy}
    except Exception as e:
        print(f"  [dairyonline] 解析文章失败: {e}")
    return None


def parse_moa_page():
    """从农业农村部官网抓取"""
    try:
        resp = requests.get("https://www.moa.gov.cn/wx/blyj_1020/sccpjgbj/", headers=HEADERS, timeout=15)
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        articles = []
        for link in soup.find_all('a', href=True):
            text = link.get_text(strip=True)
            if '生鲜乳' in text and len(text) < 100:
                full_url = link['href'] if link['href'].startswith('http') else 'https://www.moa.gov.cn/' + link['href']
                articles.append({'title': text, 'url': full_url})
        return articles
    except Exception as e:
        print(f"  [moa] 抓取失败: {e}")
        return []


def parse_moa_article(url):
    """解析农业农村部文章"""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, 'html.parser')
        text = soup.get_text()
        
        price_match = re.search(r'(\d+\.?\d*)\s*元/公斤', text)
        if not price_match:
            return None
        price = float(price_match.group(1))
        
        yoy_match = re.search(r'同比([上下]跌?|\S*)\s*(\d+\.?\d*)%', text)
        if yoy_match:
            direction = yoy_match.group(1)
            value = yoy_match.group(2)
            if '下' in direction or '跌' in direction or '降' in direction:
                yoy = f"-{value}%"
            else:
                yoy = f"+{value}%"
        else:
            yoy = 'N/A'
        
        week_match = re.search(r'(\d{1,2})月第(\d{1,2})周', text)
        if week_match:
            month = week_match.group(1)
            week = week_match.group(2)
            period = f'{month}月第{week}周'
            return {'period': period, 'price': price, 'yoy': yoy}
    except Exception as e:
        print(f"  [moa] 解析文章失败: {e}")
    return None


# ============ 多数据源调度 ============

def scrape_from_source(source):
    """从指定数据源抓取最新一周数据，成功就停"""
    name = source['name']
    label = source['label']
    parse_article_fn = source['parse_article']
    
    print(f"\n[{label}] 开始抓取...")
    
    # 根据数据源调用不同的页面抓取
    if name == 'feedtrade':
        fetch_page = parse_feedtrade_page
        parse_article = parse_feedtrade_article
    elif name == 'dairyonline':
        fetch_page = parse_dairyonline_page
        parse_article = parse_dairyonline_article
    elif name == 'moa':
        fetch_page = parse_moa_page
        parse_article = parse_moa_article
    else:
        return None
    
    # 多页抓取，遇到有数据的文章就解析
    for page in range(1, 11):
        if name == 'moa' and page > 1:
            articles = []  # 农业农村部只抓第1页
        else:
            articles = fetch_page(page)
        
        if not articles:
            print(f"  [{label}] 第{page}页无数据，停止翻页")
            break
        
        print(f"  [{label}] 第{page}页找到 {len(articles)} 篇文章")
        
        for article in articles:
            data = parse_article(article['url'])
            if data:
                print(f"  [{label}] ✅ 抓取到: {data['period']} - {data['price']}元/kg ({data['yoy']})")
                return data
    
    print(f"  [{label}] ❌ 未找到有效数据")
    return None


def scrape_all_sources():
    """按顺序抓取，成功就停"""
    for source in DATA_SOURCES:
        data = scrape_from_source(source)
        if data:
            return data
        print(f"[{source['label']}] 抓取失败，尝试下一个数据源...")
    return None


# ============ 历史数据缓存 ============

def load_history():
    """加载历史数据"""
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            print("[缓存] 加载失败，使用空缓存")
            return []
    return []


def save_history(history):
    """保存历史数据"""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    print(f"[缓存] 已保存 {len(history)} 条记录")


def dedup_and_keep_recent(history, new_data):
    """
    合并新数据到历史：
    - 按 period 去重
    - 保留最近 52 周数据
    """
    period_map = {}
    
    # 先加入旧数据
    for item in history:
        period_map[item['period']] = item
    
    # 再加入新数据（覆盖旧数据）
    if new_data:
        period_map[new_data['period']] = new_data
    
    # 转回列表并按时间排序
    merged = list(period_map.values())
    
    def sort_key(item):
        m = re.match(r'(\d{1,2})月第(\d{1,2})周', item['period'])
        if m:
            month = int(m.group(1))
            week = int(m.group(2))
            year = 2026
            return (year, month, week)
        return (0, 0, 0)
    
    merged.sort(key=sort_key, reverse=True)
    
    # 只保留最近 52 周
    return merged[:52]


# ============ 内容构建 ============

def build_content(prices, new_data=None):
    """构建推送标题和内容"""
    today = datetime.now().strftime('%Y-%m-%d')
    
    # 获取最新一周数据
    if not new_data:
        # 抓取失败，推送告警
        title = f"⚠️ 原奶数据抓取失败"
        content = (
            f"# ⚠️ 原奶数据抓取失败（{today}）\n\n"
            f"> 所有数据源均未获取到最新一周数据，请手动检查。\n\n"
            f"最近缓存数据（最近7周）：\n"
        )
        if not prices:
            content += "> 连缓存也没有，数据源可能长期中断。"
        else:
            for p in prices[:7]:
                content += f"- {p['period']}: {p['price']}元/kg (同比 {p['yoy']})\n"
        return title, content
    
    # 获取最新一周信息
    month = re.search(r'(\d{1,2})月', new_data['period']).group(1)
    week = re.search(r'第(\d{1,2})周', new_data['period']).group(1)
    price = new_data['price']
    yoy = new_data['yoy']
    
    # 推送标题格式
    title = f"原奶第{month}月第{week}周 {price} 同比{yoy}"
    
    # 推送内容：最近 7 周
    if not prices:
        prices = []
    
    rows = []
    for p in prices[:7]:
        if p['price'] == 'N/A':
            rows.append(f"| {p['period'].ljust(8)} | {'N/A'.ljust(14)} | {'N/A'.ljust(10)} |")
        else:
            yoy_str = p['yoy'] if p['yoy'] != 'N/A' else 'N/A'
            price_str = str(p['price'])
            rows.append(f"| {p['period'].ljust(8)} | {price_str.ljust(14)} | {yoy_str.ljust(10)} |")
    
    source_note = "\n\n> 数据来源：饲料行业信息网 | 乳业在线 | 农业农村部"
    trend = ""
    if prices and prices[0]['price'] != 'N/A':
        prices_valid = [p['price'] for p in prices if p['price'] != 'N/A']
        if prices_valid:
            trend = f"\n\n📊 近期趋势：价格维持在 {min(prices_valid)}-{max(prices_valid)} 元/kg 区间，整体处于低位磨底阶段。"
    
    content = f"# 🥛 原奶收购价周报\n\n"
    content += "| 日期 | 均价（元/kg） | 同比变化 |\n"
    content += "| ----- | --------------- | ---------- |\n"
    content += "\n".join(rows) + "\n"
    content += source_note
    content += trend
    
    return title, content


# ============ 推送 ============

def push_to_wechat(title, content):
    """推送到 Server 酱"""
    if not TOKEN:
        print("❌ 未设置 PUSH_TOKEN，跳过推送")
        return False
    
    resp = requests.post(f"{PUSH_BASE}/{TOKEN}", data={'title': title, 'content': content}, timeout=15)
    result = resp.json()
    
    if result.get('error_code') == 0:
        print(f"✅ 推送到微信成功！")
        return True
    else:
        print(f"❌ 推送失败: {result}")
        return False


# ============ 主流程 ============

def main():
    print("=" * 50)
    print("🥛 原奶价格推送脚本")
    print("=" * 50)
    
    # 1. 按顺序抓取数据源，成功就停
    new_data = scrape_all_sources()
    
    if new_data:
        print(f"\n✅ 最新一周数据: {new_data}")
    else:
        print("\n⚠️ 所有数据源均未抓取到新数据")
    
    # 2. 加载历史缓存
    history = load_history()
    print(f"[缓存] 当前有 {len(history)} 条历史记录")
    
    # 3. 合并去重，保留最近 52 周
    merged = dedup_and_keep_recent(history, new_data)
    print(f"[缓存] 合并后有 {len(merged)} 条记录")
    
    # 4. 保存历史
    save_history(merged)
    
    # 5. 构建推送内容（标题：原奶第{几}月{几}周 {价格} 同比{多少}）
    title, content = build_content(merged, new_data)
    
    # 6. 推送
    push_to_wechat(title, content)
    
    # 7. 输出概要
    print("\n" + "=" * 50)
    print("📋 推送标题:", title)
    print("\n📋 最近7周数据:")
    for p in merged[:7]:
        print(f"  {p['period']}: {p['price']}元/kg ({p['yoy']})")
    print("=" * 50)


if __name__ == '__main__':
    main()
