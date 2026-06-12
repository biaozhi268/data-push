import os
import re
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime

# ============ 配置 ============
TOKEN = os.environ.get('PUSH_TOKEN', '')
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')
REPO_OWNER = "biaozhi268"        # 改成你的 GitHub 用户名
REPO_NAME = "milk-price-push"  # 改成你的仓库名
RELEASE_ASSET_NAME = "history.json"

PUSH_BASE = "https://push.showdoc.com.cn/server/api/push"

# 数据源定义（按优先级排列）
DATA_SOURCES = [
    {'name': 'feedtrade', 'label': '饲料行业信息网', 'parse_article': 'parse_feedtrade_article'},
    {'name': 'dairyonline', 'label': '乳业在线', 'parse_article': 'parse_dairyonline_article'},
    {'name': 'moa', 'label': '农业农村部', 'parse_article': 'parse_moa_article'},
]

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}


# ============ 数据源解析 ============

def parse_feedtrade_page(page=1):
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
    name = source['name']
    label = source['label']
    parse_article = None
    
    if name == 'feedtrade':
        parse_article = parse_feedtrade_article
    elif name == 'dairyonline':
        parse_article = parse_dairyonline_article
    elif name == 'moa':
        parse_article = parse_moa_article
    else:
        return None
    
    print(f"\n[{label}] 开始抓取...")
    
    fetch_page = None
    if name == 'feedtrade':
        fetch_page = parse_feedtrade_page
    elif name == 'dairyonline':
        fetch_page = parse_dairyonline_page
    elif name == 'moa':
        fetch_page = parse_moa_page
    
    for page in range(1, 11):
        if name == 'moa' and page > 1:
            articles = []
        else:
            articles = fetch_page(page) if fetch_page else []
        
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
    for source in DATA_SOURCES:
        data = scrape_from_source(source)
        if data:
            return data
        print(f"[{source['label']}] 抓取失败，尝试下一个数据源...")
    return None


# ============ GitHub Releases 存储 ============

def get_release_id():
    """获取最新 Release ID"""
    api_url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/latest"
    headers = {'Authorization': f'token {GITHUB_TOKEN}', 'Accept': 'application/vnd.github.v3+json'}
    try:
        resp = requests.get(api_url, headers=headers, timeout=15)
        if resp.status_code == 200:
            return resp.json().get('id')
        return None
    except Exception as e:
        print(f"[GitHub] 获取 Release 失败: {e}")
        return None


def upload_history_to_github(history):
    """上传 history.json 到 GitHub Releases"""
    import tempfile
    
    # 创建临时 JSON 文件
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
        temp_file = f.name
    
    try:
        release_id = get_release_id()
        
        if release_id:
            # 已有 Release，更新资产
            asset_api = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/assets/{release_id}"
            # 先删除旧资产
            asset_url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/{release_id}/assets"
            headers = {'Authorization': f'token {GITHUB_TOKEN}', 'Accept': 'application/vnd.github.v3+json'}
            resp = requests.get(asset_url, headers=headers, timeout=15)
            if resp.status_code == 200:
                for asset in resp.json():
                    if asset['name'] == RELEASE_ASSET_NAME:
                        delete_url = asset['url']
                        requests.delete(delete_url, headers=headers, timeout=15)
                        print(f"[GitHub] 已删除旧资产: {RELEASE_ASSET_NAME}")
            
            # 上传新资产
            upload_url = f"https://uploads.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/{release_id}/assets?name={RELEASE_ASSET_NAME}"
            resp = requests.post(upload_url, headers=headers, files={'file': open(temp_file, 'rb')}, timeout=30)
            if resp.status_code == 201:
                print(f"[GitHub] ✅ 已上传 history.json")
                return True
            else:
                print(f"[GitHub] ❌ 上传失败: {resp.status_code} {resp.text}")
                return False
        else:
            # 创建新 Release
            api_url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases"
            headers = {'Authorization': f'token {GITHUB_TOKEN}', 'Accept': 'application/vnd.github.v3+json'}
            data = {
                'tag_name': 'history',
                'name': 'Milk Price History',
                'body': '每日原奶收购价格历史数据缓存'
            }
            resp = requests.post(api_url, headers=headers, json=data, timeout=15)
            if resp.status_code == 201:
                new_release_id = resp.json().get('id')
                upload_url = f"https://uploads.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/{new_release_id}/assets?name={RELEASE_ASSET_NAME}"
                resp = requests.post(upload_url, headers=headers, files={'file': open(temp_file, 'rb')}, timeout=30)
                if resp.status_code == 201:
                    print(f"[GitHub] ✅ 已创建 Release 并上传 history.json")
                    return True
                else:
                    print(f"[GitHub] ❌ 上传失败: {resp.status_code} {resp.text}")
                    return False
            else:
                print(f"[GitHub] ❌ 创建 Release 失败: {resp.status_code} {resp.text}")
                return False
    except Exception as e:
        print(f"[GitHub] 上传异常: {e}")
        return False
    finally:
        os.unlink(temp_file)


