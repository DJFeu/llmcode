#!/usr/bin/env python3
"""東京旅遊資訊抓取工具"""

import json
from datetime import datetime
from typing import Dict, List, Optional


class TokyoTravelScraper:
    """抓取東京旅遊相關資訊"""
    
    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
    
    def get_weather(self, city: str = "Tokyo") -> Optional[Dict]:
        """獲取天氣資訊（需要 OpenWeatherMap API key）"""
        # 需要 API key，這裡僅提供結構範例
        # 請到 https://openweathermap.org/api 申請免費 API key
        print(f"天氣查詢需要 OpenWeatherMap API key，請自行申請後加入程式")
        return None
    
    def get_spot_info(self, spot_name: str) -> Dict:
        """獲取景點基本資訊"""
        spots = {
            "淺草寺": {
                "name": "淺草寺 (Senso-ji)",
                "area": "台東區",
                "hours": "6:00-17:00",
                "entrance": "免費",
                "transport": "地下鐵淺草站",
                "highlights": ["雷門", "仲見世商店街", "五重塔"]
            },
            "東京鐵塔": {
                "name": "東京鐵塔 (Tokyo Tower)",
                "area": "港區",
                "hours": "9:00-23:00",
                "entrance": "1500 日圓",
                "transport": "地下鐵御成門站",
                "highlights": ["展望台", "夜景", "拍照點"]
            },
            "明治神宮": {
                "name": "明治神宮 (Meiji Shrine)",
                "area": "澀谷區",
                "hours": "日出 - 日落",
                "entrance": "免費",
                "transport": "JR 原宿站",
                "highlights": ["森林步道", "神前式婚禮", "御守"]
            },
            "澀谷十字路口": {
                "name": "澀谷全向十字路口",
                "area": "澀谷區",
                "hours": "24 小時",
                "entrance": "免費",
                "transport": "JR 澀谷站",
                "highlights": ["世界最繁忙十字路口", "SHIBUYA SKY", "忠犬八公像"]
            },
            "teamLab Planets": {
                "name": "teamLab Planets TOKYO",
                "area": "江東區",
                "hours": "10:00-22:00",
                "entrance": "3200 日圓",
                "transport": "地下鐵辰巳站",
                "highlights": ["沉浸式藝術", "水中展覽", "需要提前預約"]
            },
            "築地外圍市場": {
                "name": "築地外圍市場",
                "area": "中央區",
                "hours": "5:00-15:00",
                "entrance": "免費",
                "transport": "地下鐵築地站",
                "highlights": ["新鮮海鮮", "壽司", "小吃"]
            }
        }
        return spots.get(spot_name, {"error": "景點未找到"})
    
    def get_area_recommendations(self) -> Dict:
        """區域住宿推薦"""
        return {
            "新宿": {
                "優點": ["交通樞紐", "購物便利", "夜生活豐富"],
                "缺點": ["人潮多", "較吵雜"],
                "預算範圍": "$3000-6000 TWD/晚",
                "適合": "第一次來東京、喜歡便利者"
            },
            "上野": {
                "優點": ["價格親民", "近機場", "有美術館"],
                "缺點": ["部分區域較舊", "夜生活較少"],
                "預算範圍": "$2000-4000 TWD/晚",
                "適合": "預算有限、帶長輩旅行"
            },
            "銀座": {
                "優點": ["高級購物", "交通便利", "安全"],
                "缺點": ["價格高", "夜晚較冷清"],
                "預算範圍": "$6000-15000+ TWD/晚",
                "適合": "預算充足、喜歡高級購物"
            },
            "澀谷": {
                "優點": ["年輕潮流", "交通方便", "夜生活豐富"],
                "缺點": ["人潮多", "較吵雜"],
                "預算範圍": "$3000-6000 TWD/晚",
                "適合": "年輕人、喜歡潮流文化"
            },
            "秋葉原": {
                "優點": ["電玩動漫", "獨特體驗", "價格合理"],
                "缺點": ["較偏動漫主題", "夜生活有限"],
                "預算範圍": "$2500-5000 TWD/晚",
                "適合": "動漫電玩愛好者"
            }
        }
    
    def get_two_day_itinerary(self) -> Dict:
        """兩日遊行程建議"""
        return {
            "Day1_傳統東京": {
                "上午": {
                    "景點": "淺草寺",
                    "時間": "8:00-11:00",
                    "交通": "地下鐵淺草站",
                    "建議": "避開人潮，先逛雷門再進寺內"
                },
                "午餐": {
                    "推薦": "淺草仲見世商店街小吃",
                    "預算": "500-1000 日圓"
                },
                "下午": {
                    "景點": ["皇居外苑", "銀座"],
                    "時間": "13:00-17:00",
                    "交通": "地下鐵日比谷線",
                    "建議": "皇居免費參觀外苑，銀座可逛街"
                },
                "晚餐": {
                    "推薦": "銀座高級料理或新宿居酒屋",
                    "預算": "2000-5000 日圓"
                },
                "晚上": {
                    "景點": "新宿歌舞伎町",
                    "時間": "19:00-22:00",
                    "建議": "注意安全，可參觀哥吉拉頭"
                }
            },
            "Day2_現代東京": {
                "上午": {
                    "景點": ["明治神宮", "原宿"],
                    "時間": "8:00-11:00",
                    "交通": "JR 原宿站",
                    "建議": "明治神宮晨走，原宿可逛竹下通"
                },
                "午餐": {
                    "推薦": "原宿或澀谷美食",
                    "預算": "1000-2000 日圓"
                },
                "下午": {
                    "景點": ["澀谷十字路口", "SHIBUYA SKY", "東京鐵塔"],
                    "時間": "13:00-18:00",
                    "交通": "JR 山手線",
                    "建議": "SHIBUYA SKY 需預約，鐵塔可看夜景"
                },
                "晚餐": {
                    "推薦": "六本木或秋葉原",
                    "預算": "2000-5000 日圓"
                },
                "晚上": {
                    "景點": "六本木夜景 或 秋葉原電器街",
                    "時間": "19:00-22:00",
                    "建議": "六本木看夜景，秋葉原買周邊"
                }
            }
        }
    
    def get_transport_tips(self) -> Dict:
        """交通建議"""
        return {
            "交通卡": {
                "Suica": "西瓜卡，可用於大部分交通和便利商店",
                "PASMO": "與 Suica 相同功能",
                "ICOCA": "關西出發可帶到東京使用"
            },
            "地鐵券": {
                "東京地鐵 24/48/72 小時券": "無限次搭乘地鐵",
                "都營地鐵": "包含都營線，需確認是否包含"
            },
            "機場交通": {
                "成田機場": "Skyliner (40 分鐘) 或 Narita Express (60 分鐘)",
                "羽田機場": "東京單軌電車 (25 分鐘) 或京急線 (30 分鐘)"
            },
            "建議": "第一次來東京建議購買 Suica/PASMO，方便且不用每次買票"
        }
    
    def get_budget_estimate(self, days: int = 2) -> Dict:
        """預算估算（每人，不含機票）"""
        return {
            "住宿": {
                "經濟": f"{days * 2000} TWD",
                "中級": f"{days * 4000} TWD",
                "高級": f"{days * 8000} TWD"
            },
            "交通": {
                "機場往返": "1500-3000 TWD",
                "市區交通": f"{days * 500} TWD"
            },
            "餐飲": {
                "經濟": f"{days * 1000} TWD",
                "中級": f"{days * 2500} TWD",
                "高級": f"{days * 5000} TWD"
            },
            "景點門票": f"{days * 2000} TWD",
            "購物": "自訂",
            "總計": {
                "經濟": f"{days * 5500} TWD",
                "中級": f"{days * 10000} TWD",
                "高級": f"{days * 20000} TWD"
            }
        }
    
    def save_to_json(self, data: Dict, filename: str):
        """儲存資料到 JSON 檔案"""
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"資料已儲存至 {filename}")


