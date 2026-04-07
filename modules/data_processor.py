# -*- coding: utf-8 -*-
"""
数据处理模块 - 负责解析、转换和计算CSQAQ返回数据

核心功能:
  1. 解析首页数据 -> 结构化子指数信息
  2. 解析K线数据 -> DataFrame格式，计算MA均线等指标
  3. 生成企业微信推送所需的格式化文本
"""

import logging
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class DataProcessor:
    """CS饰品指数数据处理器"""

    def __init__(self):
        self._last_current_data: Optional[Dict[str, Any]] = None

    # ============================================================
    #  首页数据解析
    # ============================================================

    def parse_current_data(self, raw_resp: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        解析首页数据响应，提取所有子指数信息

        Args:
            raw_resp: API原始响应（来自 CSQAQClient.get_current_data）

        Returns:
            子指数列表，每条包含:
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
                "updated_at": "2025-11-21T17:47:07",
                "direction": "up"     # 辅助字段: up/down/flat
            }
        """
        if raw_resp.get("code") != 200:
            logger.error("首页数据响应异常: %s", raw_resp.get("msg"))
            return []

        sub_data = raw_resp.get("data", {}).get("sub_index_data", [])
        if not sub_data:
            logger.warning("首页数据为空")
            return []

        parsed = []
        for item in sub_data:
            chg_rate = float(item.get("chg_rate", 0))
            if chg_rate > 0:
                direction = "up"
            elif chg_rate < 0:
                direction = "down"
            else:
                direction = "flat"

            parsed.append({
                "id": item.get("id"),
                "name": item.get("name", ""),
                "name_key": item.get("name_key", ""),
                "market_index": float(item.get("market_index", 0)),
                "chg_num": float(item.get("chg_num", 0)),
                "chg_rate": chg_rate,
                "open": float(item.get("open", 0)),
                "close": float(item.get("close", 0)),
                "high": float(item.get("high", 0)),
                "low": float(item.get("low", 0)),
                "updated_at": item.get("updated_at", ""),
                "direction": direction,
            })

        self._last_current_data = raw_resp
        logger.info("成功解析 %d 个子指数数据", len(parsed))
        return parsed

    def get_index_by_name_key(self, indices: List[Dict], name_key: str) -> Optional[Dict]:
        """按 name_key 查找特定指数"""
        for idx in indices:
            if idx.get("name_key") == name_key:
                return idx
        return None

    # ============================================================
    #  K线数据解析
    # ============================================================

    def parse_kline_data(self, raw_kline: List[Dict[str, Any]]) -> pd.DataFrame:
        """
        将API返回的K线原始数据转换为 pandas DataFrame

        API返回格式:
        [{"t": "1700150400000", "o": 1402.74, "c": 1385.55, "h": 1402.74, "l": 1385.55, "v": 0}, ...]

        Args:
            raw_kline: API原始K线数据列表

        Returns:
            DataFrame，列: date, open, close, high, low, volume
        """
        if not raw_kline:
            logger.warning("K线原始数据为空")
            return pd.DataFrame()

        records = []
        for item in raw_kline:
            try:
                ts_ms = int(item.get("t", 0))
                records.append({
                    "date": pd.Timestamp(ts_ms, unit="ms"),
                    "open": float(item.get("o", 0)),
                    "close": float(item.get("c", 0)),
                    "high": float(item.get("h", 0)),
                    "low": float(item.get("l", 0)),
                    "volume": float(item.get("v", 0)),
                })
            except (ValueError, TypeError) as e:
                logger.debug("跳过异常K线数据: %s", e)
                continue

        df = pd.DataFrame(records)
        df.set_index("date", inplace=True)
        df.sort_index(inplace=True)
        logger.info("K线数据解析完成，共 %d 条记录", len(df))
        return df

    # ============================================================
    #  技术指标计算
    # ============================================================

    @staticmethod
    def calc_ma(df: pd.DataFrame, periods: List[int] = None) -> pd.DataFrame:
        """
        计算移动平均线(MA)

        Args:
            df: K线DataFrame（需含 'close' 列）
            periods: MA周期列表，默认 [5, 10, 20]

        Returns:
            添加了 MA5, MA10, MA20... 列的DataFrame
        """
        if periods is None:
            periods = [5, 10, 20]

        result = df.copy()
        for period in periods:
            col_name = f"MA{period}"
            result[col_name] = result["close"].rolling(window=period).mean().round(2)
            logger.debug("已计算 %s", col_name)

        return result

    @staticmethod
    def calc_changes(df: pd.DataFrame) -> pd.DataFrame:
        """
        计算涨跌额和涨跌幅

        Returns:
            添加了 'change' 和 'pct_change' 列的DataFrame
        """
        result = df.copy()
        result["change"] = result["close"].diff()
        result["pct_change"] = result["close"].pct_change() * 100
        return result

    # ============================================================
    #  企业微信推送文本生成
    # ============================================================

    def format_index_summary_md(self, indices: List[Dict[str, Any]]) -> str:
        """
        生成指数概览的 Markdown 文本（用于企业微信推送）

        Args:
            indices: parse_current_data() 返回的子指数列表

        Returns:
            Markdown格式的指数概览文本
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            f"## CS饰品指数行情速报",
            f"> 数据更新时间: {now}",
            "",
        ]

        for idx in indices:
            direction = idx.get("direction", "flat")
            chg_num = idx.get("chg_num", 0)
            chg_rate = idx.get("chg_rate", 0)

            # 涨跌箭头和颜色标记
            if direction == "up":
                arrow = "▲"
                sign = "+"
            elif direction == "down":
                arrow = "▼"
                sign = ""
            else:
                arrow = "—"
                sign = ""

            name = idx.get("name", "未知")
            mi = idx.get("market_index", 0)
            open_p = idx.get("open", 0)
            high_p = idx.get("high", 0)
            low_p = idx.get("low", 0)

            lines.append(
                f"### {name}"
            )
            lines.append(
                f"> 当前指数: **{mi:.2f}**　"
                f"{arrow} {sign}{chg_num:.2f} ({sign}{chg_rate:.2f}%)"
            )
            lines.append(
                f"> 开盘: {open_p:.2f}　最高: {high_p:.2f}　最低: {low_p:.2f}"
            )
            lines.append("")

        # 添加汇总统计
        up_count = sum(1 for i in indices if i.get("direction") == "up")
        down_count = sum(1 for i in indices if i.get("direction") == "down")
        flat_count = sum(1 for i in indices if i.get("direction") == "flat")
        avg_rate = np.mean([i.get("chg_rate", 0) for i in indices]) if indices else 0

        lines.append("---")
        lines.append(
            f"**市场概况:** 上涨 {up_count} / 下跌 {down_count} / 持平 {flat_count}　|　"
            f"平均涨跌幅: {avg_rate:+.2f}%"
        )

        return "\n".join(lines)

    def format_index_detail_md(self, index_info: Dict[str, Any],
                                kline_df: Optional[pd.DataFrame] = None,
                                tail_days: int = 5) -> str:
        """
        生成单个指数详情的 Markdown 文本

        Args:
            index_info: 单个子指数信息字典
            kline_df: K线DataFrame（可选）
            tail_days: 展示最近几天的K线数据

        Returns:
            Markdown格式文本
        """
        name = index_info.get("name", "未知")
        mi = index_info.get("market_index", 0)
        chg_num = index_info.get("chg_num", 0)
        chg_rate = index_info.get("chg_rate", 0)
        direction = index_info.get("direction", "flat")

        if direction == "up":
            trend = "📈 上涨"
        elif direction == "down":
            trend = "📉 下跌"
        else:
            trend = "➡️ 持平"

        lines = [
            f"### {name} {trend}",
            f"> 指数: **{mi:.2f}**　涨跌: {chg_num:+.2f} ({chg_rate:+.2f}%)",
            f"> 开盘: {index_info.get('open', 0):.2f}　"
            f"最高: {index_info.get('high', 0):.2f}　"
            f"最低: {index_info.get('low', 0):.2f}",
        ]

        if kline_df is not None and not kline_df.empty:
            recent = kline_df.tail(tail_days)
            lines.append("")
            lines.append("**近期走势:**")
            for date, row in recent.iterrows():
                date_str = date.strftime("%m-%d") if hasattr(date, "strftime") else str(date)
                chg = row["close"] - row["open"]
                sign = "+" if chg >= 0 else ""
                lines.append(
                    f"> {date_str}　收盘: {row['close']:.2f}　"
                    f"({sign}{chg:.2f})"
                )

        return "\n".join(lines)

    # ============================================================
    #  数据过滤（推送策略）
    # ============================================================

    def filter_by_threshold(self, indices: List[Dict[str, Any]],
                            threshold: float) -> List[Dict[str, Any]]:
        """
        按涨跌幅阈值过滤指数

        Args:
            indices: 子指数列表
            threshold: 涨跌幅绝对值阈值，仅返回超过该值的指数

        Returns:
            过滤后的子指数列表
        """
        if threshold <= 0:
            return indices

        filtered = [
            idx for idx in indices
            if abs(idx.get("chg_rate", 0)) >= threshold
        ]
        logger.info("阈值过滤: %d/%d 个指数触发推送", len(filtered), len(indices))
        return filtered

    def filter_by_name_keys(self, indices: List[Dict[str, Any]],
                            target_keys: List[str]) -> List[Dict[str, Any]]:
        """
        按name_key白名单过滤指数

        Args:
            indices: 子指数列表
            target_keys: 目标name_key列表，空列表=不过滤

        Returns:
            过滤后的子指数列表
        """
        if not target_keys:
            return indices

        filtered = [
            idx for idx in indices
            if idx.get("name_key") in target_keys
        ]
        logger.info("名称过滤: %d/%d 个指数匹配", len(filtered), len(indices))
        return filtered
