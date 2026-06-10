# ETL 清洗逻辑说明

## 1. 清洗步骤

ETL 清洗过程按照以下步骤执行：

1. 去重
   - 读取 `raw_rent` 中尚未写入 `clean_rent` 的原始房源数据
   - 通过 `house_id` 去重，避免同一房源重复处理

2. 过滤异常
   - 按 `etl_config.yaml` 中的 `price_range` 和 `area_range` 过滤极端值
   - 过滤掉面积或租金不在合理区间的记录

3. 字段拆分
   - `bizcircle` 和 `district` 字段组合拆分行政区、商圈和小区
   - 将 `layout` 中的 `X室Y厅` 提取为 `rooms` 和 `halls`

4. 单价计算
   - 计算 `price_per_sqm = price / area`
   - 结果保留到 `clean_rent` 中，作为后续分析指标

5. 异常值标记
   - 基于 `price_per_sqm` 使用 IQR 方法检测异常值
   - 默认按 `district` 分区计算 IQR，样本不足时回退到全局 IQR
   - 异常记录在字段 `is_abnormal` 标记为 `1`

## 2. `etl_config.yaml` 参数说明

```yaml
cleaning:
  price_range: [200, 50000]
  area_range: [10, 500]
  iqr_multiplier: 1.5
  district_mapping:
    锦江: 锦江区
    青羊: 青羊区
    金牛: 金牛区
    武侯: 武侯区
    成华: 成华区
    高新: 高新区
    天府: 天府新区
    双流: 双流区
  missing_strategy:
    rent_amount: drop
    area: drop
    decoration: mode
  floor_mapping:
    低楼层: low
    中楼层: mid
    高楼层: high
  decoration_mapping:
    精装: luxury
    简装: fine
    毛坯: unfurnished
```

参数含义：

- `price_range`：租金过滤范围，低于最低值或高于最高值的记录被视为异常并剔除。
- `area_range`：面积过滤范围，低于最低值或高于最高值的记录被视为异常并剔除。
- `iqr_multiplier`：IQR 异常检测的倍数系数，默认 `1.5`。
- `district_mapping`：原始行政区名称到标准化行政区的映射，用于规范化 `district` 字段。
- `missing_strategy`：缺失字段处理策略，例如房租金额或面积缺失时采取 `drop` 丢弃。
- `floor_mapping`：楼层描述词到标准枚举值的映射。
- `decoration_mapping`：装修描述词到标准枚举值的映射。

## 3. 增量清洗机制

ETL 脚本采用增量清洗方式：

- 从 `raw_rent` 中读取所有记录
- 通过 `LEFT JOIN clean_rent ON raw_rent.house_id = clean_rent.house_id`
- 只处理 `clean_rent.house_id IS NULL` 的记录

这样可以确保：

- 已经处理过的房源不会重复写入
- 新抓取的原始数据可以被增量加入清洗流程
- 适合定时任务场景，避免全量重跑

## 4. 清洗前后数据量对比（示例）

| 阶段 | 记录数 | 说明 |
| --- | --- | --- |
| 原始抓取 `raw_rent` | 277 | 爬虫抓取数据总量 |
| 已清洗 `clean_rent` | 267 | ETL 过滤异常后写入数据量 |
| 丢弃记录 | 10 | 价格或面积异常、重复、缺失等原因 |

说明：上述示例来自当前数据库环境，实际数值因后续抓取和清洗策略调整会变化。

## 5. 如何运行清洗脚本

### 手动运行

在项目根目录下执行：

```bash
python etl/clean_data.py
```

### 定时任务

建议使用操作系统定时调度工具：

- Linux / macOS：`cron`
- Windows：任务计划程序

定时任务步骤示例：

1. 在脚本所在项目根目录创建虚拟环境并安装依赖
2. 编写 shell 或 PowerShell 脚本调用 `python etl/clean_data.py`
3. 将该脚本添加到计划任务，每小时或每天执行一次

定时任务效果：

- 自动加载新增原始数据
- 增量写入 `clean_rent`
- 保持数据仓库最新