def main():
    """主程式"""
    scraper = TokyoTravelScraper()
    
    print("=" * 50)
    print("東京旅遊資訊")
    print("=" * 50)
    
    # 景點資訊
    print("\n【熱門景點】")
    for spot in ["淺草寺", "東京鐵塔", "明治神宮", "澀谷十字路口", "teamLab Planets"]:
        info = scraper.get_spot_info(spot)
        print(f"\n{info.get('name', spot)}")
        print(f"  區域: {info.get('area', 'N/A')}")
        print(f"  營業時間: {info.get('hours', 'N/A')}")
        print(f"  門票: {info.get('entrance', 'N/A')}")
        print(f"  交通: {info.get('transport', 'N/A')}")
    
    # 住宿推薦
    print("\n\n【住宿區域推薦】")
    areas = scraper.get_area_recommendations()
    for area, info in areas.items():
        print(f"\n{area}")
        print(f"  預算: {info['預算範圍']}")
        print(f"  適合: {info['適合']}")
    
    # 兩日行程
    print("\n\n【兩日遊行程建議】")
    itinerary = scraper.get_two_day_itinerary()
    for day, schedule in itinerary.items():
        print(f"\n{day}")
        for period, info in schedule.items():
            if isinstance(info, dict) and "景點" in info:
                print(f"  {period}: {info['景點']} ({info.get('時間', 'N/A')})")
    
    # 交通建議
    print("\n\n【交通建議】")
    transport = scraper.get_transport_tips()
    for category, info in transport.items():
        print(f"\n{category}")
        if isinstance(info, str):
            print(f"  {info}")
        else:
            for key, value in info.items():
                print(f"  {key}: {value}")
    
    # 預算估算
    print("\n\n【預算估算（每人，2 天）】")
    budget = scraper.get_budget_estimate(2)
    for category, info in budget.items():
        if isinstance(info, dict):
            print(f"\n{category}:")
            for level, amount in info.items():
                print(f"  {level}: {amount}")
        else:
            print(f"{category}: {info}")
    
    # 儲存完整資料
    all_data = {
        "景點": {spot: scraper.get_spot_info(spot) for spot in ["淺草寺", "東京鐵塔", "明治神宮", "澀谷十字路口", "teamLab Planets", "築地外圍市場"]},
        "住宿推薦": scraper.get_area_recommendations(),
        "兩日行程": scraper.get_two_day_itinerary(),
        "交通建議": scraper.get_transport_tips(),
        "預算估算": scraper.get_budget_estimate(2),
        "查詢時間": datetime.now().isoformat()
    }
    
    scraper.save_to_json(all_data, "tokyo_travel_info.json")


if __name__ == "__main__":
    main()
