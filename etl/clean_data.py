# etl/clean_data.py
import re
import os
import sys
import yaml
import pandas as pd
from urllib.parse import quote_plus
from sqlalchemy import create_engine

# ================= 1. 配置加载 =================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, 'config', 'config.yaml')
RULES_PATH = os.path.join(BASE_DIR, 'config', 'etl_rules.yaml')

def load_configs():
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        db_conf = yaml.safe_load(f)['database']
    with open(RULES_PATH, 'r', encoding='utf-8') as f:
        rules = yaml.safe_load(f)['cleaning']
    return db_conf, rules

db_conf, rules = load_configs()
password = quote_plus(str(db_conf['password']))
DB_URL = f"mysql+pymysql://{db_conf['user']}:{password}@{db_conf['host']}:{db_conf['port']}/{db_conf['database']}"
engine = create_engine(DB_URL)

# ================= 2. 核心清洗逻辑 =================
def split_location(bizcircle_str, district_str):
    """
    智能拆分混合地址：将 '锦江-东湖-翡翠城三期' 拆分为 区、商圈、小区
    """
    if not bizcircle_str or '-' not in str(bizcircle_str):
        return district_str, '', bizcircle_str or ''
    
    parts = str(bizcircle_str).split('-')
    if len(parts) == 3:
        # 标准格式：区-商圈-小区
        return parts[0] + '区' if '区' not in parts[0] else parts[0], parts[1], parts[2]
    elif len(parts) == 2:
        # 缺失格式：商圈-小区
        return district_str, parts[0], parts[1]
    return district_str, '', bizcircle_str

def parse_layout(layout_str):
    """从 '2室1厅1卫' 中提取 室 和 厅"""
    if not layout_str: return None, None
    match = re.search(r'(\d+)室(\d+)厅', str(layout_str))
    if match:
        return int(match.group(1)), int(match.group(2))
    return None, None

def run_etl():
    print("[*] 启动 ETL 清洗任务...")
    
    # 1. Extract: 抽取未清洗的数据 (通过 house_id 比对)
    query = """
        SELECT r.* FROM raw_rent r
        LEFT JOIN clean_rent c ON r.house_id = c.house_id
        WHERE c.house_id IS NULL
    """
    try:
        df = pd.read_sql(query, engine)
    except Exception as e:
        print(f"[!] 抽取数据失败: {e}")
        return

    if df.empty:
        print("[ℹ️] 没有新的原始数据需要清洗。")
        return
        
    print(f"[*] 成功抽取 {len(df)} 条待清洗数据。")

    # 2. Transform: 数据转换
    # 2.1 拆分地址
    df[['district', 'bizcircle', 'community']] = df.apply(
        lambda row: pd.Series(split_location(row['bizcircle'], row['district'])), axis=1
    )
    
    # 2.2 拆分户型
    df['rooms'], df['halls'] = zip(*df['layout'].apply(parse_layout))
    
    # 2.3 过滤绝对异常值 (面积和价格不在合理区间)
    df = df[df['area'].between(rules['area_range'][0], rules['area_range'][1])]
    df = df[df['price'].between(rules['price_range'][0], rules['price_range'][1])]
    
    # 2.4 计算平米单价
    df['price_per_sqm'] = df['price'] / df['area']
    
    # 2.5 IQR 统计学异常值检测 (标记极端价格)
    Q1 = df['price_per_sqm'].quantile(0.25)
    Q3 = df['price_per_sqm'].quantile(0.75)
    IQR = Q3 - Q1
    lower_bound = Q1 - rules['iqr_multiplier'] * IQR
    upper_bound = Q3 + rules['iqr_multiplier'] * IQR
    
    df['is_abnormal'] = (~df['price_per_sqm'].between(lower_bound, upper_bound)).astype(int)

    # 3. Load: 加载到 clean_rent 表
    cols_to_save = ['house_id', 'district', 'bizcircle', 'community', 
                    'rooms', 'halls', 'area', 'price', 'price_per_sqm', 'is_abnormal']
    
    df_clean = df[cols_to_save].dropna(subset=['area', 'price']) # 丢弃面积为空或价格为空的
    
    print(f"[*] 清洗完成，准备入库 {len(df_clean)} 条干净数据...")
    df_clean.to_sql('clean_rent', engine, if_exists='append', index=False)
    print("[✅] ETL 任务成功完成！数据已存入 clean_rent 表。")

if __name__ == '__main__':
    run_etl()
