"""
快速測試一周新聞分析 API
"""
# -*- coding: utf-8 -*-
import sys
import io

# 設置 stdout 為 UTF-8 編碼（解決 Windows 控制台顯示問題）
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import requests
import json

BASE_URL = "http://127.0.0.1:5000"

def test_weekly_analysis():
    """測試一周新聞分析 API"""
    print("=" * 60)
    print("測試 /api/weekly_analysis")
    print("=" * 60)
    
    try:
        response = requests.get(f"{BASE_URL}/api/weekly_analysis")
        if response.status_code == 200:
            data = response.json()
            print("\n✅ API 響應成功！")
            print(f"\n📰 總新聞數: {data['total_news']}")
            print(f"📊 平均情緒: {data['avg_sentiment']}")
            print(f"📈 正面新聞: {data['positive_count']}")
            print(f"📉 負面新聞: {data['negative_count']}")
            print(f"\n🔥 本周最熱門股票 TOP 5:")
            for i, stock in enumerate(data['top_stocks'], 1):
                print(f"  {i}. {stock['stock_name']} ({stock['news_count']} 則)")
        else:
            print(f"\n❌ API 請求失敗，狀態碼: {response.status_code}")
    except Exception as e:
        print(f"\n❌ 錯誤: {e}")

def test_weekly_sentiment_trend():
    """測試一周情緒趨勢 API"""
    print("\n" + "=" * 60)
    print("測試 /api/weekly_sentiment_trend")
    print("=" * 60)
    
    try:
        response = requests.get(f"{BASE_URL}/api/weekly_sentiment_trend")
        if response.status_code == 200:
            data = response.json()
            print("\n✅ API 響應成功！")
            print(f"\n📅 日期: {', '.join(data['labels'])}")
            print(f"📊 情緒: {', '.join(map(str, data['data']))}")
            
            # 簡單的趨勢分析
            if len(data['data']) >= 2:
                if data['data'][-1] > data['data'][0]:
                    print("\n📈 趨勢: 情緒上升")
                elif data['data'][-1] < data['data'][0]:
                    print("\n📉 趨勢: 情緒下降")
                else:
                    print("\n➡️ 趨勢: 情緒持平")
        else:
            print(f"\n❌ API 請求失敗，狀態碼: {response.status_code}")
    except Exception as e:
        print(f"\n❌ 錯誤: {e}")

def test_stats():
    """測試基本統計 API (確保服務器正常運行)"""
    print("\n" + "=" * 60)
    print("測試 /api/stats (基本檢查)")
    print("=" * 60)
    
    try:
        response = requests.get(f"{BASE_URL}/api/stats")
        if response.status_code == 200:
            data = response.json()
            print("\n✅ 服務器運行正常！")
            print(f"\n總新聞數: {data['total_news']}")
            print(f"總股票數: {data['total_stocks']}")
            print(f"今日新聞: {data['today_news']}")
            print(f"平均情緒: {data['avg_sentiment']}")
        else:
            print(f"\n❌ API 請求失敗，狀態碼: {response.status_code}")
    except requests.exceptions.ConnectionError:
        print("\n❌ 無法連接到服務器！請確保:")
        print("   1. Flask 應用正在運行 (python app.py)")
        print("   2. 服務器監聽在 http://127.0.0.1:5000")
    except Exception as e:
        print(f"\n❌ 錯誤: {e}")

if __name__ == "__main__":
    print("\n🚀 開始測試一周新聞分析功能...\n")
    
    # 先測試基本 API 確保服務器運行
    test_stats()
    
    # 測試一周分析 API
    test_weekly_analysis()
    
    # 測試一周情緒趨勢 API
    test_weekly_sentiment_trend()
    
    print("\n" + "=" * 60)
    print("✅ 測試完成！")
    print("=" * 60)
    print("\n💡 如果所有測試都通過，說明一周新聞分析功能已正常運作！")
    print("   可以在瀏覽器訪問 http://127.0.0.1:5000 查看 Dashboard")
    print("   或在 LINE Bot 中發送 '一周' 指令進行測試\n")
