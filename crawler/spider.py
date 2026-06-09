import time
import random
import re
import sys
import os
import yaml
import pandas as pd
from urllib.parse import quote_plus
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from sqlalchemy import create_engine

# ================= 1. 配置加载与初始化 =================
CONFIG_PATH = 'config/config.yaml'

def load_config():
    if not os.path.exists(CONFIG_PATH):
        print(f"[❌ 错误] 未找到配置文件 {CONFIG_PATH}！")
        sys.exit(1)
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

config = load_config()
db_conf = config['database']
password = quote_plus(str(db_conf['password']))
DB_URL = f"mysql+pymysql://{db_conf['user']}:{password}@{db_conf['host']}:{db_conf['port']}/{db_conf['database']}"
engine = create_engine(DB_URL)

# ================= 2. Playwright 原生解析模块 =================
def parse_item_with_playwright(item_locator):
    """使用特征匹配解析 DOM，彻底解决字段错位问题"""
    try:
        # 1. 找标题和链接
        title_loc = item_locator.locator('a[class*="title"]').first
        if title_loc.count() == 0:
            title_loc = item_locator.locator('a').first 
            
        if title_loc.count() == 0:
            return None
            
        title = (title_loc.text_content() or "").strip()
        href = title_loc.get_attribute('href') or ""
        house_id = href.split('/')[-1].replace('.html', '') if href else ""
        if not house_id: 
            return None

        # 2. 找价格
        price_loc = item_locator.locator('[class*="price"] em, [class*="price"] span').first
        price_text = (price_loc.text_content() or "0").strip() if price_loc.count() > 0 else "0"
        price_match = re.search(r'\d+', price_text)
        price = int(price_match.group()) if price_match else 0

        # 3. 找描述信息 (核心重构：智能特征匹配)
        des_loc = item_locator.locator('p[class*="des"], div[class*="des"]').first
        des_text = (des_loc.text_content() or "").replace('\n', '/').strip() if des_loc.count() > 0 else ""
        parts = [p.strip() for p in des_text.split('/') if p.strip()]

        # 初始化字段
        district, bizcircle, community, layout, area_str = "", "", "", "", "0"
        
        # 🌟 智能遍历：根据内容特征赋值，无视顺序和缺失字段
        for part in parts:
            if '㎡' in part:
                area_str = part
            elif '室' in part and '厅' in part:
                layout = part
            elif '区' in part or '新区' in part or '县' in part:
                district = part
            elif not bizcircle and '㎡' not in part and '室' not in part and '区' not in part:
                # 排除面积、户型、行政区后，第一个未知字符串通常是商圈
                bizcircle = part
            elif not community and '㎡' not in part and '室' not in part and '区' not in part and bizcircle:
                # 第二个未知字符串通常是小区名
                community = part

        # 提取面积数字
        area_match = re.search(r'[\d.]+', area_str)
        area = float(area_match.group()) if area_match else 0.0

        return {
            'house_id': house_id, 'title': title, 'district': district,
            'bizcircle': bizcircle, 'community': community, 'layout': layout,
            'area': area, 'price': price
        }
    except Exception as e:
        # print(f"[-] 解析单条异常: {e}")
        return None


# ================= 3. 核心爬虫逻辑 =================
def run_crawler():
    print("[*] 启动 Playwright 原生解析版爬虫...")
    
    with sync_playwright() as p:
        user_data_dir = os.path.join(os.getcwd(), "chrome_profile")
        context = p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir, headless=False, channel="chrome",
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        )
        page = context.pages[0] if context.pages else context.new_page()
        
        base_url = "https://cd.zu.ke.com/zufang/"
        districts = {'jinjiang': '锦江区', 'qingyang': '青羊区', 'jinniu': '金牛区', 
                     'chenghua': '成华区', 'wuhou': '武侯区', 'gaoxin': '高新区'}
        
        all_data = []
        max_pages = config.get('crawler', {}).get('max_pages', 2) 
        
        for dist_code, dist_name in districts.items():
            print(f"\n=== 开始爬取 {dist_name} ===")
            for page_num in range(1, max_pages + 1):
                url = f"{base_url}{dist_code}/pg{page_num}/"
                print(f"[*] 访问: {url}")
                
                try:
                    page.goto(url, wait_until="networkidle", timeout=30000)
                    # 模拟滚动触发懒加载
                    page.mouse.wheel(0, 800)
                    time.sleep(1) 
                    page.mouse.wheel(0, 800)
                    time.sleep(1)

                    # 🌟 核心改进：使用模糊匹配等待房源列表容器出现
                    page.wait_for_selector('div[class*="content__list"], div[class*="list-content"]', timeout=15000)
                except PlaywrightTimeoutError:
                    print("\n[!] 加载超时！请查看浏览器处理验证码/登录。")
                    input(">>> 处理完毕并确保看到房源后，按回车继续...")
                    page.mouse.wheel(0, 1000)
                    time.sleep(2)

                # 移除弹窗
                page.evaluate("""
                    document.querySelectorAll('.modal, .mask, [class*="login"], [class*="dialog"]').forEach(el => el.remove());
                    document.body.style.overflow = 'auto';
                """)
                
                # 🌟 核心改进：直接用 Playwright 获取所有房源卡片节点
                # 尝试多种可能的卡片外层 class
                items = page.locator('div[class*="content__list--item"]').all()
                if not items:
                    items = page.locator('div[class*="list-item"]').all()
                if not items:
                    # 终极兜底：找所有包含特定结构的 div
                    items = page.locator('div:has(> a[class*="title"])').all()

                print(f"[i] 当前页面找到 {len(items)} 个房源卡片节点")

                if not items:
                    print(f"[❌ 诊断] 仍然找不到卡片节点！保存截图...")
                    page.screenshot(path=f"debug_{dist_code}_pg{page_num}.png")
                    continue

                page_data_count = 0
                for item in items:
                    data = parse_item_with_playwright(item)
                    if data:
                        data['district'] = dist_name 
                        all_data.append(data)
                        page_data_count += 1
                
                print(f"[+] 第 {page_num} 页解析完成，成功提取 {page_data_count} 条房源")
                time.sleep(random.uniform(2.0, 4.0)) 
                
        context.close()
        
    # ================= 4. 数据入库 =================
    if not all_data:
        print("[❌] 未获取到任何数据。")
        return

    df = pd.DataFrame(all_data).drop_duplicates(subset=['house_id'])
    print(f"[*] 准备入库 {len(df)} 条...")
    try:
        existing_df = pd.read_sql("SELECT house_id FROM raw_rent", engine)
        df_new = df[~df['house_id'].isin(existing_df['house_id'].tolist())]
    except:
        df_new = df

    if not df_new.empty:
        df_new.to_sql('raw_rent', engine, if_exists='append', index=False)
        print("[✅] 入库成功！")
    else:
        print("[ℹ️] 无新数据。")

if __name__ == '__main__':
    run_crawler()
