#!/usr/bin/env python3
"""
中国生鲜乳（原奶）主产区价格采集脚本
=======================================
数据来源: 农业农村部畜牧兽医局
  - 主站 (xmsyj.moa.gov.cn): 更新最快，通常周二/周三发布
  - 畜牧总站 (nahs.org.cn): 有结构化表格，作为备选校验
更新频率: 每周一次
数据范围: 内蒙古、河北等10个主产省份生鲜乳平均价格（元/公斤）

用法:
    python scraper.py                # 采集最新一期数据
    python scraper.py --history 50   # 回溯最近50期数据
    python scraper.py --all          # 采集全部可获取的历史数据
"""

import re
import time
import json
import csv
import argparse
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

# ── 配置 ──────────────────────────────────────────────────
# 两个数据源：moa.gov.cn 更新更快，nahs.org.cn 有表格校验
SOURCES = {
    "moa": {
        "list_url": "https://xmsyj.moa.gov.cn/jcyj/",
        # 列表页链接可能以 "./" 或绝对路径开头
    },
    "nahs": {
        "list_url": "https://www.nahs.org.cn/jcyj/jghq/",
    },
}
DATA_DIR = Path(__file__).parent / "data"
CSV_FILE = DATA_DIR / "raw_milk_price.csv"
REQUEST_TIMEOUT = 30
REQUEST_DELAY = 1.5  # 请求间隔，避免被封

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ── 工具函数 ────────────────────────────────────────────────
def parse_week_to_date(year: int, month: int, week: int) -> str:
    """将"X月第Y周"转换为近似日期（取该周周四为采集日）"""
    # 该月第1天
    first_day = datetime(year, month, 1)
    # 找到该月第一个周一
    days_to_monday = (7 - first_day.weekday()) % 7
    first_monday = first_day + timedelta(days=days_to_monday)
    # 第N周的周四 = 第一个周一 + 3天 + (N-1)*7天
    target = first_monday + timedelta(days=3 + (week - 1) * 7)
    return target.strftime("%Y-%m-%d")


def extract_week_info(text: str) -> Optional[tuple]:
    """从标题/文本中提取 年、月、周序号
    例如: "6月第4周畜产品和饲料集贸市场价格情况" → (None, 6, 4)
    例如: "2026年6月第1周" → (2026, 6, 1)
    """
    # 带年份: "2026年6月第1周"
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*第\s*(\d{1,2})\s*周", text)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))

    # 无年份: "6月第4周"
    m = re.search(r"(?:^|[^\d])(\d{1,2})\s*月\s*第\s*(\d{1,2})\s*周", text)
    if m:
        return (None, int(m.group(1)), int(m.group(2)))

    return None


def extract_price_from_text(text: str) -> Optional[float]:
    """从文本段落中提取生鲜乳价格"""
    m = re.search(r"生鲜乳平均价格\s*(\d+\.?\d*)\s*元/公斤", text)
    if m:
        return float(m.group(1))
    # 备用模式
    m = re.search(r"生鲜乳.*?(\d+\.\d+)\s*元/公斤", text)
    if m:
        return float(m.group(1))
    return None


def extract_extra_info(full_text: str) -> dict:
    """从生鲜乳段落中提取环比/同比变化信息（限定在生鲜乳段内，避免误匹配其他品类）"""
    info = {}

    # 截取生鲜乳相关段落：从"生鲜乳价格"到"饲料价格"（避免误匹配其他品类）
    # 两种页面格式：
    #   moa.gov.cn:  "生鲜乳价格。内蒙古...同比下跌0.3%。饲料价格。全国玉米..."
    #   nahs.org.cn: "生鲜乳价格。内蒙古...同比下跌0.3%。**饲料价格**。"
    m_section = re.search(
        r"生鲜乳价格[。.](.*?)(?:饲料价格|\*\*饲料价格\*\*)",
        full_text, re.DOTALL
    )
    section = m_section.group(1) if m_section else full_text

    # 环比: "比前一周下跌0.3%" / "与前一周持平" / "比前一周上涨0.5%" / "环比下跌0.3%"
    m = re.search(r"(?:比前一周|与前一周|环比)\s*(下跌|上涨|持平)\s*(\d+\.?\d*)?%?", section)
    if m:
        direction = m.group(1)
        val = float(m.group(2)) if m.group(2) else 0.0
        if direction == "下跌":
            info["环比变化%"] = -val
        elif direction == "上涨":
            info["环比变化%"] = val
        else:
            info["环比变化%"] = 0.0

    # 同比: "同比下跌0.3%" / "同比上涨1.2%" / "同比持平"
    m = re.search(r"同比\s*(下跌|上涨|持平)\s*(\d+\.?\d*)?%?", section)
    if m:
        direction = m.group(1)
        val = float(m.group(2)) if m.group(2) else 0.0
        if direction == "下跌":
            info["同比变化%"] = -val
        elif direction == "上涨":
            info["同比变化%"] = val
        else:
            info["同比变化%"] = 0.0

    return info


