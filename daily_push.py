#!/usr/bin/env python3
"""
中国生鲜乳（原奶）主产区价格采集 - 每日推送脚本
==============================================

完整工作流：
1. 从 GitHub Releases 下载 history.csv（历史数据）
2. 爬取最新一期数据（双源鲁棒解析）
3. 去重合并到历史数据
4. 上传 history.csv 到 GitHub Releases
5. 推送到企业微信（最近7周数据）

数据格式：3字段（period / price / yoy），CSV 存储
存储方式：GitHub Releases 附件（稳定URL直接访问）

用法:
    python daily_push.py              # 完整流程
    python daily_push.py --no-push   # 只采集上传，不推送
    python daily_push.py --no-upload # 只采集推送，不上传Releases
"""

import os
import re
import csv
import argparse
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List

import requests
from bs4 import BeautifulSoup

# ── 配置 ──────────────────────────────────────────────────
SOURCES = {
    "moa": {
        "list_url": "https://xmsyj.moa.gov.cn/jcyj/",
    },
    "nahs": {
        "list_url": "https://www.nahs.org.cn/jcyj/jghq/",
    },
}
DATA_DIR = Path(__file__).parent / "data"
HISTORY_CSV = DATA_DIR / "history.csv"
HISTORY_FIELDS = ["period", "price", "yoy"]
REQUEST_TIMEOUT = 30

# GitHub 配置（用于 Releases 上传/下载）
REPO_OWNER = "biaozhi268"
REPO_NAME   = "数据推送"
RELEASE_TAG = "history"

# 推送配置
PUSH_TOKEN = os.environ.get("PUSH_TOKEN", "")
GITHUB_TOKEN = os.environ.get("MY_GITHUB_TOKEN", "")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── 网络请求 session（带自动重试）──────────────────────────
_http = requests.Session()
_retry = requests.adapters.HTTPAdapter(
    max_retries=requests.adapters.Retry(
        total=3,
        backoff_factor=1.5,
        status_forcelist=[500, 502, 503, 504],
    )
)
_http.mount("https://", _retry)
_http.mount("http://", _retry)
_http.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
})


# ═══════════════════════════════════════════════════════
# 第一部分：数据采集（鲁棒双源解析）
# ═══════════════════════════════════════════════════════

def parse_week_to_date(year: int, month: int, week: int) -> str:
    """将"X月第Y周"转换为近似日期（周三）"""
    first_day = datetime(year, month, 1)
    days_to_monday = (7 - first_day.weekday()) % 7
    first_monday = first_day + timedelta(days=days_to_monday)
    target = first_monday + timedelta(days=3 + (week - 1) * 7)
    return target.strftime("%Y-%m-%d")


def extract_week_info(text: str):
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*第\s*(\d{1,2})\s*周", text)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"(?:^|[^\d])(\d{1,2})\s*月\s*第\s*(\d{1,2})\s*周", text)
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
    """解析单篇周报页面，返回详细格式 dict"""
    log.info(f"获取页面: {url}")
    try:
        resp = _http.get(url, timeout=REQUEST_TIMEOUT)
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
        resp = _http.get(list_url, timeout=REQUEST_TIMEOUT)
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


def _sort_links_by_date(links: list) -> list:
    """对链接按日期降序排列（最新的在前）"""
    def _key(item):
        _text, url = item
        m = re.search(r"/(\d{4})/(\d{2})/t\1\2(\d{2})", url)
        if m:
            return int(m.group(1) + m.group(2) + m.group(3))
        m2 = re.search(r"(\d{4})年(\d{1,2})月第(\d{1,2})周", _text)
        if m2:
            return int(m2.group(1) + m2.group(2).zfill(2) + m2.group(3).zfill(2))
        return 0
    return sorted(links, key=_key, reverse=True)


