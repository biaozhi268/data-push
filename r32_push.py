#!/usr/bin/env python3
"""
R32 制冷剂价格采集 - 每日推送脚本
======================================

完整工作流（对齐奶价项目 daily_push.py 逻辑）：
1. 从 GitHub Releases 下载 r32_history.csv（历史数据）
2. 爬取 sci99.com 最新一期价格数据
3. 去重合并到历史数据（保留近3年）
4. 上传 r32_history.csv 到 GitHub Releases
5. 推送到企业微信（最近30天数据）

数据格式：4字段（date / price / change / change_percent），CSV 存储
存储方式：GitHub Releases 附件（稳定URL直接访问）

用法:
    python r32_push.py              # 完整流程
    python r32_push.py --no-push   # 只采集上传，不推送
    python r32_push.py --no-upload # 只采集推送，不上传Releases
"""

import os
import csv
import argparse
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List

import requests
from bs4 import BeautifulSoup

# ── 配置 ──────────────────────────────────────────────────
# 数据源：sci99.com R32 价格监控接口
SOURCE_URL = "https://www.sci99.com/priceMonitor/listProductPagePrice?oldId=1572&type=0"

DATA_DIR = Path(__file__).parent / "data"
HISTORY_CSV = DATA_DIR / "r32_history.csv"
HISTORY_FIELDS = ["date", "price", "change", "change_percent"]
MAX_RECORDS = 365 * 3  # 保留近3年
REQUEST_TIMEOUT = 30

# GitHub 配置（用于 Releases 上传/下载）
REPO_OWNER = "biaozhi268"
REPO_NAME   = "data-push"
RELEASE_TAG = "r32-history"

# 推送配置（与奶价共用 PUSH_TOKEN）
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
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
})


# ══════════════════════════════════════════════════════
# 第一部分：数据采集（sci99.com JSON 接口）
# ══════════════════════════════════════════════════════