def extract_collection_date(text: str) -> Optional[str]:
    """从正文提取采集日，如 "采集日为6月18日" """
    m = re.search(r"采集日为\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if m:
        # 月份和日期，年份从其他地方推断
        return f"{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    return None


def extract_publish_date(soup: BeautifulSoup) -> Optional[str]:
    """从页面meta或文本提取发布日期"""
    # 尝试从 meta 标签获取
    for meta in soup.find_all("meta"):
        if meta.get("name") == "publishdate" or meta.get("name") == "PubDate":
            return meta.get("content", "")[:10]

    # 尝试从页面文本获取
    text = soup.get_text()
    m = re.search(r"日期[：:]\s*(\d{4}-\d{2}-\d{2})", text)
    if m:
        return m.group(1)
    m = re.search(r"(\d{4}-\d{2}-\d{2})\s*(?:作者|来源)", text)
    if m:
        return m.group(1)

    return None


# ── 页面解析 ────────────────────────────────────────────────
def parse_report_page(url: str) -> Optional[dict]:
    """解析单个周报页面，提取生鲜乳价格数据"""
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

    # 1. 从标题提取年月周
    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else ""
    week_info = extract_week_info(title)
    if not week_info:
        # 从正文中再试
        week_info = extract_week_info(full_text)

    # 2. 提取价格（从文本段落）
    price = extract_price_from_text(full_text)

    # 3. 从表格提取（备选方案，更精确）
    if price is None:
        price = _extract_from_table(soup)

    if price is None:
        log.warning(f"未能从页面提取到价格: {url}")
        return None

    # 4. 提取辅助信息
    extra = extract_extra_info(full_text)
    pub_date = extract_publish_date(soup)
    collect_date = extract_collection_date(full_text)

    # 5. 计算日期
    year, month, week_num = week_info if week_info else (None, None, None)
    # 尝试从URL中提取年份
    if year is None:
        m = re.search(r"/(\d{4})(\d{2})/", url)
        if m:
            year = int(m.group(1))
            if month is None:
                month = int(m.group(2))

    if year is None:
        # fallback: 从发布日期取年份
        if pub_date:
            year = int(pub_date[:4])
        else:
            year = datetime.now().year

    approx_date = parse_week_to_date(year, month, week_num) if (month and week_num) else ""

    result = {
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
    return result


def _extract_from_table(soup: BeautifulSoup) -> Optional[float]:
    """从页面底部结构化表格中提取 主产省份生鲜乳 本周价格"""
    # 寻找包含 "主产省份生鲜乳" 的表格行
    for row in soup.find_all("tr"):
        cells = row.find_all(["td", "th"])
        cell_texts = [c.get_text(strip=True) for c in cells]
        if not cell_texts:
            continue
        # 第一列匹配 "主产省份生鲜乳"
        if "主产省份生鲜乳" in cell_texts[0] or "生鲜乳" in cell_texts[0]:
            # 通常 "本周" 是第2列
            for i, ct in enumerate(cell_texts[1:], 1):
                try:
                    val = float(ct)
                    if 1.0 < val < 10.0:  # 合理的价格范围
                        return val
                except ValueError:
                    continue
    return None


# ── 工具：解析列表页 ──────────────────────────────────────────
def _normalize_url(href: str, base: str) -> str:
    """将相对路径转为绝对URL"""
    if href.startswith("http"):
        return href
    if href.startswith("./"):
        # "./202606/t20260617_xxx.htm" → 拼接域名路径
        from urllib.parse import urljoin
        return urljoin(base, href.lstrip("./"))
    if href.startswith("/"):
        from urllib.parse import urlparse
        parsed = urlparse(base)
        return f"{parsed.scheme}://{parsed.netloc}{href}"
    # 相对路径
    return base.rstrip("/") + "/" + href.lstrip("/")


TARGET_TITLE = "畜产品和饲料集贸市场价格情况"


def _find_report_links(list_url: str) -> list:
    """从列表页提取所有目标报告的 (title, url) 对"""
    try:
        resp = requests.get(list_url, timeout=REQUEST_TIMEOUT, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        resp.encoding = "utf-8"
    except requests.RequestException as e:
        log.warning(f"请求列表页失败: {list_url} — {e}")
        return []

    if resp.status_code != 200:
        log.warning(f"HTTP {resp.status_code}: {list_url}")
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


# ── 列表页爬取 ──────────────────────────────────────────────
def fetch_latest() -> Optional[dict]:
    """从两个数据源获取最新一期数据，优先使用更新更快的 moa 源"""
    log.info("获取最新一期生鲜乳价格...")

    for source_name, cfg in SOURCES.items():
        links = _find_report_links(cfg["list_url"])
        if not links:
            log.warning(f"  {source_name} 源无数据，尝试下一个...")
            continue

        title, url = links[0]  # 最新一条
        log.info(f"  使用 {source_name} 源: {title} → {url}")
        data = parse_report_page(url)
        if data:
            return data

    log.error("所有数据源均获取失败")
    return None


def fetch_history(max_count: int = 50) -> list:
    """回溯历史数据，从 moa 源获取（更新更全）"""
    results = []
    seen_urls = set()
    moa_cfg = SOURCES["moa"]

    # 先尝试 index_0, index_1, ... 翻页
    for page_num in range(0, 50):
        if page_num == 0:
            url = moa_cfg["list_url"]
        else:
            url = f"{moa_cfg['list_url']}index_{page_num}.htm"

        log.info(f"扫描列表页 [{page_num}]: {url}")
        links = _find_report_links(url)
        if not links:
            log.info("  无更多数据，停止翻页")
            break

        for title, full_url in links:
            if full_url in seen_urls:
                continue
            seen_urls.add(full_url)

            data = parse_report_page(full_url)
            if data:
                results.append(data)
                log.info(f"  ✓ {data['报告标题'][:45]}... → {data['生鲜乳价格_元每公斤']} 元/公斤")

            if len(results) >= max_count:
                break

            time.sleep(REQUEST_DELAY)

        if len(results) >= max_count:
            break

    # 按日期排序
    results.sort(key=lambda x: x.get("估算日期", ""), reverse=True)
    # 截取需要的条数
    return results[:max_count]


# ── CSV 管理 ────────────────────────────────────────────────
CSV_FIELDS = [
    "报告标题", "年份", "月份", "第几周", "估算日期", "采集日", "发布日期",
    "生鲜乳价格_元每公斤", "环比变化%", "同比变化%",
    "数据来源URL", "采集时间",
]


def load_existing_csv() -> list:
    """读取已有CSV数据"""
    if not CSV_FILE.exists():
        return []
    rows = []
    with open(CSV_FILE, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def merge_and_save(new_data: list):
    """将新数据去重合并到CSV（以估算日期为去重键）"""
    existing = load_existing_csv()
    existing_dates = {r.get("估算日期", "") for r in existing}

    added = 0
    for record in new_data:
        if record.get("估算日期") and record["估算日期"] not in existing_dates:
            existing.append(record)
            existing_dates.add(record["估算日期"])
            added += 1

    # 按日期排序
    existing.sort(key=lambda x: x.get("估算日期", ""), reverse=True)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(CSV_FILE, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(existing)

    log.info(f"保存完成: {CSV_FILE} (新增 {added} 条, 总计 {len(existing)} 条)")
    return added


# ── 命令行入口 ───────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="中国生鲜乳（原奶）主产区价格采集工具"
    )
    parser.add_argument(
        "--history", type=int, default=0, metavar="N",
        help="回溯最近 N 期历史数据（默认只采集最新一期）"
    )
    parser.add_argument(
        "--all", action="store_true",
        help="采集全部可获取的历史数据"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="以JSON格式输出到stdout（不保存CSV）"
    )
    args = parser.parse_args()

    if args.history > 0:
        data = fetch_history(max_count=args.history)
    elif args.all:
        data = fetch_history(max_count=9999)
    else:
        result = fetch_latest()
        data = [result] if result else []

    if not data:
        log.error("未获取到任何数据")
        return

    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        added = merge_and_save(data)
        # 打印摘要
        latest = data[0]
        print(f"\n{'='*50}")
        print(f"  最新生鲜乳价格: {latest['生鲜乳价格_元每公斤']} 元/公斤")
        print(f"  报告期: {latest['估算日期']} ({latest['报告标题'][:30]}...)")
        if latest.get("环比变化%"):
            print(f"  环比: {latest['环比变化%']:+.1f}%")
        if latest.get("同比变化%"):
            print(f"  同比: {latest['同比变化%']:+.1f}%")
        print(f"  新增 {added} 条记录 → {CSV_FILE}")
        print(f"{'='*50}")


if __name__ == "__main__":
    main()
