# -*- coding: utf-8 -*-
"""
K线图绘制模块 - 使用 mplfinance 绘制专业K线图

核心功能:
  1. 绘制标准K线图（含MA均线、成交量）
  2. 自定义涨跌颜色（红涨绿跌 - A股风格）
  3. 导出为图片文件（供企业微信推送使用）
"""

import logging
import io
import os
from typing import Optional, Dict, Any, List, Tuple

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")  # 无GUI后端，仅用于导出图片
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import mplfinance as mpf

# 配置中文字体（解决K线图中文显示为方框的问题）
# 优先级: 微软雅黑 > SimHei > WenQuanYi
matplotlib.rcParams["font.sans-serif"] = [
    "Microsoft YaHei", "SimHei", "WenQuanYi Zen Hei",
    "WenQuanYi Micro Hei", "DejaVu Sans",
]
matplotlib.rcParams["axes.unicode_minus"] = False

logger = logging.getLogger(__name__)


class ChartDrawer:
    """K线图绘制器"""

    def __init__(self, style_config: Optional[Dict[str, Any]] = None):
        """
        初始化绘制器

        Args:
            style_config: 图表样式配置字典，包含:
              - candle_up: 涨色
              - candle_down: 跌色
              - bg_color: 背景色
              - grid_color: 网格色
              - ma_colors: MA线颜色列表 [MA5, MA10, MA20]
        """
        self.config = style_config or {}

        # 默认样式
        self.candle_up = self.config.get("candle_up", "#EF4444")
        self.candle_down = self.config.get("candle_down", "#22C55E")
        self.bg_color = self.config.get("bg_color", "#FFFFFF")
        self.grid_color = self.config.get("grid_color", "#E5E7EB")
        self.ma_colors = self.config.get("ma_colors", ["#F59E0B", "#3B82F6", "#8B5CF6"])

        # 创建自定义mplfinance样式
        self._mpf_style = self._create_mpf_style()

    def _create_mpf_style(self) -> dict:
        """创建 mplfinance 自定义样式（红涨绿跌）"""
        # 解析颜色为RGB元组
        def hex_to_rgb(hex_color: str) -> Tuple[float, float, float]:
            h = hex_color.lstrip("#")
            return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))

        up_rgb = hex_to_rgb(self.candle_up)
        down_rgb = hex_to_rgb(self.candle_down)

        style = mpf.make_mpf_style(
            base_mpf_style="default",
            marketcolors=mpf.make_marketcolors(
                up=up_rgb,
                down=down_rgb,
                edge=up_rgb,
                wick=up_rgb,
                volume={"up": (0.937, 0.267, 0.267, 0.5),
                         "down": (0.133, 0.773, 0.369, 0.5)},
            ),
            facecolor=self.bg_color,
            gridcolor=self.grid_color,
            gridaxis="both",
            gridstyle="--",
            rc={
                "axes.labelcolor": "#374151",
                "xtick.color": "#6B7280",
                "ytick.color": "#6B7280",
                "font.sans-serif": [
                    "Microsoft YaHei", "SimHei", "WenQuanYi Zen Hei",
                    "WenQuanYi Micro Hei", "DejaVu Sans",
                ],
                "axes.unicode_minus": False,
            },
        )
        return style

    # ============================================================
    #  公共绘制方法
    # ============================================================

    def draw_kline(self, df: pd.DataFrame, title: str = "CS饰品指数K线",
                   ma_periods: List[int] = None, volume: bool = True,
                   tail_days: Optional[int] = None) -> bytes:
        """
        绘制K线图并返回图片字节流

        Args:
            df: K线 DataFrame，需含 open/close/high/low/volume 列，index为日期
            title: 图表标题
            ma_periods: MA周期列表，默认 [5, 10, 20]
            volume: 是否显示成交量副图
            tail_days: 仅显示最近N天数据（None=显示全部）

        Returns:
            PNG图片字节流 (bytes)
        """
        if ma_periods is None:
            ma_periods = [5, 10, 20]

        if df.empty:
            logger.warning("K线数据为空，无法绘制")
            return self._generate_empty_chart(title)

        # 性能优化：如果指定了tail_days，先截取数据再计算MA
        if tail_days:
            # 为了计算MA，需要比tail_days更多的数据（最大MA周期）
            max_ma_period = max(ma_periods) if ma_periods else 20
            # 获取足够的数据来计算MA
            required_data = df.tail(tail_days + max_ma_period)
            plot_df = required_data.copy()
            
            # 在截取的数据上计算MA
            for period in ma_periods:
                # 使用 min_periods=1 确保即使数据不足也能计算部分值
                plot_df[f"MA{period}"] = plot_df["close"].rolling(window=period, min_periods=1).mean()
            
            # 只保留最后tail_days的数据用于显示
            plot_df = plot_df.tail(tail_days)
        else:
            # 显示全部数据，使用完整数据计算MA
            plot_df = df.copy()
            for period in ma_periods:
                # 使用 min_periods=1 确保即使数据不足也能计算部分值
                plot_df[f"MA{period}"] = plot_df["close"].rolling(window=period, min_periods=1).mean()

        # 准备附加线
        add_plots = []
        for i, period in enumerate(ma_periods):
            color = self.ma_colors[i] if i < len(self.ma_colors) else "#888888"
            add_plots.append(
                mpf.make_addplot(
                    plot_df[f"MA{period}"],
                    color=color,
                    width=1.0,
                    label=f"MA{period}",
                )
            )

        # 绘制
        kwargs = {
            "type": "candle",
            "style": self._mpf_style,
            "title": title,
            "volume": volume,
            "addplot": add_plots if add_plots else None,
            "figratio": (12, 7),
            "figscale": 1.2,
            "datetime_format": "%m-%d",
            "xrotation": 30,
            "tight_layout": True,
            "returnfig": True,
        }

        try:
            mc, axlist = mpf.plot(plot_df, **kwargs)

            # 添加MA图例
            if add_plots and len(axlist) > 1:
                ax = axlist[0]
                handles = [
                    plt.Line2D([0], [0], color=self.ma_colors[i] if i < len(self.ma_colors) else "#888",
                               linewidth=1.0, label=f"MA{ma_periods[i]}")
                    for i in range(len(ma_periods))
                ]
                ax.legend(handles=handles, loc="upper left", fontsize=8,
                          framealpha=0.7, edgecolor="#D1D5DB")

            # 保存到字节流
            buf = io.BytesIO()
            mc.savefig(buf, format="png", dpi=100, bbox_inches="tight",
                       facecolor=self.bg_color, edgecolor="none")
            plt.close(mc)
            buf.seek(0)

            logger.info("K线图绘制完成: %s (%d条数据)", title, len(plot_df))
            return buf.getvalue()

        except Exception as e:
            logger.error("K线图绘制失败: %s", e, exc_info=True)
            return self._generate_empty_chart(title)

    def draw_kline_to_file(self, df: pd.DataFrame, filepath: str,
                           title: str = "CS饰品指数K线",
                           ma_periods: List[int] = None,
                           volume: bool = True,
                           tail_days: Optional[int] = None) -> str:
        """
        绘制K线图并保存到文件

        Args:
            df: K线 DataFrame
            filepath: 输出文件路径（.png）
            title: 图表标题
            ma_periods: MA周期列表
            volume: 是否显示成交量
            tail_days: 仅显示最近N天

        Returns:
            保存的文件绝对路径
        """
        img_bytes = self.draw_kline(df, title, ma_periods, volume, tail_days)

        # 确保目录存在
        os.makedirs(os.path.dirname(filepath) if os.path.dirname(filepath) else ".", exist_ok=True)

        with open(filepath, "wb") as f:
            f.write(img_bytes)

        abs_path = os.path.abspath(filepath)
        logger.info("K线图已保存: %s (%d bytes)", abs_path, len(img_bytes))
        return abs_path

    # ============================================================
    #  多指数对比图
    # ============================================================

    def draw_multi_index_comparison(self, data_map: Dict[str, pd.DataFrame],
                                     title: str = "指数走势对比") -> bytes:
        """
        绘制多个指数的收盘价走势对比图

        Args:
            data_map: {指数名: DataFrame} 字典
            title: 图表标题

        Returns:
            PNG图片字节流
        """
        fig, ax = plt.subplots(figsize=(12, 6))
        fig.patch.set_facecolor(self.bg_color)
        ax.set_facecolor(self.bg_color)

        colors = ["#EF4444", "#3B82F6", "#F59E0B", "#8B5CF6",
                  "#22C55E", "#EC4899", "#06B6D4", "#F97316"]

        for i, (name, df) in enumerate(data_map.items()):
            if df.empty:
                continue
            color = colors[i % len(colors)]
            ax.plot(df.index, df["close"], label=name, color=color, linewidth=1.5)

        ax.set_title(title, fontsize=14, fontweight="bold", color="#1F2937")
        ax.set_xlabel("日期", fontsize=10, color="#6B7280")
        ax.set_ylabel("指数", fontsize=10, color="#6B7280")
        ax.legend(fontsize=8, loc="upper left", framealpha=0.8)
        ax.grid(True, alpha=0.3, color=self.grid_color)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        fig.autofmt_xdate(rotation=30)
        fig.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100, facecolor=self.bg_color)
        plt.close(fig)
        buf.seek(0)

        return buf.getvalue()

    # ============================================================
    #  内部工具方法
    # ============================================================

    def _generate_empty_chart(self, title: str) -> bytes:
        """生成空数据占位图"""
        fig, ax = plt.subplots(figsize=(8, 4))
        fig.patch.set_facecolor(self.bg_color)
        ax.set_facecolor(self.bg_color)
        ax.text(0.5, 0.5, "暂无K线数据", transform=ax.transAxes,
                fontsize=16, ha="center", va="center", color="#9CA3AF")
        ax.set_title(title, fontsize=14, color="#6B7280")
        ax.axis("off")

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100, facecolor=self.bg_color)
        plt.close(fig)
        buf.seek(0)
        return buf.getvalue()