def fetch_latest() -> Optional[dict]:
    """获取最新一期（双源容错，取日期最新的链接）"""
    for source_name, cfg in SOURCES.items():
        links = _find_report_links(cfg["list_url"])
        if not links:
            continue
        links = _sort_links_by_date(links)
        title, url = links[0]
        log.info(f"  使用 {source_name} 源: {title[:40]}...")
        data = parse_report_page(url)
        if data:
            return data
    return None


def to_simple_format(record: dict) -> dict:
    """转换为3字段格式：period / price / yoy"""
    period = record.get("估算日期", "")
    if not period:
        y, m, w = record.get("年份"), record.get("月份"), record.get("第几周")
        if y and m and w:
            period = parse_week_to_date(y, m, w)

    price = record.get("生鲜乳价格_元每公斤")
    yoy = record.get("同比变化%")
    yoy_str = f"{yoy:+.1f}%" if yoy is not None else "N/A"

    return {"period": period, "price": price, "yoy": yoy_str}


# ═══════════════════════════════════════════════════════
# 第二部分：GitHub Releases 历史数据管理
# ═══════════════════════════════════════════════════════

def download_history_from_github() -> List[dict]:
    """从 GitHub Releases 下载历史数据（CSV 格式，兼容旧 JSON）"""
    # 优先用 API 下载（不受 CDN 缓存影响）
    if GITHUB_TOKEN:
        api_url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/tags/{RELEASE_TAG}"
        try:
            resp = _http.get(api_url, headers={
                "Authorization": f"token {GITHUB_TOKEN}",
                "Accept": "application/vnd.github+json",
            }, timeout=15)
            if resp.status_code == 200:
                assets = resp.json().get("assets", [])
                for asset in assets:
                    if asset["name"] == "history.csv":
                        dl_resp = _http.get(
                            f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/assets/{asset['id']}",
                            headers={
                                "Authorization": f"token {GITHUB_TOKEN}",
                                "Accept": "application/octet-stream",
                            },
                            timeout=15,
                        )
                        if dl_resp.status_code == 200:
                            reader = csv.DictReader(dl_resp.text.splitlines())
                            data = []
                            for row in reader:
                                price_raw = row.get("price", "")
                                try:
                                    price = float(price_raw)
                                except (ValueError, TypeError):
                                    price = price_raw or "N/A"
                                data.append({
                                    "period": row.get("period", ""),
                                    "price": price,
                                    "yoy": row.get("yoy", "N/A"),
                                })
                            log.info(f"[GitHub] 下载历史数据成功（CSV，{len(data)} 条）")
                            return data
                    elif asset["name"] == "history.json":
                        # 兼容旧 JSON 格式（自动迁移）
                        dl_resp = _http.get(
                            f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/assets/{asset['id']}",
                            headers={
                                "Authorization": f"token {GITHUB_TOKEN}",
                                "Accept": "application/octet-stream",
                            },
                            timeout=15,
                        )
                        if dl_resp.status_code == 200:
                            import json
                            data = dl_resp.json()
                            log.info(f"[GitHub] 下载历史数据成功（旧 JSON，{len(data)} 条），将自动迁移为 CSV")
                            return data
        except Exception as e:
            log.warning(f"[GitHub] API 下载失败: {e}")

    # 公共 URL 备选（不依赖 token，但可能受 CDN 缓存影响）
    csv_url = (
        f"https://github.com/{REPO_OWNER}/{REPO_NAME}"
        f"/releases/download/{RELEASE_TAG}/history.csv"
    )
    try:
        resp = _http.get(csv_url, timeout=15)
        if resp.status_code == 200:
            reader = csv.DictReader(resp.text.splitlines())
            data = []
            for row in reader:
                price_raw = row.get("price", "")
                try:
                    price = float(price_raw)
                except (ValueError, TypeError):
                    price = price_raw or "N/A"
                data.append({
                    "period": row.get("period", ""),
                    "price": price,
                    "yoy": row.get("yoy", "N/A"),
                })
            log.info(f"[GitHub] 下载历史数据成功（CSV，{len(data)} 条）")
            return data
    except Exception as e:
        log.warning(f"[GitHub] 下载 CSV 失败: {e}")

    # 兼容旧 JSON 格式
    json_url = (
        f"https://github.com/{REPO_OWNER}/{REPO_NAME}"
        f"/releases/download/{RELEASE_TAG}/history.json"
    )
    try:
        resp = _http.get(json_url, timeout=15)
        if resp.status_code == 200:
            import json
            data = resp.json()
            log.info(f"[GitHub] 下载历史数据成功（旧 JSON，{len(data)} 条），将自动迁移为 CSV")
            return data
        else:
            log.info(f"[GitHub] 历史数据不存在（HTTP {resp.status_code}），将从空开始")
            return []
    except Exception as e:
        log.warning(f"[GitHub] 下载历史数据失败: {e}，将从空开始")
        return []


