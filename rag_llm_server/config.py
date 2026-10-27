import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    VOLC_AK = os.getenv("VOLC_ACCESS_KEY")
    VOLC_SK = os.getenv("VOLC_SECRET_KEY")
    ARK_ENDPOINT_ID = os.getenv("ARK_ENDPOINT_ID")
    ARK_API_KEY = os.getenv("ARK_API_KEY")

    RTC_APP_ID = os.getenv("RTC_APP_ID")
    RTC_APP_KEY = os.getenv("RTC_APP_KEY")
    # 现成的 RTC Token（可选）。填了就直接下发给前端，不再用 AppKey 动态签发。
    RTC_TOKEN = os.getenv("RTC_TOKEN")

    SERVER_URL = os.getenv("SERVER_URL")

    # --- 火山知识库（RAG）---
    # 主账号 ID，对应请求头 V-Account-Id；控制台右上角「账号 > 账号信息」
    VOLC_ACCOUNT_ID = os.getenv("VOLC_ACCOUNT_ID", "")
    # 知识库集合名：https://console.volcengine.com/vikingdb/knowledge/region:vdb-knowledge+cn-beijing/collection/list
    KB_COLLECTION_NAME = os.getenv("KB_COLLECTION_NAME", "")
    KB_PROJECT_NAME = os.getenv("KB_PROJECT_NAME", "default")

settings = Config()