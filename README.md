# Raw Milk Price Monitor (中国生鲜乳价格监测)

自动采集中国生鲜乳（原奶）主产区周度价格数据。

## 数据来源

[农业农村部畜牧兽医局](https://xmsyj.moa.gov.cn/jcyj/) — 每周发布的"畜产品和饲料集贸市场价格情况"周报。

- **指标**: 内蒙古、河北等10个主产省份生鲜乳平均价格（元/公斤）
- **频率**: 每周一次（周二/周三更新）
- **历史**: 2010年起持续发布

## 快速开始

```bash
pip install -r requirements.txt

# 采集最新一期
python scraper.py

# 回溯最近 50 期
python scraper.py --history 50

# 采集全部可获取的历史
python scraper.py --all

# 输出 JSON（不存 CSV）
python scraper.py --json
```

## 数据格式

CSV 文件保存在 `data/raw_milk_price.csv`，字段说明：

| 字段 | 说明 |
|------|------|
| 报告标题 | 周报标题 |
| 估算日期 | 报告覆盖周的估算日期（周四） |
| 采集日 | 实际采集日 |
| 发布日期 | 报告发布日期 |
| 生鲜乳价格_元每公斤 | 10个主产省份平均价格 |
| 环比变化% | 与前一周比较 |
| 同比变化% | 与去年同期比较 |
| 数据来源URL | 原始报告链接 |

## 自动化运行

建议通过 cron / Task Scheduler 每周四定时运行：

```bash
# crontab (Linux/macOS)
0 10 * * 4 cd /path/to/raw_milk_monitor && python scraper.py

# Windows Task Scheduler: 新建基本任务，触发器设为每周四
```

## License

MIT