def upload_history_to_github(history: List[dict]) -> bool:
    """上传 history.csv 到 GitHub Releases（不存在则创建，存在则更新）"""
    if not GITHUB_TOKEN:
        log.warning("未设置 MY_GITHUB_TOKEN，跳过上传")
        return False

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }

    # Step 1: 检查 Releases 是否存在
    rel_url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/tags/{RELEASE_TAG}"
    resp = _http.get(rel_url, headers=headers, timeout=15)
    release_id = None
    if resp.status_code == 200:
        release_id = resp.json()["id"]
        log.info(f"[GitHub] Releases 已存在（id={release_id}）")
    else:
        create_url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases"
        payload = {
            "tag_name": RELEASE_TAG,
            "name": "原奶价格历史数据",
            "body": "自动更新的生鲜乳主产区周度价格历史数据（最近52周）\n\n数据来源：农业农村部畜牧兽医局",
            "draft": False,
            "prerelease": False,
        }
        r = _http.post(create_url, headers=headers, json=payload, timeout=15)
        if r.status_code in (200, 201):
            release_id = r.json()["id"]
            log.info(f"[GitHub] Releases 创建成功（id={release_id}）")
        else:
            log.error(f"[GitHub] 创建 Releases 失败: {r.text}")
            return False

    # Step 2: 删除旧附件（兼容 history.csv 和旧 history.json）
    if release_id:
        assets_url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/{release_id}/assets"
        r = _http.get(assets_url, headers=headers, timeout=15)
        if r.status_code == 200:
            for asset in r.json():
                if asset["name"] in ("history.csv", "history.json"):
                    del_url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/assets/{asset['id']}"
                    _http.delete(del_url, headers=headers, timeout=15)
                    log.info(f"[GitHub] 已删除旧附件 {asset['name']}")

    # Step 3: 写 CSV 并上传
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_CSV, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=HISTORY_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for rec in history:
            writer.writerow(rec)

    upload_url = (
        f"https://uploads.github.com/repos/{REPO_OWNER}/{REPO_NAME}"
        f"/releases/{release_id}/assets?name=history.csv"
    )
    with open(HISTORY_CSV, "rb") as f:
        r = _http.post(
            upload_url,
            headers={**headers, "Content-Type": "text/csv"},
            data=f,
            timeout=30,
        )
    if r.status_code in (200, 201):
        log.info(f"[GitHub] history.csv 上传成功（{len(history)} 条）")
        return True
    else:
        log.error(f"[GitHub] 上传失败: {r.status_code} {r.text}")
        return False


