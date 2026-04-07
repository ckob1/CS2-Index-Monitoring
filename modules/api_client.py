# -*- coding: utf-8 -*-
"""
API 客户端模块 - 负责与 CSQAQ API 交互

接口说明（来自官方文档）:
  1. GET /api/v1/current_data   -> 获取首页相关数据（所有子指数概览）
     - 参数: type (可选, 指定name_key筛选)
     - 返回: sub_index_data[] 包含 id/name/market_index/chg_num/chg_rate/open/close/high/low/updated_at

  2. GET /api/v1/sub_data       -> 获取指数详情数据（时间序列）
     - 参数: id (子指数id), type (daily/...)
     - 返回: data.timestamp[] + 各字段时间序列

  3. GET /api/v1/sub/kline      -> 获取指数K线图
     - 参数: id (子指数id), type (1day/1week/1month)
     - 返回: data[] 包含 t/o/c/h/l/v (时间戳/开/收/高/低/量)

所有接口通过 Header 传递 ApiToken 进行鉴权。
"""

import time
import logging
from typing import Optional, Dict, Any, List

import requests

logger = logging.getLogger(__name__)


class CSQAQClient:
    """CSQAQ API 客户端"""

    def __init__(self, base_url: str, api_token: str, timeout: int = 30,
                 retry_times: int = 3, retry_delay: int = 2):
        """
        初始化客户端

        Args:
            base_url: API基础地址，如 https://api.csqaq.com
            api_token: API鉴权令牌
            timeout: 请求超时(秒)
            retry_times: 失败重试次数
            retry_delay: 重试间隔(秒)
        """
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self.timeout = timeout
        self.retry_times = retry_times
        self.retry_delay = retry_delay

        # 默认请求头
        self._headers = {
            "ApiToken": self.api_token,
            "User-Agent": "CSIndexAnalyzer/1.0",
        }

        # 缓存字典 {cache_key: (timestamp, data)}
        self._cache: Dict[str, tuple] = {}

    # ============================================================
    #  公共接口
    # ============================================================

    def get_current_data(self, index_type: Optional[str] = None) -> Dict[str, Any]:
        """
        获取首页相关数据（所有子指数概览）

        API: GET /api/v1/current_data

        Args:
            index_type: 可选，按 name_key 筛选特定指数（如 "init" 表示饰品指数）

        Returns:
            响应JSON，关键结构:
            {
              "code": 200,
              "msg": "Success",
              "data": {
                "sub_index_data": [
                  {
                    "id": 1,
                    "name": "饰品指数",
                    "name_key": "init",
                    "market_index": 2119.85,
                    "chg_num": 26.09,
                    "chg_rate": 1.25,
                    "open": 2094.11,
                    "close": 2119.85,
                    "high": 2121.56,
                    "low": 2094.11,
                    "updated_at": "2025-11-21T17:47:07"
                  },
                  ...
                ]
              }
            }
        """
        params = {}
        if index_type:
            params["type"] = index_type

        cache_key = f"current_data_{index_type or 'all'}"
        return self._request("GET", "/api/v1/current_data", params=params, cache_key=cache_key)

    def get_sub_data(self, sub_id: int, data_type: str = "daily") -> Dict[str, Any]:
        """
        获取单个子指数的详情数据（时间序列）

        API: GET /api/v1/sub_data

        Args:
            sub_id: 子指数ID（从 current_data 的 sub_index_data[].id 获取）
            data_type: 数据周期类型，如 "daily"

        Returns:
            响应JSON，包含 timestamp[] 时间序列及对应字段数据
        """
        params = {"id": sub_id, "type": data_type}
        cache_key = f"sub_data_{sub_id}_{data_type}"
        return self._request("GET", "/api/v1/sub_data", params=params, cache_key=cache_key)

    def get_kline_data(self, sub_id: int, kline_type: str = "1day") -> List[Dict[str, Any]]:
        """
        获取单个子指数的K线数据

        API: GET /api/v1/sub/kline

        Args:
            sub_id: 子指数ID（从 current_data 的 sub_index_data[].id 获取）
            kline_type: K线周期，可选 "1day" / "1week" / "1month"

        Returns:
            K线数据列表，每条记录结构:
            {
              "t": "1700150400000",   # 时间戳(毫秒)
              "o": 1402.74,           # 开盘价
              "c": 1385.55,           # 收盘价
              "h": 1402.74,           # 最高价
              "l": 1385.55,           # 最低价
              "v": 0                  # 成交量
            }
        """
        params = {"id": sub_id, "type": kline_type}
        cache_key = f"kline_{sub_id}_{kline_type}"
        resp = self._request("GET", "/api/v1/sub/kline", params=params, cache_key=cache_key)
        if resp.get("code") == 200:
            return resp.get("data", [])
        logger.warning("获取K线数据失败: %s", resp.get("msg", ""))
        return []

    # ============================================================
    #  缓存控制
    # ============================================================

    def clear_cache(self):
        """清空所有缓存"""
        self._cache.clear()
        logger.info("API缓存已清空")

    # ============================================================
    #  内部方法
    # ============================================================

    def _request(self, method: str, endpoint: str,
                 params: Optional[Dict] = None,
                 cache_key: Optional[str] = None,
                 cache_ttl: int = 60) -> Dict[str, Any]:
        """
        发送HTTP请求（带重试和缓存）

        Args:
            method: HTTP方法 (GET/POST)
            endpoint: API端点路径
            params: 查询参数
            cache_key: 缓存键（为None则不缓存）
            cache_ttl: 缓存有效期(秒)

        Returns:
            解析后的JSON响应字典
        """
        # 检查缓存
        if cache_key and cache_key in self._cache:
            cached_time, cached_data = self._cache[cache_key]
            if time.time() - cached_time < cache_ttl:
                logger.debug("命中缓存: %s", cache_key)
                return cached_data

        url = f"{self.base_url}{endpoint}"

        for attempt in range(1, self.retry_times + 1):
            try:
                resp = requests.request(
                    method=method,
                    url=url,
                    params=params,
                    headers=self._headers,
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                data = resp.json()

                if data.get("code") != 200:
                    logger.warning("API返回错误 [code=%s]: %s",
                                   data.get("code"), data.get("msg", ""))
                    return data

                # 写入缓存
                if cache_key:
                    self._cache[cache_key] = (time.time(), data)

                return data

            except requests.exceptions.Timeout:
                logger.warning("请求超时 (%s) - 第 %d/%d 次", url, attempt, self.retry_times)
            except requests.exceptions.ConnectionError as e:
                logger.warning("连接错误: %s - 第 %d/%d 次", e, attempt, self.retry_times)
            except requests.exceptions.HTTPError as e:
                status = resp.status_code
                if status == 401:
                    logger.error("鉴权失败(401): 请确认ApiToken正确且已在CSQAQ官网绑定当前IP白名单")
                    return {"code": 401, "msg": "鉴权失败，请检查ApiToken或绑定IP白名单", "data": None}
                logger.error("HTTP错误: %s", e)
                return {"code": status, "msg": str(e), "data": None}
            except ValueError as e:
                logger.error("JSON解析失败: %s", e)
                return {"code": -1, "msg": f"JSON解析失败: {e}", "data": None}

            if attempt < self.retry_times:
                time.sleep(self.retry_delay)

        logger.error("请求失败，已达最大重试次数: %s", url)
        return {"code": -1, "msg": "请求超时，请稍后重试", "data": None}
