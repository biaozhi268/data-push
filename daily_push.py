#!/usr/bin/env python3
"""
中国生鲜乳（原奶）主产区价格采集 - 每日推送脚本
==============================================

整合功能：
1. 采集最新一期数据（复用 scraper.py 的鲁棒解析逻辑）
2. 保存 CSV + 生成 history.json（3字段格式）
3. 推送到企业微信（push.showdoc API）
4. Git commit & push 到 GitHub（数据版本管理）
5. 支持 GitHub Actions 自动运行

用法:
    python daily_push.py              # 采集最新一期 + 推送
    python daily_push.py --no-push   # 只采集，不推送
    python daily_push.py --history 10 # 回溯10期 + 推送
"""

import os
import re
import sys
import time
import json
import csv
import argparse
import logging
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict

import requests
from bs4 import BeautifulSoup

# ── 配置 ──────────────────────────────────────────────────
# 数据源（同 scraper.py 的鲁棒双源架构）
SOURCES = {
    "moa": {
        "list_url": "https://xmsyj.moa.gov.cn/jcyj/",
    },
    "nahs": {
        "list_url": "https://www.nahs.org.cn/jcyj/jghq/",
    },
}
DATA_DIR = Path(__file__).parent / "data"
CSV_FILE = DATA_DIR / "raw_milk_price.csv"
HISTORY_JSON = DATA_DIR / "history.json"
REQUEST_TIMEOUT = 30
REQUEST_DELAY = 1.5

# 推送配置
PUSH_TOKEN = os.environ.get("PUSH_TOKEN", "")
GITHUB_TOKEN = os.environ.get("MY_GITHUB_TOKEN", "")
REPO_OWNER = "biaozhi268"
REPO_NAME = "milk-price-push"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# 第一部分：数据采集（复用 scraper.py 的鲁棒逻辑）
# ═══════════════════════════════════════════════════════

def parse_week_to_date(year: int, month: int, week: int) -> str:
    """将"X月第Y周"转换为近似日期"""
    first_day = datetime(year, month, 1)
    days_to_monday = (7 - first_day.weekday()) % 7
    first_monday = first_day + timedelta(days=days_to_monday)
    target = first_monday + timedelta(days=3 + (week - 1) * 7)
    return target.strftime("%Y-%m-%d")


def extract_week_info(text: str):
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*第\s*(\d{1,2})\s*周", text)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"(?:^[^\d]|^)(\d{1,2})\s*月\s*第\s*(\d{1,2})\s*周", text)
    if m:
        return (None, int(m.group(1)), int(m.group(2)))
    return None


def extract_price_from_text(text: str) -> Optional[float]:
    m = re.search(r"生鲜乳平均价格\s*(\d+\.?\d*)\s*元/公斤", text)
    if m:
        return float(m.group(1))
    return None


def extract_extra_info(full_text: str) -> dict:
    """从生鲜乳段落中提取环比/同比（限定段落，避免误匹配）"""
    info = {}
    m_section = re.search(
        r"生鲜乳价格[。](.*?)(?:饲料价格|\*\*饲料价格\*\*)",
        full_text, re.DOTALL
    )
    section = m_section.group(1) if m_section else full_text

    # 环比
    m = re.search(r"(?:比前一周|与前一周|环比)\s*(下跌|上涨|持平)\s*(\d+\.?\d*)?%?", section)
    if m:
        direction = m.group(1)
        val = float(m.group(2)) if m.group(2) else 0.0
        info["环比变化%"] = -val if direction == "下跌" else (val if direction == "上涨" else 0.0)

    # 同比
    m = re.search(r"同比\s*(下跌|上涨|持平)\s*(\d+\.?\d*)?%?", section)
    if m:
        direction = m.group(1)
        val = float(m.group(2)) if m.group(2) else 0.0
        info["同比变化%"] = -val if direction == "下跌" else (val if direction == "上涨" else 0.0)

    return info