def load_history_from_csv() -> list:
    """
    从本地 history.csv 恢复历史数据（本地开发用，Actions 环境无此文件）
    读取 3字段格式 CSV
    """
    if not HISTORY_CSV.exists():
        return []

    history = []
    with open(HISTORY_CSV, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            price_raw = row.get("price", "")
            try:
                price = float(price_raw)
            except (ValueError, TypeError):
                price = price_raw or "N/A"
            rec = {
                "period": row.get("period", "").strip(),
                "price": price,
                "yoy": row.get("yoy", "N/A"),
            }
            if rec["period"]:
                history.append(rec)

    # 去重 + 按 period 降序
    seen = set()
    unique = []
    for r in history:
        if r["period"] not in seen:
            seen.add(r["period"])
            unique.append(r)
    unique.sort(key=lambda x: x["period"], reverse=True)

    log.info(f"[CSV] 从本地 history.csv 恢复历史数据（{len(unique)} 条）")
    return unique


def deduplicate_and_keep_recent(history: List[dict], new_records: List[dict], max_records: int = 52) -> List[dict]:
    """
    去重合并：将新采集的详细格式数据合并到历史（3字段格式）
    保留最近 max_records 条，按 period 降序
    """
    new_simple = [to_simple_format(r) for r in new_records]

    existing_periods = {r["period"] for r in history}
    added = 0
    for rec in new_simple:
        if rec["period"] not in existing_periods:
            history.append(rec)
            existing_periods.add(rec["period"])
            added += 1

    history.sort(key=lambda x: x["period"], reverse=True)
    result = history[:max_records]

    if added:
        log.info(f"[合并] 新增 {added} 条，合并后共 {len(result)} 条")
    else:
        log.info(f"[合并] 无新增数据，历史共 {len(result)} 条")

    return result


# ═══════════════════════════════════════════════════════
# 第三部分：企业微信推送
# ═══════════════════════════════════════════════════════

def push_to_wechat(title: str, content: str, token: str) -> bool:
    if not token:
        log.warning("未设置 PUSH_TOKEN，跳过推送")
        return False

    url = f"https://push.showdoc.com.cn/server/api/push/{token}"
    try:
        resp = _http.post(url, data={"title": title, "content": content}, timeout=15)
        result = resp.json()
        if result.get("error_code") == 0:
            log.info("推送到企业微信成功")
            return True
        else:
            log.error(f"推送失败: {result}")
            return False
    except Exception as e:
        log.error(f"推送异常: {e}")
        return False


def build_content(history: List[dict], latest_detailed: dict = None) -> tuple:
    """
    构建推送标题和内容
    - history: 3字段格式列表（period/price/yoy），按 period 降序
    - latest_detailed: 最新一期详细格式（含月份/周次/数据源URL）
    """
    today = datetime.now().strftime('%Y-%m-%d')

    # ── 无新数据：推送失败通知（附缓存历史）───
    if latest_detailed is None:
        title = "原奶数据抓取失败"
        content = (
            f"# 原奶数据抓取失败（{today}）\n\n"
            "> 所有数据源均未获取到最新一周数据，请手动检查。\n\n"
        )
        if not history:
            content += "> 连缓存也没有，数据源可能长期中断。"
        else:
            content += "最近缓存数据（最近7周）：\n"
            for p in history[:7]:
                content += f"- {p['period']}: {p['price']}元/kg (同比 {p['yoy']})\n"
        return title, content

    # ── 有新数据 ─────────────────────────────
    # 空值保护：所有字段可能为 None
    month = latest_detailed.get("月份") or "?"
    week  = latest_detailed.get("第几周") or "?"
    price = latest_detailed.get("生鲜乳价格_元每公斤")
    price_str = str(price) if price is not None else "N/A"
    yoy   = latest_detailed.get("同比变化%")

    yoy_str = f"{yoy:+.1f}%" if yoy is not None else "N/A"
    title   = f"原奶{month}月第{week}周 {price_str} 同比{yoy_str}"

    # 构建表格行（history 是 3字段格式，取最近7周）
    rows = []
    for p in history[:7]:
        price_str_row = str(p["price"]) if isinstance(p["price"], (int, float)) else str(p["price"])
        rows.append(f"| {p['period']} | {price_str_row.ljust(14)} | {p['yoy'].ljust(10)} |")

    # 数据源说明 + 原文链接 + 发布时间
    source_note = "\n\n> 数据来源：农业农村部畜牧兽医局"
    publish_date = latest_detailed.get("发布日期") or ""
    if publish_date:
        source_note += f"  |  发布时间：{publish_date}"
    source_url = latest_detailed.get("数据来源URL") or ""
    if source_url:
        source_note += f'  |  <a href="{source_url}" target="_blank">查看原文</a>'

    # 近期趋势
    prices_valid = [
        p["price"] for p in history
        if isinstance(p.get("price"), (int, float)) and p["price"] != "N/A"
    ]
    trend = ""
    if prices_valid:
        trend = (
            f"\n\n近期趋势：价格维持在 "
            f"{min(prices_valid):.2f}-{max(prices_valid):.2f} "
            f"元/kg 区间，整体处于低位磨底阶段。"
        )

    # 拼接完整内容
    content  = "# 原奶收购价周报\n\n"
    content += "| 日期 | 均价（元/kg） | 同比变化 |\n"
    content += "| ----- | --------------- | ---------- |\n"
    content += "\n".join(rows) + "\n"
    content += source_note
    content += trend

    return title, content


# ═══════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="生鲜乳价格每日推送")
    parser.add_argument("--no-push",   action="store_true", help="不推送到企微")
    parser.add_argument("--no-upload", action="store_true", help="不上传到 GitHub Releases")
    args = parser.parse_args()

    print("=" * 50)
    print("原奶价格采集 + 推送")
    print("=" * 50)

    # Step 1: 从 GitHub Releases 下载历史数据
    print("\n[1/5] 从 GitHub Releases 下载历史数据...")
    history = download_history_from_github()

    # Step 1.5: 合并本地 CSV 历史数据（本地开发用，Actions 环境无 CSV）
    csv_history = load_history_from_csv()
    if csv_history:
        existing = {r["period"] for r in history}
        added = 0
        for rec in csv_history:
            if rec["period"] not in existing:
                history.append(rec)
                existing.add(rec["period"])
                added += 1
        history.sort(key=lambda x: x["period"], reverse=True)
        print(f"  合并本地 CSV 后共 {len(history)} 条（新增 {added} 条）")

    # Step 2: 爬取最新一期数据
    print("\n[2/5] 爬取最新一周数据...")
    latest = fetch_latest()

    if latest:
        print(f"  最新: {latest['估算日期']} {latest['生鲜乳价格_元每公斤']} 元/公斤")
    else:
        print("\n  所有数据源均未抓取到新数据")

    # Step 3: 去重合并历史数据
    print("\n[3/5] 合并历史数据...")
    if latest:
        history = deduplicate_and_keep_recent(history, [latest])
    else:
        print("  无新数据，使用缓存历史")

    # Step 4: 上传到 GitHub Releases
    print("\n[4/5] 上传到 GitHub Releases...")
    if not args.no_upload:
        upload_history_to_github(history)
    else:
        print("  跳过上传（--no-upload）")

    # Step 5: 推送到企业微信
    print("\n[5/5] 推送到企业微信...")
    if not args.no_push and PUSH_TOKEN:
        title, content = build_content(history, latest)
        push_to_wechat(title, content, PUSH_TOKEN)
    else:
        title = "(未推送)"
        print("  跳过推送（--no-push 或 未设置 PUSH_TOKEN）")

    # 摘要
    print("\n" + "=" * 50)
    if latest:
        yoy = latest.get('同比变化%')
        yoy_s = f"{yoy:+.1f}%" if yoy is not None else "N/A"
        print(f"推送标题: 原奶{latest.get('月份', '?')}月第{latest.get('第几周', '?')}周 {latest.get('生鲜乳价格_元每公斤', 'N/A')} 同比{yoy_s}")
    else:
        print("无新数据，已推送失败通知")
    print(f"历史数据: 共 {len(history)} 条")
    print(f"稳定访问: https://github.com/{REPO_OWNER}/{REPO_NAME}/releases/tag/{RELEASE_TAG}")
    print("=" * 50)


if __name__ == "__main__":
    main()
