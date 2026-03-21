import asyncio
import json
from curl_cffi.requests import AsyncSession
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
}

async def analyze_nstock():
    url = "https://www.nstock.tw/stock_info?stock_id=2330"
    
    async with AsyncSession() as session:
        print(f"正在抓取: {url}\n")
        
        try:
            response = await session.get(
                url,
                headers=HEADERS,
                timeout=20,
                impersonate="chrome131",
                allow_redirects=True
            )
            
            print(f"狀態碼: {response.status_code}\n")
            
            if response.status_code != 200:
                print("抓取失敗")
                return
            
            html = response.text
            soup = BeautifulSoup(html, "lxml")
            
            # 檢查 __NEXT_DATA__
            print("="*60)
            print("檢查 __NEXT_DATA__:")
            print("="*60)
            next_data_script = soup.find('script', {'id': '__NEXT_DATA__'})
            if next_data_script:
                print("[OK] 找到 __NEXT_DATA__")
                try:
                    data = json.loads(next_data_script.string)
                    print(f"\n__NEXT_DATA__ 結構預覽:")
                    print(json.dumps(data, indent=2, ensure_ascii=False)[:1000])
                except:
                    print("無法解析 JSON")
            else:
                print("[X] 沒有找到 __NEXT_DATA__")
            
            # 檢查 JSON-LD
            print("\n" + "="*60)
            print("檢查 JSON-LD:")
            print("="*60)
            json_ld_scripts = soup.find_all('script', {'type': 'application/ld+json'})
            if json_ld_scripts:
                print(f"[OK] 找到 {len(json_ld_scripts)} 個 JSON-LD")
                for i, script in enumerate(json_ld_scripts, 1):
                    try:
                        data = json.loads(script.string)
                        print(f"\nJSON-LD {i} 類型: {data.get('@type', 'Unknown')}")
                        if 'itemListElement' in data:
                            print(f"  包含 itemListElement: {len(data['itemListElement'])} 項")
                    except:
                        print(f"\nJSON-LD {i}: 無法解析")
            else:
                print("[X] 沒有找到 JSON-LD")
            
            # 檢查新聞連結
            print("\n" + "="*60)
            print("檢查新聞連結:")
            print("="*60)
            
            # 尋找新聞相關的 a 標籤
            news_links = soup.find_all('a', href=lambda x: x and '/news/' in x)
            if news_links:
                print(f"[OK] 找到 {len(news_links)} 個新聞連結\n")
                for i, link in enumerate(news_links[:10], 1):
                    print(f"新聞 {i}:")
                    print(f"  標題: {link.get_text(strip=True)}")
                    print(f"  URL: {link.get('href')}")
                    print()
            else:
                print("[X] 沒有找到新聞連結")
            
            # 檢查所有 script 標籤
            print("="*60)
            print("檢查所有 Script 標籤:")
            print("="*60)
            scripts = soup.find_all('script')
            print(f"共找到 {len(scripts)} 個 script 標籤\n")
            
            for i, script in enumerate(scripts, 1):
                script_content = script.string or ""
                if script_content and len(script_content) > 100:
                    # 檢查是否包含新聞相關的 JSON 資料
                    if 'news' in script_content.lower() or 'article' in script_content.lower():
                        print(f"Script {i} (可能包含新聞資料):")
                        print(f"  ID: {script.get('id', 'N/A')}")
                        print(f"  Type: {script.get('type', 'N/A')}")
                        print(f"  內容預覽: {script_content[:200]}...")
                        print()
            
            # 儲存完整 HTML
            with open("nstock_page.html", "w", encoding="utf-8") as f:
                f.write(html)
            print("\n[OK] 完整 HTML 已儲存到 nstock_page.html")
            
        except Exception as e:
            print(f"發生錯誤: {e}")

if __name__ == "__main__":
    asyncio.run(analyze_nstock())
