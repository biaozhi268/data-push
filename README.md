# 原奶价格监测推送 (milk-price-push)

自动采集中国生鲜乳（原奶）主产区周度价格，每日定时推送到企业微信。

## 数据来源

[农业农村部畜牧兽医局](https://xmsyj.moa.gov.cn/jcyj/) — 每周发布的"畜产品和饲料集贸市场价格情况"周报。

- **指标**: 内蒙古、河北等10个主产省份生鲜乳平均价格（元/公斤）
- **频率**: 每周一次（周二/周三更新）
- **备用源**: [全国畜牧总站](https://www.nahs.org.cn/jcyj/jghq/)（双源容错）

## 工作流

```
GitHub Actions (每天 08:00 北京时间)
    │
    ├─ 1. 从 Releases 下载 history.csv（历史数据）
    ├─ 2. 爬取农业农村部最新一期周报
    ├─ 3. 去重合并（period 去重，保留最近52周）
    ├─ 4. 上传 history.csv 到 GitHub Releases
    └─ 5. 推送到企业微信（最近7周数据 + 原文链接）
```

### 数据存储

历史数据存储在 [GitHub Releases](https://github.com/biaozhi268/milk-price-push/releases/tag/history) 附件中（CSV 格式），可通过稳定 URL 直接访问：

```
https://github.com/biaozhi268/milk-price-push/releases/download/history/history.csv
```

### 数据格式（CSV，3字段）

```csv
period,price,yoy
2026-06-25,3.03,-0.3%
2026-06-18,3.03,-0.3%
```

| 字段 | 说明 |
|------|------|
| period | 报告周估算日期（周三），格式 YYYY-MM-DD |
| price | 生鲜乳平均价格（元/公斤） |
| yoy | 同比变化，如 "-0.3%" 或 "N/A" |

## 文件结构

```
├── daily_push.py              # 主脚本：采集 + 上传 + 推送
├── .github/workflows/daily.yml # GitHub Actions 每日定时运行
├── requirements.txt           # Python 依赖
└── .gitignore
```

## 本地运行

```bash
pip install -r requirements.txt

# 完整流程（采集 + 上传 Releases + 推送企微）
python daily_push.py

# 只采集上传，不推送
python daily_push.py --no-push

# 只采集推送，不上传 Releases
python daily_push.py --no-upload
```

### 环境变量

| 变量 | 说明 |
|------|------|
| `MY_GITHUB_TOKEN` | GitHub PAT，用于上传 Releases（需 repo 权限） |
| `PUSH_TOKEN` | push.showdoc.com.cn 的推送 token |

## 配置 GitHub Secrets

在 [仓库设置 → Secrets → Actions](https://github.com/biaozhi268/milk-price-push/settings/secrets/actions) 中添加：

1. **`MY_GITHUB_TOKEN`** — GitHub PAT（需 `repo` 权限），用于 Actions 自动上传 Releases
2. **`PUSH_TOKEN`** — push.showdoc.com.cn 的 token，用于推送企业微信通知

## 推送内容示例

```
标题: 原奶6月第4周 3.03 同比-0.3%

# 原奶收购价周报

| 日期       | 均价（元/kg）   | 同比变化   |
| ---------- | --------------- | ---------- |
| 2026-06-25 | 3.03            | -0.3%      |
| 2026-06-18 | 3.03            | -0.3%      |
...（共7周）

> 数据来源：农业农村部畜牧兽医局  |  发布时间：2026-07-01  |  查看原文

近期趋势：价格维持在 3.02-3.03 元/kg 区间，整体处于低位磨底阶段。
```

## License

MIT
