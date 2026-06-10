# AI点评生成说明

## 1. 目的

本说明文档介绍如何调用 DeepSeek API 生成成都市各行政区的租房点评，重点涵盖：

- DeepSeek API 调用流程
- `clean_rent` 数据按行政区聚合方式
- Prompt 设计示例
- 生成点评示例
- 运行脚本 `ai_scripts/generate_district_comments.py`

---

## 2. DeepSeek API 生成流程

### 2.1 脚本入口

主要脚本：`ai_scripts/generate_district_comments.py`

脚本流程：

1. 从 `config/config.yaml` 读取数据库连接配置
2. 从 `config/ai_config.yaml` 读取 AI 模型配置、API Key、Base URL、提示模板等
3. 连接 MySQL 数据库
4. 从 `clean_rent` 表读取关键字段
5. 按行政区聚合租房数据
6. 为每个行政区构建 prompt
7. 调用 DeepSeek API 生成区级点评文本
8. 将结果写入 `district_summary` 表（如果有）或输出到控制台

### 2.2 DeepSeek 调用实现

当前实现使用 `openai.OpenAI` 客户端，该客户端可兼容 DeepSeek API。

关键代码：

```python
client = OpenAI(
    api_key=ai_cfg['llm']['api_key'],
    base_url=ai_cfg['llm']['base_url']
)

response = client.chat.completions.create(
    model=ai_cfg['llm']['model'],
    messages=[{"role": "user", "content": prompt}],
    max_tokens=ai_cfg['llm']['max_tokens'],
    temperature=ai_cfg['llm']['temperature']
)
```

- `base_url`：指向 DeepSeek API 地址，例如 `https://api.deepseek.com`
- `model`：当前配置为 `deepseek-chat`
- `max_tokens`、`temperature`：由 `config/ai_config.yaml` 控制

---

## 3. `clean_rent` 数据聚合

脚本中使用 `pandas` 从 `clean_rent` 表中读取数据，并按 `district` 聚合：

```python
df = pd.read_sql("SELECT district, price, area, price_per_sqm FROM clean_rent", conn)

grouped = df.groupby('district').agg(
    avg_rent=('price', 'mean'),
    avg_price_per_sqm=('price_per_sqm', 'mean'),
    avg_area=('area', 'mean'),
    total_houses=('price', 'count')
).reset_index()
```

### 3.1 计算项说明

- `avg_rent`：区内房源平均租金，单位元/月
- `avg_price_per_sqm`：区内平均单价，单位元/㎡
- `avg_area`：区内房源平均建筑面积，单位㎡
- `total_houses`：区内房源数

### 3.2 SQL 聚合示例

如果需要直接在数据库侧聚合，也可以使用 SQL：

```sql
SELECT
  district,
  AVG(price) AS avg_rent,
  AVG(price_per_sqm) AS avg_price_per_sqm,
  AVG(area) AS avg_area,
  COUNT(*) AS total_houses
FROM clean_rent
GROUP BY district;
```

---

## 4. Prompt 设计示例

当前 `config/ai_config.yaml` 中使用的模板为：

```yaml
prompts:
  district_comment: |
    你是一个资深成都房产分析师。请根据以下【{district}】{business_area}商圈的租房统计数据，用150字以内总结该区域的租房性价比，并分点列出2个优点和2个缺点。语气客观专业。
    数据：平均租金{avg_rent}元/月，平均单价{avg_price_per_sqm}元/㎡，平均面积{avg_area}㎡，房源数量{total_houses}套。
```

### 4.1 设计要点

- 明确角色：资深成都房产分析师
- 明确目标：总结租房性价比并给出优缺点
- 明确长度：150字以内
- 明确数据维度：平均租金、平均单价、平均面积、房源数量
- 语气要求：客观专业

### 4.2 变量替换

脚本会将聚合结果替换进模板：

- `{district}`：行政区名称
- `{business_area}`：商圈或行政区名称
- `{avg_rent}`：平均租金
- `{avg_price_per_sqm}`：平均单价
- `{avg_area}`：平均面积
- `{total_houses}`：房源数量

---

## 5. 生成的点评示例

以下是一个示例点评输出，基于典型成都区级租房数据：

> 该区域平均租金约 4200 元/月，平均单价约 6600 元/㎡，房源面积适中，供给量充足。整体性价比较高，适合预算在 4000-5000 元的年轻白领或小家庭。
>
> 优点：
> 1. 房源数量多，选择面广；
> 2. 平均单价处于市区中低水平，性价比较好；
>
> 缺点：
> 1. 由于房源量大，部分房源品质参差不齐；
> 2. 核心商圈距离稍远，通勤时间可能增加。

如果需要更丰富的示例，可以在 `docs/screenshots/` 下查看现有 `页面3：AI点评卡片图+切片器.png`。

---

## 6. 如何运行脚本

### 6.1 依赖安装

在项目根目录执行：

```bash
pip install pandas PyYAML PyMySQL openai
```

如果已经在 `requirements.txt` 中管理依赖，可按项目规范安装。

### 6.2 配置文件

确保以下文件存在并配置正确：

- `config/config.yaml`：数据库连接信息
- `config/ai_config.yaml`：DeepSeek API 配置、模型、prompt 模板

### 6.3 运行命令

在项目根目录运行：

```bash
python ai_scripts/generate_district_comments.py
```

### 6.4 运行结果

脚本执行完成后，会输出 `AI 点评生成完成！`。

当前实现会将结果写入数据库表 `district_summary`：

- district
- avg_rent
- avg_price_per_sqm
- avg_area
- total_houses
- ai_comment

若 `district_summary` 表不存在，需要先创建表结构或修改脚本为写入其它目标。

---

## 7. 备注与优化建议

- 若想按商圈生成点评，可在 `clean_rent` 表引入 `business_area` 字段，并改为按商圈聚合
- 若想增加对比维度，可在 prompt 中加入“环比上月”“同比去年”等数据字段
- 为避免 API 调用失败，可补充重试机制和日志记录
- 若要导出为 CSV，可在脚本中增加 `grouped.to_csv(...)`