def download_history_from_github():
    """从 GitHub Releases 下载 history.json"""
    try:
        release_id = get_release_id()
        if not release_id:
            print("[GitHub] 未找到 Release，使用空历史")
            return []
        
        asset_url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/{release_id}/assets"
        headers = {'Authorization': f'token {GITHUB_TOKEN}', 'Accept': 'application/vnd.github.v3+json'}
        resp = requests.get(asset_url, headers=headers, timeout=15)
        if resp.status_code != 200:
            print(f"[GitHub] 获取资产列表失败: {resp.status_code}")
            return []
        
        for asset in resp.json():
            if asset['name'] == RELEASE_ASSET_NAME:
                download_url = asset['browser_download_url']
                resp = requests.get(download_url, timeout=30)
                if resp.status_code == 200:
                    history = resp.json()
                    print(f"[GitHub] ✅ 已下载 {len(history)} 条历史记录")
                    return history
                else:
                    print(f"[GitHub] 下载文件失败: {resp.status_code}")
                    return []
        print("[GitHub] 未找到 history.json 资产")
        return []
    except Exception as e:
        print(f"[GitHub] 下载异常: {e}")
        return []


def dedup_and_keep_recent(history, new_data):
    """合并新数据到历史，保留最近 52 周"""
    period_map = {}
    for item in history:
        period_map[item['period']] = item
    if new_data:
        period_map[new_data['period']] = new_data
    
    merged = list(period_map.values())
    
    def sort_key(item):
        m = re.match(r'(\d{1,2})月第(\d{1,2})周', item['period'])
        if m:
            month = int(m.group(1))
            week = int(m.group(2))
            return (2026, month, week)
        return (0, 0, 0)
    
    merged.sort(key=sort_key, reverse=True)
    return merged[:52]


# ============ 内容构建 ============

def build_content(prices, new_data=None):
    today = datetime.now().strftime('%Y-%m-%d')
    
    if not new_data:
        title = f"⚠️ 原奶数据抓取失败"
        content = f"# ⚠️ 原奶数据抓取失败（{today}）\n\n> 所有数据源均未获取到最新一周数据，请手动检查。\n\n"
        if not prices:
            content += "> 连缓存也没有，数据源可能长期中断。"
        else:
            content += "最近缓存数据（最近7周）：\n"
            for p in prices[:7]:
                content += f"- {p['period']}: {p['price']}元/kg (同比 {p['yoy']})\n"
        return title, content
    
    month = re.search(r'(\d{1,2})月', new_data['period']).group(1)
    week = re.search(r'第(\d{1,2})周', new_data['period']).group(1)
    price = new_data['price']
    yoy = new_data['yoy']
    
    title = f"原奶第{month}月第{week}周 {price} 同比{yoy}"
    
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
    if not TOKEN:
        print("❌ 未设置 PUSH_TOKEN，跳过推送")
        return False
    
    resp = requests.post(f"{PUSH_BASE}/{TOKEN}", data={'title': title, 'content': content}, timeout=15)
    result = resp.json()
    
    if result.get('error_code') == 0:
        print("✅ 推送到微信成功！")
        return True
    else:
        print(f"❌ 推送失败: {result}")
        return False


# ============ 主流程 ============

def main():
    print("=" * 50)
    print("🥛 原奶价格推送脚本")
    print("=" * 50)
    
    # 1. 从 GitHub 下载历史数据
    print("\n[1/5] 从 GitHub 下载历史数据...")
    history = download_history_from_github()
    
    # 2. 抓取最新一周
    print("\n[2/5] 抓取最新一周数据...")
    new_data = scrape_all_sources()
    
    if new_data:
        print(f"\n✅ 最新一周数据: {new_data}")
    else:
        print("\n⚠️ 所有数据源均未抓取到新数据")
    
    # 3. 合并去重
    print("\n[3/5] 合并历史数据...")
    merged = dedup_and_keep_recent(history, new_data)
    print(f"[历史] 合并后有 {len(merged)} 条记录")
    
    # 4. 上传到 GitHub
    print("\n[4/5] 上传到 GitHub Releases...")
    if new_data:
        upload_history_to_github(merged)
    
    # 5. 构建并推送
    print("\n[5/5] 推送到微信...")
    title, content = build_content(merged, new_data)
    push_to_wechat(title, content)
    
    print("\n" + "=" * 50)
    print("📋 推送标题:", title)
    print("\n📋 最近7周数据:")
    for p in merged[:7]:
        print(f"  {p['period']}: {p['price']}元/kg ({p['yoy']})")
    print("=" * 50)


if __name__ == '__main__':
    main()
