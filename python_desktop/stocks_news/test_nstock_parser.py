import asyncio
import json
import re
from curl_cffi.requests import AsyncSession
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
}

def extract_nuxt_data(html: str):
    """從 HTML 提取 window.__NUXT__ 資料"""
    if not html:
        return None
    
    soup = BeautifulSoup(html, "lxml")
    
    # 尋找包含 window.__NUXT__ 的 script 標籤
    for script in soup.find_all('script'):
        script_content = script.string
        if script_content and 'window.__NUXT__' in script_content:
            return script_content
    
    return None


def parse_news_from_nuxt_js(script_content: str):
    """直接從 JS 程式碼中提取新聞資料
    
    格式範例:
    news:[{id:"20260210-082108210000",category:"J",title:"...",link:"...",source:e,image:b,date:"...",stocks:l}]
    """
    if not script_content:
        return []
    
    news_list = []
    
    # 找到 news:[ 開始的區塊
    news_match = re.search(r'news:\[(.*?)\]', script_content, re.DOTALL)
    if not news_match:
        return []
    
    news_block = news_match.group(1)
    
    # 匹配每個新聞物件 - 使用非貪婪匹配
    # 格式: {id:"...",category:"...",title:"...",link:"...",source:...,image:...,date:"...",stocks:"..."}
    # stocks 可能是變數引用 (l) 或字串 ("2330(TW)")
    news_pattern = r'\{id:"([^"]+)",category:"([^"]*?)",title:"(.*?)",link:"([^"]+)",source:[^,]+,image:[^,]+,date:"([^"]+)",stocks:(?:"([^"]+)"|([a-z]))\}'
    
    matches = re.finditer(news_pattern, news_block)
    
    for match in matches:
        # 解析 link 中的 \u002F 為 /
        link = match.group(4).replace('\\u002F', '/')
        
        # stocks 可能是第6組(字串)或第7組(變數引用)
        stocks = match.group(6) if match.group(6) else f"VAR_{match.group(7)}"
        
        news_item = {
            'id': match.group(1),
            'category': match.group(2),
            'title': match.group(3),
            'link': link,
            'date': match.group(5),
            'stocks': stocks
        }
        news_list.append(news_item)
    
    return news_list


async def test_nstock_parser():
    """測試 nstock 解析器"""
    stock_id = "2330"
    url = f"https://www.nstock.tw/stock_info?stock_id={stock_id}"
    
    async with AsyncSession() as session:
        print(f"正在抓取 nstock: {stock_id}\n")
        
        try:
            response = await session.get(
                url,
                headers=HEADERS,
                timeout=20,
                impersonate="chrome131"
            )
            
            print(f"狀態碼: {response.status_code}\n")
            
            if response.status_code != 200:
                print("抓取失敗")
                return
            
            # 提取 NUXT 資料
            nuxt_script = extract_nuxt_data(response.text)
            
            if nuxt_script:
                print("[OK] 找到 window.__NUXT__ 資料\n")
                
                # 解析新聞
                news_list = parse_news_from_nuxt_js(nuxt_script)
                
                print(f"{'='*60}")
                print(f"共找到 {len(news_list)} 筆新聞")
                print(f"{'='*60}\n")
                
                for i, news in enumerate(news_list, 1):
                    print(f"【新聞 {i}】")
                    print(f"  ID     : {news['id']}")
                    print(f"  標題   : {news['title']}")
                    print(f"  時間   : {news['date']}")
                    print(f"  股票   : {news['stocks']}")
                    print(f"  連結   : {news['link']}")
                    print()
                
                # 儲存到 JSON
                if news_list:
                    with open("nstock_news_sample.json", "w", encoding="utf-8") as f:
                        json.dump(news_list, f, ensure_ascii=False, indent=2)
                    print(f"\n[OK] 資料已儲存到 nstock_news_sample.json")
            else:
                print("[X] 未找到 window.__NUXT__ 資料")
        
        except Exception as e:
            print(f"[ERROR] 發生錯誤: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_nstock_parser())