def extract_collection_date(text: str) -> Optional[str]:
    m = re.search(r"采集日为\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if m:
        return f"{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    return None


def extract_publish_date(soup: BeautifulSoup) -> Optional[str]:
    for meta in soup.find_all("meta"):
        if meta.get("name") in ("publishdate", "PubDate"):
            return meta.get("content", "")[:10]
    text = soup.get_text()
    m = re.search(r"日期[：:]\s*(\d{4}-\d{2}-\d{2})", text)
    if m:
        return m.group(1)
    return None


def _extract_from_table(soup: BeautifulSoup) -> Optional[float]:
    """从结构化表格提取价格（nahs 源的双保险）"""
    for row in soup.find_all("tr"):
        cells = row.find_all(["td", "th"])
        cell_texts = [c.get_text(strip=True) for c in cells]
        if not cell_texts:
            continue
        if "主产省份生鲜乳" in cell_texts[0]:
            for ct in cell_texts[1:]:
                try:
                    val = float(ct)
                    if 1.0 < val < 10.0:
                        return val
                except ValueError:
                    continue
    return None


def parse_report_page(url: str) -> Optional[dict]:
    """解析单篇周报页面"""
    log.info(f"获取页面: {url}")
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        resp.encoding = "utf-8"
    except requests.RequestException as e:
        log.error(f"请求失败: {e}")
        return None

    if resp.status_code != 200:
        log.error(f"HTTP {resp.status_code}: {url}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    full_text = soup.get_text()

    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else ""
    week_info = extract_week_info(title) or extract_week_info(full_text)

    price = extract_price_from_text(full_text) or _extract_from_table(soup)
    if price is None:
        log.warning(f"未能提取价格: {url}")
        return None

    extra = extract_extra_info(full_text)
    pub_date = extract_publish_date(soup)
    collect_date = extract_collection_date(full_text)

    year, month, week_num = week_info if week_info else (None, None, None)
    if year is None:
        m = re.search(r"/(\d{4})(\d{2})/", url)
        if m:
            year = int(m.group(1))
            month = month or int(m.group(2))
    if year is None:
        year = int(pub_date[:4]) if pub_date else datetime.now().year

    approx_date = parse_week_to_date(year, month, week_num) if (month and week_num) else ""

    return {
        "报告标题": title.replace("\n", " ").strip(),
        "年份": year,
        "月份": month,
        "第几周": week_num,
        "估算日期": approx_date,
        "采集日": collect_date or "",
        "发布日期": pub_date or "",
        "生鲜乳价格_元每公斤": price,
        **extra,
        "数据来源URL": url,
        "采集时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def _normalize_url(href: str, base: str) -> str:
    if href.startswith("http"):
        return href
    if href.startswith("./"):
        from urllib.parse import urljoin
        return urljoin(base, href.lstrip("./"))
    if href.startswith("/"):
        from urllib.parse import urlparse
        parsed = urlparse(base)
        return f"{parsed.scheme}://{parsed.netloc}{href}"
    return base.rstrip("/") + "/" + href.lstrip("/")


TARGET_TITLE = "畜产品和饲料集贸市场价格情况"


def _find_report_links(list_url: str) -> list:
    try:
        resp = requests.get(list_url, timeout=REQUEST_TIMEOUT, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        resp.encoding = "utf-8"
    except requests.RequestException as e:
        log.warning(f"请求列表页失败: {list_url} — {e}")
        return []

    if resp.status_code != 200:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    links = []
    for a_tag in soup.find_all("a"):
        text = a_tag.get_text(strip=True)
        href = a_tag.get("href", "")
        if TARGET_TITLE in text and href:
            full_url = _normalize_url(href, list_url)
            links.append((text, full_url))
    return links


def fetch_latest() -> Optional[dict]:
    """获取最新一期（双源容错）"""
    for source_name, cfg in SOURCES.items():
        links = _find_report_links(cfg["list_url"])
        if not links:
            continue
        title, url = links[0]
        log.info(f"  使用 {source_name} 源: {title[:40]}...")
        data = parse_report_page(url)
        if data:
            return data
    return None


def fetch_history(max_count: int = 50) -> List[dict]:
    """回溯历史数据"""
    results = []
    seen_urls = set()
    moa_cfg = SOURCES["moa"]

    for page_num in range(0, 50):
        url = moa_cfg["list_url"] if page_num == 0 else f"{moa_cfg['list_url']}index_{page_num}.htm"
        log.info(f"扫描列表页 [{page_num}]: {url}")
        links = _find_report_links(url)
        if not links:
            break

        for title, full_url in links:
            if full_url in seen_urls:
                continue
            seen_urls.add(full_url)
            data = parse_report_page(full_url)
            if data:
                results.append(data)
                log.info(f"  ✓ {data['报告标题'][:40]}... → {data['生鲜乳价格_元每公斤']} 元/公斤")
            if len(results) >= max_count:
                break
            time.sleep(REQUEST_DELAY)
        if len(results) >= max_count:
            break

    results.sort(key=lambda x: x.get("估算日期", ""), reverse=True)
    return results[:max_count]


# ═══════════════════════════════════════════════════════
# 第二部分：数据持久化（CSV详细 + JSON简化3字段）
# ═══════════════════════════════════════════════════════

def to_simple_format(record: dict) -> dict:
    """转换为3字段格式（同对方方案）：period / price / yoy"""
    period = record.get("估算日期", "")
    if not period:
        y, m, w = record.get("年份"), record.get("月份"), record.get("第几周")
        if y and m and w:
            period = parse_week_to_date(y, m, w)

    price = record.get("生鲜乳价格_元每公斤")
    yoy = record.get("同比变化%")
    yoy_str = f"{yoy:+.1f}%" if yoy is not None else "N/A"

    return {"period": period, "price": price, "yoy": yoy_str}


CSV_FIELDS = [
    "报告标题", "年份", "月份", "第几周", "估算日期", "采集日", "发布日期",
    "生鲜乳价格_元每公斤", "环比变化%", "同比变化%",
    "数据来源URL", "采集时间",
]


def merge_and_save_csv(new_data: List[dict]) -> int:
    """合并新数据到 CSV（去重）"""
    existing = []
    if CSV_FILE.exists():
        with open(CSV_FILE, "r", encoding="utf-8-sig", newline="") as f:
            existing = list(csv.DictReader(f))

    existing_dates = {r.get("估算日期", "") for r in existing}
    added = 0
    for record in new_data:
        if record.get("估算日期") and record["估算日期"] not in existing_dates:
            existing.append(record)
            existing_dates.add(record["估算日期"])
            added += 1

    existing.sort(key=lambda x: x.get("估算日期", ""), reverse=True)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(CSV_FILE, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(existing)

    log.info(f"CSV 保存完成: {CSV_FILE} (新增 {added} 条, 总计 {len(existing)} 条)")
    return added


def save_history_json(records: List[dict], max_records: int = 52):
    """保存为3字段格式 JSON（同对方方案）"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_JSON, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    log.info(f"history.json 保存完成 ({len(records)} 条)")
    return records


def load_history_json() -> List[dict]:
    """读取已有 history.json（3字段格式）"""
    if not HISTORY_JSON.exists():
        return []
    with open(HISTORY_JSON, "r", encoding="utf-8") as f:
        return json.load(f)


def load_and_merge_history(new_data: List[dict], max_records: int = 52) -> List[dict]:
    """将新采集的详细数据合并到 history.json（3字段格式）"""
    existing = load_history_json()
    existing_periods = {r["period"] for r in existing}

    for record in new_data:
        simple = to_simple_format(record)
        if simple["period"] not in existing_periods:
            existing.append(simple)
            existing_periods.add(simple["period"])

    # 按 period 降序，保留最近 max_records 条
    existing.sort(key=lambda x: x["period"], reverse=True)
    return existing[:max_records]


# ═══════════════════════════════════════════════════════
# 第三部分：企业微信推送
# ═══════════════════════════════════════════════════════

def push_to_wechat(title: str, content: str, token: str) -> bool:
    if not token:
        log.warning("未设置 PUSH_TOKEN，跳过推送")
        return False

    url = f"https://push.showdoc.com.cn/server/api/push/{token}"
    try:
        resp = requests.post(url, data={"title": title, "content": content}, timeout=15)
        result = resp.json()
        if result.get("error_code") == 0:
            log.info("✅ 推送到企业微信成功！")
            return True
        else:
            log.error(f"❌ 推送失败: {result}")
            return False
    except Exception as e:
        log.error(f"❌ 推送异常: {e}")
        return False


def build_push_content(latest: dict, history: List[dict]) -> tuple:
    """构建推送的标题和内容（Markdown格式）"""
    price = latest.get("生鲜乳价格_元每公斤")
    yoy = latest.get("同比变化%")
    month = latest.get("月份", "")
    week = latest.get("第几周", "")
    period = latest.get("估算日期", "")

    yoy_str = f"{yoy:+.1f}%" if yoy is not None else "N/A"
    title = f"原奶{month}月第{week}周 {price} 同比{yoy_str}"

    # Markdown 内容
    content = f"# 🥛 原奶收购价周报\n\n"
    content += f"**最新价格**: {price} 元/公斤\n"
    content += f"**同比变化**: {yoy_str}\n"
    content += f"**报告期**: {period}\n\n"

    content += "## 最近7周数据\n\n"
    content += "| 日期 | 均价（元/kg） | 同比变化 |\n"
    content += "| ----- | --------------- | ---------- |\n"

    for r in history[:7]:
        s = to_simple_format(r)
        content += f"| {s['period']} | {s['price']} | {s['yoy']} |\n"

    # 趋势分析
    prices = [r.get("生鲜乳价格_元每公斤") for r in history[:7] if r.get("生鲜乳价格_元每公斤")]
    if prices:
        content += f"\n> 📊 近期趋势：价格在 {min(prices):.2f}-{max(prices):.2f} 元/kg 区间\n"

    content += "\n> 数据来源：农业农村部畜牧兽医局"
    return title, content


# ═══════════════════════════════════════════════════════
# 第四部分：Git 自动同步（数据版本管理）
# ═══════════════════════════════════════════════════════

def git_commit_and_push() -> bool:
    """Git commit & push 数据变化"""
    try:
        repo_dir = Path(__file__).parent
        subprocess.run(["git", "add", "data/"], cwd=repo_dir, check=True, capture_output=True)
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=repo_dir, capture_output=True
        )
        if result.returncode == 0:
            log.info("[Git] 数据无变化，跳过 push")
            return True

        msg = f"data: 自动更新 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        subprocess.run(["git", "commit", "-m", msg], cwd=repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "push"], cwd=repo_dir, check=True, capture_output=True)
        log.info("[Git] ✅ 数据已推送到 GitHub")
        return True
    except subprocess.CalledProcessError as e:
        log.warning(f"[Git] 推送失败: {e}")
        return False


# ═══════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="生鲜乳价格每日推送")
    parser.add_argument("--history", type=int, default=0, help="回溯N期历史")
    parser.add_argument("--all", action="store_true", help="采集全部历史")
    parser.add_argument("--no-push", action="store_true", help="不推送到企微")
    parser.add_argument("--no-git", action="store_true", help="不 git push")
    args = parser.parse_args()

    print("=" * 50)
    print("🥛 原奶价格采集 + 推送")
    print("=" * 50)

    # Step 1: 采集数据
    print("\n[1/4] 采集数据...")
    if args.history > 0:
        data = fetch_history(max_count=args.history)
    elif args.all:
        data = fetch_history(max_count=9999)
    else:
        result = fetch_latest()
        data = [result] if result else []

    if not data:
        log.error("未获取到任何数据")
        # 推送失败通知
        if not args.no_push and PUSH_TOKEN:
            push_to_wechat(
                "⚠️ 原奶数据抓取失败",
                f"# ⚠️ 原奶数据抓取失败（{datetime.now().strftime('%Y-%m-%d')}）\n\n所有数据源均未获取到最新一周数据，请手动检查。",
                PUSH_TOKEN
            )
        return

    latest = data[0]
    print(f"  ✅ 最新: {latest['估算日期']} {latest['生鲜乳价格_元每公斤']} 元/公斤")

    # Step 2: 保存数据
    print("\n[2/4] 保存数据...")
    added = merge_and_save_csv(data)

    # 合并到 history.json（保留最近52周）
    history = load_and_merge_history(data)
    save_history_json(history)

    # Step 3: 推送
    print("\n[3/4] 推送到企业微信...")
    if not args.no_push and PUSH_TOKEN:
        title, content = build_push_content(latest, data)
        push_to_wechat(title, content, PUSH_TOKEN)
    else:
        print("  ⏭ 跳过推送（--no-push 或 未设置 PUSH_TOKEN）")

    # Step 4: Git 同步
    print("\n[4/4] Git 同步...")
    if not args.no_git:
        git_commit_and_push()
    else:
        print("  ⏭ 跳过 Git push（--no-git）")

    # 摘要
    print("\n" + "=" * 50)
    print(f"📋 推送标题: {title if not args.no_push else '(未推送)'}")
    print(f"📋 最新价格: {latest['生鲜乳价格_元每公斤']} 元/公斤")
    print(f"📋 同比: {latest.get('同比变化%', 'N/A')}")
    print("=" * 50)


if __name__ == "__main__":
    main()