def fetch_latest() -> Optional[dict]:
    """
    从 sci99.com 获取最新一期 R32 价格数据
    返回详细格式 dict，或 None（失败）
    """
    log.info(f"请求数据源: {SOURCE_URL}")
    try:
        # 显式禁用代理，确保直连
        proxies = {"http": None, "https": None}
        resp = _http.get(SOURCE_URL, timeout=REQUEST_TIMEOUT, proxies=proxies)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.error(f"请求失败: {e}")
        return None

    try:
        data = resp.json()
    except ValueError as e:
        log.error(f"JSON 解析失败: {e}")
        return None

    if data.get("code") != 200 or not data.get("data"):
        log.error(f"返回数据格式不正确: {data}")
        return None

    # 取第一条（最新一期）
    item = data["data"][0]
    try:
        date_str = item.get("dateRange", "")
        if not date_str:
            log.warning("数据无日期字段")
            return None
        price_str = item.get("mdataValue", "")
        if not price_str:
            log.warning("数据无价格字段")
            return None

        price = float(price_str)
        change_str = item.get("change")
        change = float(change_str) if change_str else 0.0

        change_percent_str = item.get("changeRate")
        if change_percent_str:
            change_percent_str = change_percent_str.replace('%', '')
            change_percent = float(change_percent_str) if change_percent_str else 0.0
        else:
            change_percent = 0.0

        result = {
            "date": date_str,
            "price": price,
            "change": change,
            "change_percent": change_percent,
            "采集时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        log.info(f"获取成功: 日期={date_str}, 价格={price}, 涨跌={change}({change_percent}%)")
        return result
    except (ValueError, KeyError) as e:
        log.error(f"解析数据失败: {e}")
        return None


# ══════════════════════════════════════════════════════
# 第二部分：GitHub Releases 历史数据管理
# ══════════════════════════════════════════════════════

def download_history_from_github() -> List[dict]:
    """从 GitHub Releases 下载 r32 历史数据（CSV 格式）"""
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
                    if asset["name"] == "r32_history.csv":
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
                                try:
                                    price = float(row.get("price", 0))
                                except (ValueError, TypeError):
                                    price = 0.0
                                try:
                                    change = float(row.get("change", 0))
                                except (ValueError, TypeError):
                                    change = 0.0
                                try:
                                    change_percent = float(row.get("change_percent", 0))
                                except (ValueError, TypeError):
                                    change_percent = 0.0
                                data.append({
                                    "date": row.get("date", ""),
                                    "price": price,
                                    "change": change,
                                    "change_percent": change_percent,
                                })
                            log.info(f"[GitHub] 下载历史数据成功（CSV，{len(data)} 条）")
                            return data
        except Exception as e:
            log.warning(f"[GitHub] API 下载失败: {e}")

    # 公共 URL 备选
    csv_url = (
        f"https://github.com/{REPO_OWNER}/{REPO_NAME}"
        f"/releases/download/{RELEASE_TAG}/r32_history.csv"
    )
    try:
        resp = _http.get(csv_url, timeout=15)
        if resp.status_code == 200:
            reader = csv.DictReader(resp.text.splitlines())
            data = []
            for row in reader:
                try:
                    price = float(row.get("price", 0))
                except (ValueError, TypeError):
                    price = 0.0
                try:
                    change = float(row.get("change", 0))
                except (ValueError, TypeError):
                    change = 0.0
                try:
                    change_percent = float(row.get("change_percent", 0))
                except (ValueError, TypeError):
                    change_percent = 0.0
                data.append({
                    "date": row.get("date", ""),
                    "price": price,
                    "change": change,
                    "change_percent": change_percent,
                })
            log.info(f"[GitHub] 下载历史数据成功（CSV，{len(data)} 条）")
            return data
    except Exception as e:
        log.warning(f"[GitHub] 下载 CSV 失败: {e}")

    log.info("[GitHub] 历史数据不存在，将从空开始")
    return []


def upload_history_to_github(history: List[dict]) -> bool:
    """上传 r32_history.csv 到 GitHub Releases"""
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
            "name": "R32价格历史数据",
            "body": "自动更新的R32制冷剂每日价格历史数据（最近3年）\n\n数据来源：sci99.com",
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

    # Step 2: 删除旧附件
    if release_id:
        assets_url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/{release_id}/assets"
        r = _http.get(assets_url, headers=headers, timeout=15)
        if r.status_code == 200:
            for asset in r.json():
                if asset["name"] == "r32_history.csv":
                    del_url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/assets/{asset['id']}"
                    _http.delete(del_url, headers=headers, timeout=15)
                    log.info(f"[GitHub] 已删除旧附件 r32_history.csv")

    # Step 3: 写 CSV 并上传
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_CSV, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=HISTORY_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for rec in history:
            writer.writerow(rec)

    upload_url = (
        f"https://uploads.github.com/repos/{REPO_OWNER}/{REPO_NAME}"
        f"/releases/{release_id}/assets?name=r32_history.csv"
    )
    with open(HISTORY_CSV, "rb") as f:
        r = _http.post(
            upload_url,
            headers={**headers, "Content-Type": "text/csv"},
            data=f,
            timeout=30,
        )
    if r.status_code in (200, 201):
        log.info(f"[GitHub] r32_history.csv 上传成功（{len(history)} 条）")
        return True
    else:
        log.error(f"[GitHub] 上传失败: {r.status_code} {r.text}")
        return False


def load_history_from_csv() -> list:
    """从本地 r32_history.csv 恢复历史数据（本地开发用）"""
    if not HISTORY_CSV.exists():
        return []
    history = []
    with open(HISTORY_CSV, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                price = float(row.get("price", 0))
            except (ValueError, TypeError):
                price = 0.0
            try:
                change = float(row.get("change", 0))
            except (ValueError, TypeError):
                change = 0.0
            try:
                change_percent = float(row.get("change_percent", 0))
            except (ValueError, TypeError):
                change_percent = 0.0
            rec = {
                "date": row.get("date", "").strip(),
                "price": price,
                "change": change,
                "change_percent": change_percent,
            }
            if rec["date"]:
                history.append(rec)

    # 去重 + 按 date 降序
    seen = set()
    unique = []
    for r in history:
        if r["date"] not in seen:
            seen.add(r["date"])
            unique.append(r)
    unique.sort(key=lambda x: x["date"], reverse=True)
    log.info(f"[CSV] 从本地 r32_history.csv 恢复历史数据（{len(unique)} 条）")
    return unique


def deduplicate_and_keep_recent(history: List[dict], new_record: dict, max_records: int = MAX_RECORDS) -> List[dict]:
    """
    去重合并：将新采集的数据合并到历史
    保留最近 max_records 条，按 date 降序
    """
    existing_dates = {r["date"] for r in history}
    added = 0
    if new_record and new_record.get("date") not in existing_dates:
        history.append(new_record)
        added += 1

    history.sort(key=lambda x: x["date"], reverse=True)
    result = history[:max_records]

    if added:
        log.info(f"[合并] 新增 {added} 条，合并后共 {len(result)} 条")
    else:
        log.info(f"[合并] 无新增数据，历史共 {len(result)} 条")

    return result


# ══════════════════════════════════════════════════════
# 第三部分：企业微信推送
# ══════════════════════════════════════════════════════

def push_to_wechat(title: str, content: str, token: str) -> bool:
    """推送到企业微信（与奶价共用 push.showdoc.com.cn）"""
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


def build_content(history: List[dict], latest: dict = None) -> tuple:
    """
    构建推送标题和内容
    - history: 4字段格式列表（date/price/change/change_percent），按 date 降序
    - latest: 最新一期详细格式
    """
    today = datetime.now().strftime('%Y-%m-%d')

    # ── 无新数据：推送失败通知 ────────────────────────
    if latest is None:
        title = "R32数据抓取失败"
        content = (
            f"# R32数据抓取失败（{today}）\n\n"
            "> 数据源 sci99.com 未获取到最新价格数据，请手动检查。\n\n"
        )
        if not history:
            content += "> 连缓存也没有，数据源可能长期中断。"
        else:
            content += "最近缓存数据（最近10条）：\n"
            for p in history[:10]:
                change_str = f"{p['change']:+.0f}" if isinstance(p.get("change"), (int, float)) else "0"
                cp_str = f"{p['change_percent']:+.1f}%" if isinstance(p.get("change_percent"), (int, float)) else "0%"
                content += f"- {p['date']}: {p['price']:.0f}元/吨 (涨跌{change_str}元, {cp_str})\n"
        return title, content

    # ── 有新数据 ─────────────────────────────
    price = latest.get("price", 0)
    change = latest.get("change", 0)
    change_percent = latest.get("change_percent", 0)

    title = f"R32 {latest['date']} {price:.0f}元/吨 涨跌{change:+.0f}元"

    # 构建表格行（取最近10条）
    rows = []
    for p in history[:10]:
        price_str = f"{p['price']:.0f}" if isinstance(p.get("price"), (int, float)) else str(p["price"])
        change_str = f"{p['change']:+.0f}" if isinstance(p.get("change"), (int, float)) else "0"
        cp_str = f"{p['change_percent']:+.1f}%" if isinstance(p.get("change_percent"), (int, float)) else "0%"
        rows.append(f"| {p['date']} | {price_str.ljust(8)} | {change_str.ljust(8)} | {cp_str.ljust(10)} |")

    # 近期趋势
    prices_valid = [
        p["price"] for p in history
        if isinstance(p.get("price"), (int, float)) and p["price"] > 0
    ]
    trend = ""
    if len(prices_valid) >= 2:
        trend = (
            f"\n\n近期趋势：价格介于 "
            f"{min(prices_valid):.0f}-{max(prices_valid):.0f} "
            f"元/吨区间。"
        )

    # 拼接完整内容
    content  = "# R32制冷剂价格日报\n\n"
    content += "| 日期 | 价格(元/吨) | 涨跌(元) | 涨跌幅 |\n"
    content += "| ----- | ------------ | --------- | -------- |\n"
    content += "\n".join(rows) + "\n"
    content += '\n> 数据来源：<a href="https://www.sci99.com/monitor-1572-0.html" target="_blank">sci99.com R32价格监控</a>'
    content += trend

    return title, content


# ══════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="R32价格每日推送")
    parser.add_argument("--no-push",   action="store_true", help="不推送到企微")
    parser.add_argument("--no-upload", action="store_true", help="不上传到 GitHub Releases")
    args = parser.parse_args()

    print("=" * 50)
    print("R32 价格采集 + 推送")
    print("=" * 50)

    # Step 1: 从 GitHub Releases 下载历史数据
    print("\n[1/5] 从 GitHub Releases 下载历史数据...")
    history = download_history_from_github()

    # Step 1.5: 合并本地 CSV 历史数据（本地开发用）
    csv_history = load_history_from_csv()
    if csv_history:
        existing = {r["date"] for r in history}
        added = 0
        for rec in csv_history:
            if rec["date"] not in existing:
                history.append(rec)
                existing.add(rec["date"])
                added += 1
        history.sort(key=lambda x: x["date"], reverse=True)
        print(f"  合并本地 CSV 后共 {len(history)} 条（新增 {added} 条）")

    # Step 2: 爬取最新一期数据
    print("\n[2/5] 爬取最新一期数据...")
    latest = fetch_latest()

    if latest:
        print(f"  最新: {latest['date']} {latest['price']} 元/吨")
    else:
        print("\n  数据源未抓取到新数据")

    # Step 3: 去重合并历史数据
    print("\n[3/5] 合并历史数据...")
    if latest:
        history = deduplicate_and_keep_recent(history, latest)
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
        print("  跳过推送（--no-push 或 未设置 PUSH_TOKEN）")

    # 摘要
    print("\n" + "=" * 50)
    if latest:
        print(f"推送标题: R32 {latest['date']} {latest['price']:.0f}元/吨 涨跌{latest['change']:+.0f}元")
    else:
        print("无新数据，已推送失败通知")
    print(f"历史数据: 共 {len(history)} 条")
    print(f"稳定访问: https://github.com/{REPO_OWNER}/{REPO_NAME}/releases/tag/{RELEASE_TAG}")
    print("=" * 50)


if __name__ == "__main__":
    main()
