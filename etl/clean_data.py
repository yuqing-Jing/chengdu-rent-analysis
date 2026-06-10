

# etl/clean_data.py
import logging
import os
import yaml
import pandas as pd
import numpy as np
from urllib.parse import quote_plus
from sqlalchemy import create_engine

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, 'config', 'config.yaml')
RULES_PATH = os.path.join(BASE_DIR, 'config', 'etl_rules.yaml')

logging.basicConfig(
    level=logging.INFO,
    format='[%(levelname)s] %(asctime)s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def load_yaml(path):
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


def load_configs():
    config = load_yaml(CONFIG_PATH)
    rules_config = load_yaml(RULES_PATH)

    db_conf = config.get('database', {})
    cleaning_rules = rules_config.get('cleaning', {})

    defaults = {
        'area_range': [10, 500],
        'price_range': [300, 50000],
        'iqr_multiplier': 1.5,
        'use_district_iqr': True,
        'min_iqr_group_size': 20
    }
    for key, default in defaults.items():
        cleaning_rules.setdefault(key, default)

    required_db_keys = {'host', 'port', 'user', 'password', 'database'}
    missing_db = required_db_keys - set(db_conf)
    if missing_db:
        raise KeyError(f'Missing database config keys: {missing_db}')

    return db_conf, cleaning_rules


def build_engine(db_conf):
    password = quote_plus(str(db_conf['password']))
    db_url = f"mysql+pymysql://{db_conf['user']}:{password}@{db_conf['host']}:{db_conf['port']}/{db_conf['database']}"
    return create_engine(db_url)


def split_location(df):
    if 'bizcircle' not in df.columns or 'district' not in df.columns:
        return df

    df = df.copy()
    bizcircle = df['bizcircle'].astype(str).fillna('')
    parts = bizcircle.str.split('-', n=2, expand=True)

    has_three_parts = parts[2].notna()
    has_two_parts = parts[1].notna() & parts[2].isna()

    if has_three_parts.any():
        first = parts.loc[has_three_parts, 0].astype(str)
        df.loc[has_three_parts, 'district'] = np.where(
            first.str.contains('区'), first, first + '区'
        )
        df.loc[has_three_parts, 'bizcircle'] = parts.loc[has_three_parts, 1]
        df.loc[has_three_parts, 'community'] = parts.loc[has_three_parts, 2]

    if has_two_parts.any():
        df.loc[has_two_parts, 'bizcircle'] = parts.loc[has_two_parts, 0]
        df.loc[has_two_parts, 'community'] = parts.loc[has_two_parts, 1]

    return df


def parse_layout(df):
    if 'layout' not in df.columns:
        return df
    regex = df['layout'].astype(str).str.extract(r'(?P<rooms>\d+)室(?P<halls>\d+)厅')
    df['rooms'] = regex['rooms'].astype('Int64')
    df['halls'] = regex['halls'].astype('Int64')
    return df


def compute_iqr_bounds(series, multiplier):
    q1 = series.quantile(0.25)
    q3 = series.quantile(0.75)
    iqr = q3 - q1
    return q1 - multiplier * iqr, q3 + multiplier * iqr


def detect_abnormal_price(df, rules):
    if df.empty:
        df['is_abnormal'] = pd.Series(dtype='int8')
        return df

    global_lower, global_upper = compute_iqr_bounds(df['price_per_sqm'], rules['iqr_multiplier'])
    if not rules.get('use_district_iqr', True):
        df['is_abnormal'] = (~df['price_per_sqm'].between(global_lower, global_upper)).astype('int8')
        return df

    grouped = df.groupby('district')['price_per_sqm'].agg(
        q1=lambda x: x.quantile(0.25),
        q3=lambda x: x.quantile(0.75),
        count='count'
    ).reset_index()
    grouped['iqr'] = grouped['q3'] - grouped['q1']
    grouped['lower'] = grouped['q1'] - rules['iqr_multiplier'] * grouped['iqr']
    grouped['upper'] = grouped['q3'] + rules['iqr_multiplier'] * grouped['iqr']

    fallback = grouped['count'] < rules['min_iqr_group_size']
    grouped.loc[fallback, ['lower', 'upper']] = [global_lower, global_upper]

    df = df.merge(grouped[['district', 'lower', 'upper']], on='district', how='left')
    df['lower'] = df['lower'].fillna(global_lower)
    df['upper'] = df['upper'].fillna(global_upper)
    df['is_abnormal'] = (~df['price_per_sqm'].between(df['lower'], df['upper'])).astype('int8')
    return df.drop(columns=['lower', 'upper'])


def run_etl():
    logger.info('启动 ETL 清洗任务')
    db_conf, rules = load_configs()
    engine = build_engine(db_conf)

    query = '''
        SELECT r.* FROM raw_rent r
        LEFT JOIN clean_rent c ON r.house_id = c.house_id
        WHERE c.house_id IS NULL
    '''

    try:
        df = pd.read_sql(query, engine)
    except Exception as exc:
        logger.error('抽取数据失败: %s', exc)
        return

    if df.empty:
        logger.info('没有新的原始数据需要清洗。')
        return

    logger.info('成功抽取 %d 条待清洗数据', len(df))
    df = df.drop_duplicates(subset=['house_id'])
    df = split_location(df)
    df = parse_layout(df)

    area_min, area_max = rules['area_range']
    price_min, price_max = rules['price_range']
    df = df[df['area'].between(area_min, area_max) & df['price'].between(price_min, price_max)].copy()

    if df.empty:
        logger.info('过滤后没有符合条件的数据。')
        return

    df['price_per_sqm'] = df['price'] / df['area']
    df = detect_abnormal_price(df, rules)

    cols_to_save = [
        'house_id', 'district', 'bizcircle', 'community',
        'rooms', 'halls', 'area', 'price', 'price_per_sqm', 'is_abnormal'
    ]
    df_clean = df[cols_to_save].dropna(subset=['area', 'price']).copy()

    logger.info('清洗完成，准备入库 %d 条干净数据', len(df_clean))
    try:
        df_clean.to_sql('clean_rent', engine, if_exists='append', index=False, chunksize=5000, method='multi')
    except Exception as exc:
        logger.error('写入 clean_rent 失败: %s', exc)
        return

    logger.info('ETL 任务成功完成！数据已存入 clean_rent 表。')


if __name__ == '__main__':
    run_etl()
