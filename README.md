# 数据推送

自动采集原奶、R32制冷剂价格，每日定时推送到企业微信。

## 包含项目

| 项目 | 脚本 | 数据来源 | 更新频率 | 历史保留 |
|------|------|----------|----------|----------|
| 原奶价格 | `daily_push.py` | 农业农村部畜牧兽医局周报 | 每天 08:00 | 最近10年 |
| R32制冷剂 | `r32_push.py` | sci99.com 价格监控 | 每天 08:00 | 最近3年 |

## 工作流（两者一致）

```
GitHub Actions 定时触发
    │
    ├─ 1. 从 Releases 下载历史 CSV
    ├─ 2. 爬取最新一期价格数据
    ├─ 3. 去重合并（按日期去重，保留最近N条）
    ├─ 4. 上传 CSV 到 GitHub Releases
    └─ 5. 推送到企业微信（含近期数据表格）
```

## 数据存储

历史数据存储在 GitHub Releases 附件中（CSV 格式），两个项目使用不同的 Release Tag：

| 项目 | Release Tag | 文件名 | 稳定访问 URL |
|------|-------------|--------|-------------|
| 原奶 | `history` | `history.csv` | `https://github.com/biaozhi268/data-push/releases/download/history/history.csv` |
| R32 | `r32-history` | `r32_history.csv` | `https://github.com/biaozhi268/data-push/releases/download/r32-history/r32_history.csv` |

## 数据格式

### 原奶（3字段）

```csv
period,price,yoy
2026-06-25,3.03,-0.3%
2026-06-18,3.03,-0.3%
```

| 字段 | 说明 |
|------|------|
| period | 报告周估算日期（周三），YYYY-MM-DD |
| price | 生鲜乳平均价格（元/公斤） |
| yoy | 同比变化，如 "-0.3%" |

### R32（4字段）

```csv
date,price,change,change_percent
2026-07-03,62500.0,0.0,0.0
2026-07-02,62500.0,0.0,0.0
```

| 字段 | 说明 |
|------|------|
| date | 价格日期，YYYY-MM-DD |
| price | R32价格（元/吨） |
| change | 涨跌额（元） |
| change_percent | 涨跌幅（%） |

## 文件结构

```
├── daily_push.py                    # 原奶价格脚本（采集 + 上传 + 推送）
├── r32_push.py                      # R32价格脚本（采集 + 上传 + 推送）
├── .github/workflows/
│   ├── daily.yml                    # 原奶定时任务（每天 08:00 北京时间）
│   └── r32_daily.yml                # R32定时任务（每天 08:00 北京时间）
├── requirements.txt                 # Python 依赖
├── data/                            # 本地缓存（.gitignore 忽略）
└── README.md
```

## 本地运行

```bash
pip install -r requirements.txt

# 原奶
python daily_push.py                 # 完整流程
python daily_push.py --no-push       # 只采集上传，不推送
python daily_push.py --no-upload     # 只采集推送，不上传 Releases

# R32
python r32_push.py                   # 完整流程
python r32_push.py --no-push         # 只采集上传，不推送
python r32_push.py --no-upload       # 只采集推送，不上传 Releases
```

## 环境变量

| 变量 | 说明 | 两个项目共用 |
|------|------|-------------|
| `MY_GITHUB_TOKEN` | GitHub PAT（需 repo 权限），用于上传 Releases | 是 |
| `PUSH_TOKEN` | push.showdoc.com.cn 的推送 token | 是 |

## 配置 GitHub Secrets

在 [仓库设置 → Secrets → Actions](https://github.com/biaozhi268/data-push/settings/secrets/actions) 中添加：

1. **`MY_GITHUB_TOKEN`** — GitHub PAT（需 `repo` 权限），用于 Actions 自动上传 Releases
2. **`PUSH_TOKEN`** — push.showdoc.com.cn 的 token，用于推送企业微信通知

## 推送内容示例

### 原奶

```
标题: 原奶6月第4周 3.03 同比-0.3%

# 原奶收购价周报

| 日期       | 均价（元/kg）   | 同比变化   |
| ---------- | --------------- | ---------- |
| 2026-06-25 | 3.03            | -0.3%      |
| 2026-06-18 | 3.03            | -0.3%      |
...（共7周）

> 数据来源：农业农村部畜牧兽医局  |  发布时间：2026-07-01  |  查看原文
```

### R32

```
标题: R32 2026-07-03 62500元/吨 涨跌+0元

# R32制冷剂价格日报

| 日期       | 价格(元/吨) | 涨跌(元)  | 涨跌幅     |
| ---------- | ------------ | --------- | --------   |
| 2026-07-03 | 62500        | +0        | +0.0%      |
| 2026-07-02 | 62500        | +0        | +0.0%      |
...（共10条）

> 数据来源：sci99.com
```

## License

MIT
