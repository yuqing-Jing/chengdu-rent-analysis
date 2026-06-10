import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml
import pandas as pd
import pymysql
from openai import OpenAI

# 加载配置
with open('config/config.yaml', 'r', encoding='utf-8') as f:
    db_cfg = yaml.safe_load(f)['database']

with open('config/ai_config.yaml', 'r', encoding='utf-8') as f:
    ai_cfg = yaml.safe_load(f)

# 连接数据库
conn = pymysql.connect(
    host=db_cfg['host'],
    user=db_cfg['user'],
    password=db_cfg['password'],
    database=db_cfg['database'],
    charset='utf8mb4'
)

# 从 clean_rent 聚合数据（请根据实际字段名调整）
# 假设 clean_rent 表有字段：district, price, area, price_per_sqm
df = pd.read_sql("SELECT district, price, area, price_per_sqm FROM clean_rent", conn)

# 按行政区聚合
grouped = df.groupby('district').agg(
    avg_rent=('price', 'mean'),
    avg_price_per_sqm=('price_per_sqm', 'mean'),
    avg_area=('area', 'mean'),
    total_houses=('price', 'count')
).reset_index()

# 初始化 OpenAI 客户端（DeepSeek 兼容）
client = OpenAI(
    api_key=ai_cfg['llm']['api_key'],
    base_url=ai_cfg['llm']['base_url']
)

def get_comment(row):
    prompt = ai_cfg['prompts']['district_comment'].format(
        district=row['district'],
        business_area=row['district'],  # 如果没有细分商圈，就用行政区名称
        avg_rent=round(row['avg_rent'], 2),
        avg_price_per_sqm=round(row['avg_price_per_sqm'], 2),
        avg_area=round(row['avg_area'], 2),
        total_houses=row['total_houses']
    )
    try:
        response = client.chat.completions.create(
            model=ai_cfg['llm']['model'],
            messages=[{"role": "user", "content": prompt}],
            max_tokens=ai_cfg['llm']['max_tokens'],
            temperature=ai_cfg['llm']['temperature']
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"AI 生成失败 [{row['district']}]: {e}")
        return ""

# 更新或插入 district_summary
cursor = conn.cursor()
for _, row in grouped.iterrows():
    comment = get_comment(row)
    sql = """
        INSERT INTO district_summary (district, avg_rent, avg_price_per_sqm, avg_area, total_houses, ai_comment)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE 
            avg_rent=VALUES(avg_rent), avg_price_per_sqm=VALUES(avg_price_per_sqm),
            avg_area=VALUES(avg_area), total_houses=VALUES(total_houses), ai_comment=VALUES(ai_comment)
    """
    cursor.execute(sql, (row['district'], row['avg_rent'], row['avg_price_per_sqm'], row['avg_area'], row['total_houses'], comment))
conn.commit()
cursor.close()
conn.close()
print("AI 点评生成完成！")