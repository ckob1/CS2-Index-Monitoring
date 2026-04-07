# -*- coding: utf-8 -*-
"""
UI 主界面模块 - 基于 PyQt5 的CS饰品指数分析工具图形界面

界面布局:
  ┌──────────────────────────────────────────────────────────┐
  │  工具栏: [刷新数据] [推送全部] [推送所选] [导出图片]      │
  ├──────────────┬───────────────────────────────────────────┤
  │              │  指数概览面板 (Table)                      │
  │  指数列表     │  - 名称 / 指数值 / 涨跌 / 开高低收        │
  │  (ListWidget)│───────────────────────────────────────────│
  │              │  K线图展示区 (matplotlib嵌入)               │
  │  - 饰品指数   │                                          │
  │  - 租赁指数   │  [MA5/MA10/MA20] [1day/1week/1month]     │
  │  - 百元主战   │                                          │
  │  - ...       │───────────────────────────────────────────│
  │              │  推送预览面板 (TextEdit)                    │
  │              │  显示将要推送的Markdown文本                  │
  ├──────────────┴───────────────────────────────────────────┤
  │  状态栏: [连接状态] [最后更新时间] [自动刷新倒计时]         │
  └──────────────────────────────────────────────────────────┘
"""

import logging
import os
import sys
from typing import Optional, Dict, Any, List

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QGroupBox, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QListWidget, QListWidgetItem,
    QTextEdit, QComboBox, QCheckBox, QSpinBox, QDoubleSpinBox, QToolBar, QAction,
    QStatusBar, QFileDialog, QMessageBox, QProgressBar, QFrame,
    QAbstractItemView, QSizePolicy
)

from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QThread
from PyQt5.QtGui import QFont, QColor, QIcon, QPixmap, QPalette

import pandas as pd
import numpy as np
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

logger = logging.getLogger(__name__)


# ============================================================
#  工作线程（避免UI阻塞）
# ============================================================

class FetchDataThread(QThread):
    """后台数据获取线程"""
    finished = pyqtSignal(dict)    # 完成信号，携带结果
    error = pyqtSignal(str)        # 错误信号

    def __init__(self, client, func_name: str, **kwargs):
        super().__init__()
        self.client = client
        self.func_name = func_name
        self.kwargs = kwargs

    def run(self):
        try:
            func = getattr(self.client, self.func_name)
            result = func(**self.kwargs)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class PushDataThread(QThread):
    """后台推送线程"""
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, pusher, md_content: str, kline_images: List[bytes] = None):
        super().__init__()
        self.pusher = pusher
        self.md_content = md_content
        self.kline_images = kline_images or []

    def run(self):
        try:
            result = self.pusher.push_index_report(self.md_content, self.kline_images)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


# ============================================================
#  嵌入式 matplotlib 画布
# ============================================================

class KlineCanvas(FigureCanvas):
    """K线图嵌入式画布"""

    def __init__(self, parent=None, width=10, height=6, dpi=100):
        self.fig = Figure(figsize=(width, height), dpi=dpi)
        self.fig.patch.set_facecolor("#FFFFFF")
        self.ax = self.fig.add_subplot(111)
        super().__init__(self.fig)
        self.setParent(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def clear_chart(self):
        """清空画布"""
        self.ax.clear()
        self.ax.set_facecolor("#FFFFFF")
        self.ax.text(0.5, 0.5, "请选择左侧指数查看K线图",
                     transform=self.ax.transAxes, fontsize=14,
                     ha="center", va="center", color="#9CA3AF")
        self.ax.axis("off")
        self.draw()

    def update_chart(self, image_bytes: bytes):
        """用图片更新画布"""
        self.ax.clear()
        self.ax.axis("off")
        self.fig.clear()

        ax_img = self.fig.add_axes([0, 0, 1, 1])
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(image_bytes))
        ax_img.imshow(img)
        ax_img.axis("off")
        self.draw()


# ============================================================
#  主窗口
# ============================================================

class MainWindow(QMainWindow):
    """CS饰品指数分析工具 - 主窗口"""

    def __init__(self, config: Dict[str, Any], client, processor, drawer, pusher):
        super().__init__()
        self.config = config
        self.client = client
        self.processor = processor
        self.drawer = drawer
        self.pusher = pusher

        # 数据缓存
        self.indices_data: List[Dict[str, Any]] = []
        self.kline_cache: Dict[int, pd.DataFrame] = {}
        self.current_selected_index: Optional[Dict[str, Any]] = None

        # 定时器
        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(self.auto_refresh)

        # 初始化UI
        self._init_ui()
        self._init_toolbar()
        self._init_statusbar()
        self._connect_signals()

        # 加载数据
        QTimer.singleShot(500, self.refresh_all_data)

    def _init_ui(self):
        """初始化界面布局"""
        ui_cfg = self.config.get("ui", {})
        w, h = ui_cfg.get("window_width", 1200), ui_cfg.get("window_height", 800)
        title = ui_cfg.get("window_title", "CS饰品指数分析工具")
        self.setWindowTitle(title)
        self.resize(w, h)

        # 中央部件
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(8, 8, 8, 8)

        # === 左侧面板：指数列表 + 控制选项 ===
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_panel.setFixedWidth(220)

        # 指数列表
        list_group = QGroupBox("指数列表")
        list_layout = QVBoxLayout(list_group)
        self.index_list = QListWidget()
        self.index_list.setSelectionMode(QAbstractItemView.SingleSelection)
        list_layout.addWidget(self.index_list)
        left_layout.addWidget(list_group)

        # K线设置
        kline_group = QGroupBox("K线设置")
        kline_layout = QVBoxLayout(kline_group)

        # K线周期
        h1 = QHBoxLayout()
        h1.addWidget(QLabel("周期:"))
        self.kline_type_combo = QComboBox()
        for kt in self.config.get("api", {}).get("kline_types", []):
            self.kline_type_combo.addItem(kt["label"], kt["key"])
        h1.addWidget(self.kline_type_combo)
        kline_layout.addLayout(h1)

        # K线天数
        h2 = QHBoxLayout()
        h2.addWidget(QLabel("天数:"))
        self.kline_days_spin = QSpinBox()
        self.kline_days_spin.setRange(7, 365)
        self.kline_days_spin.setValue(60)
        h2.addWidget(self.kline_days_spin)
        kline_layout.addLayout(h2)

        # MA均线开关
        self.ma_check = QCheckBox("显示MA均线")
        self.ma_check.setChecked(True)
        kline_layout.addWidget(self.ma_check)

        # 成交量开关
        self.volume_check = QCheckBox("显示成交量")
        self.volume_check.setChecked(True)
        kline_layout.addWidget(self.volume_check)

        left_layout.addWidget(kline_group)

        # 推送设置
        push_group = QGroupBox("推送设置")
        push_layout = QVBoxLayout(push_group)
        self.threshold_spin = QDoubleSpinBox()
        self.threshold_spin.setRange(0, 50)
        self.threshold_spin.setValue(self.config.get("push_strategy", {}).get("alert_threshold", 0))
        self.threshold_spin.setSuffix("%")
        h3 = QHBoxLayout()
        h3.addWidget(QLabel("阈值:"))
        h3.addWidget(self.threshold_spin)
        push_layout.addLayout(h3)
        push_layout.addWidget(QLabel("(0=全部推送)"))
        left_layout.addWidget(push_group)

        main_layout.addWidget(left_panel)

        # === 右侧面板：数据表格 + K线图 + 推送预览 ===
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Vertical)

        # 上部：指数数据表
        self.data_table = QTableWidget()
        self.data_table.setColumnCount(8)
        self.data_table.setHorizontalHeaderLabels(
            ["名称", "当前指数", "涨跌额", "涨跌幅", "开盘", "最高", "最低", "更新时间"]
        )
        self.data_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.data_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.data_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.data_table.setAlternatingRowColors(True)
        splitter.addWidget(self.data_table)

        # 中部：K线图
        self.kline_canvas = KlineCanvas(self, width=10, height=5)
        splitter.addWidget(self.kline_canvas)

        # 下部：推送预览
        preview_group = QGroupBox("推送预览 (Markdown)")
        preview_layout = QVBoxLayout(preview_group)
        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_text.setMaximumHeight(200)
        self.preview_text.setFont(QFont("Consolas", 9))
        preview_layout.addWidget(self.preview_text)
        splitter.addWidget(preview_group)

        splitter.setSizes([250, 350, 200])
        right_layout.addWidget(splitter)
        main_layout.addWidget(right_panel, 1)

        # 初始化画布占位
        self.kline_canvas.clear_chart()

    def _init_toolbar(self):
        """初始化工具栏"""
        toolbar = self.addToolBar("主工具栏")
        toolbar.setMovable(False)
        toolbar.setIconSize(self.size().__class__(24, 24))

        # 刷新按钮
        self.act_refresh = QAction("刷新数据", self)
        self.act_refresh.setStatusTip("从API刷新最新数据")
        self.act_refresh.triggered.connect(self.refresh_all_data)
        toolbar.addAction(self.act_refresh)

        toolbar.addSeparator()

        # 推送全部
        self.act_push_all = QAction("推送全部指数", self)
        self.act_push_all.setStatusTip("将所有指数数据推送至企业微信")
        self.act_push_all.triggered.connect(self.push_all_indices)
        toolbar.addAction(self.act_push_all)

        # 推送选中
        self.act_push_selected = QAction("推送选中指数", self)
        self.act_push_selected.setStatusTip("推送当前选中的指数")
        self.act_push_selected.triggered.connect(self.push_selected_index)
        toolbar.addAction(self.act_push_selected)

        toolbar.addSeparator()

        # 导出图片
        self.act_export = QAction("导出K线图", self)
        self.act_export.setStatusTip("保存当前K线图为PNG文件")
        self.act_export.triggered.connect(self.export_kline_image)
        toolbar.addAction(self.act_export)

        toolbar.addSeparator()

        # 自动刷新开关
        self.act_auto_refresh = QAction("自动刷新: 关", self)
        self.act_auto_refresh.setCheckable(True)
        interval = self.config.get("ui", {}).get("refresh_interval", 60)
        self.act_auto_refresh.triggered.connect(self.toggle_auto_refresh)
        toolbar.addAction(self.act_auto_refresh)

    def _init_statusbar(self):
        """初始化状态栏"""
        self.statusbar = QStatusBar()
        self.setStatusBar(self.statusbar)

        self.lbl_status = QLabel("就绪")
        self.lbl_update_time = QLabel("最后更新: --")
        self.lbl_countdown = QLabel("")

        self.statusbar.addWidget(self.lbl_status, 1)
        self.statusbar.addPermanentWidget(self.lbl_update_time)
        self.statusbar.addPermanentWidget(self.lbl_countdown)

    def _connect_signals(self):
        """连接信号与槽"""
        self.index_list.currentRowChanged.connect(self.on_index_selected)
        self.kline_type_combo.currentIndexChanged.connect(self.on_kline_type_changed)
        self.kline_days_spin.valueChanged.connect(self.on_kline_setting_changed)
        self.ma_check.stateChanged.connect(self.on_kline_setting_changed)
        self.volume_check.stateChanged.connect(self.on_kline_setting_changed)

    # ============================================================
    #  数据刷新
    # ============================================================

    def refresh_all_data(self):
        """刷新所有数据（异步）"""
        self.lbl_status.setText("正在刷新数据...")
        self.act_refresh.setEnabled(False)

        self._fetch_thread = FetchDataThread(self.client, "get_current_data")
        self._fetch_thread.finished.connect(self._on_data_refreshed)
        self._fetch_thread.error.connect(self._on_fetch_error)
        self._fetch_thread.start()

    def _on_data_refreshed(self, result: Dict[str, Any]):
        """数据刷新完成回调"""
        self.indices_data = self.processor.parse_current_data(result)
        self._update_index_list()
        self._update_data_table()
        self._update_push_preview()

        self.act_refresh.setEnabled(True)
        from datetime import datetime
        now = datetime.now().strftime("%H:%M:%S")
        self.lbl_update_time.setText(f"最后更新: {now}")
        self.lbl_status.setText(f"已加载 {len(self.indices_data)} 个指数")

        # 清除K线缓存以获取最新数据
        self.kline_cache.clear()

        # 自动选中第一个
        if self.indices_data and self.index_list.count() > 0:
            self.index_list.setCurrentRow(0)

    def _on_fetch_error(self, error_msg: str):
        """数据获取失败回调"""
        self.act_refresh.setEnabled(True)
        self.lbl_status.setText(f"数据获取失败: {error_msg}")
        QMessageBox.warning(self, "错误", f"获取数据失败:\n{error_msg}")

    # ============================================================
    #  UI更新方法
    # ============================================================

    def _update_index_list(self):
        """更新左侧指数列表"""
        self.index_list.clear()
        for idx in self.indices_data:
            item = QListWidgetItem()
            name = idx.get("name", "未知")
            chg_rate = idx.get("chg_rate", 0)

            if idx.get("direction") == "up":
                text = f"📈 {name}  {chg_rate:+.2f}%"
                item.setForeground(QColor("#EF4444"))
            elif idx.get("direction") == "down":
                text = f"📉 {name}  {chg_rate:+.2f}%"
                item.setForeground(QColor("#22C55E"))
            else:
                text = f"➡️ {name}  {chg_rate:+.2f}%"

            item.setText(text)
            item.setData(Qt.UserRole, idx)
            self.index_list.addItem(item)

    def _update_data_table(self):
        """更新数据表格"""
        self.data_table.setRowCount(len(self.indices_data))
        for row, idx in enumerate(self.indices_data):
            values = [
                idx.get("name", ""),
                f"{idx.get('market_index', 0):.2f}",
                f"{idx.get('chg_num', 0):+.2f}",
                f"{idx.get('chg_rate', 0):+.2f}%",
                f"{idx.get('open', 0):.2f}",
                f"{idx.get('high', 0):.2f}",
                f"{idx.get('low', 0):.2f}",
                idx.get("updated_at", "")[:19].replace("T", " "),
            ]
            for col, val in enumerate(values):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignCenter)
                # 涨跌幅颜色
                if col == 3:
                    direction = idx.get("direction", "flat")
                    if direction == "up":
                        item.setForeground(QColor("#EF4444"))
                    elif direction == "down":
                        item.setForeground(QColor("#22C55E"))
                self.data_table.setItem(row, col, item)

    def _update_push_preview(self):
        """更新推送预览文本"""
        threshold = self.threshold_spin.value()
        target_keys = self.config.get("push_strategy", {}).get("target_indices", [])

        filtered = self.processor.filter_by_threshold(self.indices_data, threshold)
        filtered = self.processor.filter_by_name_keys(filtered, target_keys)

        if filtered:
            md = self.processor.format_index_summary_md(filtered)
        else:
            md = "暂无满足推送条件的指数数据。"

        self.preview_text.setPlainText(md)

    # ============================================================
    #  K线图加载
    # ============================================================

    def on_index_selected(self, row: int):
        """左侧列表选中指数时"""
        if row < 0 or row >= len(self.indices_data):
            return

        self.current_selected_index = self.indices_data[row]
        self._load_kline()

    def _load_kline(self):
        """加载当前选中指数的K线图（异步）"""
        if not self.current_selected_index:
            return

        idx_id = self.current_selected_index.get("id")
        kline_type = self.kline_type_combo.currentData()

        # 检查缓存
        cache_key = (idx_id, kline_type)
        if cache_key in self.kline_cache:
            self._render_kline(self.kline_cache[cache_key])
            return

        self.lbl_status.setText(f"加载K线数据... (ID={idx_id})")

        self._kline_thread = FetchDataThread(
            self.client, "get_kline_data",
            sub_id=idx_id, kline_type=kline_type
        )
        self._kline_thread.finished.connect(self._on_kline_loaded)
        self._kline_thread.error.connect(self._on_kline_error)
        self._kline_thread.start()

    def _on_kline_loaded(self, result):
        """K线数据加载完成"""
        if not self.current_selected_index:
            return

        idx_id = self.current_selected_index.get("id")
        kline_type = self.kline_type_combo.currentData()

        df = self.processor.parse_kline_data(result)
        self.kline_cache[(idx_id, kline_type)] = df
        self._render_kline(df)

    def _render_kline(self, df: pd.DataFrame):
        """渲染K线图到画布"""
        if not self.current_selected_index or df.empty:
            self.kline_canvas.clear_chart()
            return

        name = self.current_selected_index.get("name", "")
        tail_days = self.kline_days_spin.value()
        show_ma = self.ma_check.isChecked()
        show_vol = self.volume_check.isChecked()
        kline_type = self.kline_type_combo.currentData()

        type_label_map = {"1day": "日线", "1week": "周线", "1month": "月线"}
        type_label = type_label_map.get(kline_type, kline_type)
        title = f"{name} - {type_label}K线 (近{tail_days}天)"

        # 使用 ChartDrawer 生成图片
        img_bytes = self.drawer.draw_kline(
            df, title=title,
            ma_periods=[5, 10, 20] if show_ma else [],
            volume=show_vol,
            tail_days=tail_days,
        )

        self.kline_canvas.update_chart(img_bytes)
        self.lbl_status.setText(f"K线图已更新: {name}")

    def _on_kline_error(self, error_msg: str):
        self.kline_canvas.clear_chart()
        self.lbl_status.setText(f"K线加载失败: {error_msg}")

    def on_kline_type_changed(self):
        """K线周期变更"""
        self.kline_cache.clear()
        self._load_kline()

    def on_kline_setting_changed(self):
        """K线设置变更"""
        self._render_kline(
            self.kline_cache.get(
                (self.current_selected_index.get("id"), self.kline_type_combo.currentData()),
                pd.DataFrame()
            )
        )

    # ============================================================
    #  推送功能
    # ============================================================

    def push_all_indices(self):
        """推送所有指数"""
        threshold = self.threshold_spin.value()
        target_keys = self.config.get("push_strategy", {}).get("target_indices", [])

        filtered = self.processor.filter_by_threshold(self.indices_data, threshold)
        filtered = self.processor.filter_by_name_keys(filtered, target_keys)

        if not filtered:
            QMessageBox.information(self, "提示", "没有满足推送条件的指数。")
            return

        self._do_push(filtered)

    def push_selected_index(self):
        """推送当前选中的指数"""
        if not self.current_selected_index:
            QMessageBox.information(self, "提示", "请先选择一个指数。")
            return

        self._do_push([self.current_selected_index])

    def _do_push(self, indices: List[Dict[str, Any]]):
        """执行推送"""
        reply = QMessageBox.question(
            self, "确认推送",
            f"即将推送 {len(indices)} 个指数数据到企业微信，是否继续？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self.lbl_status.setText("正在推送...")

        # 生成Markdown文本
        md_content = self.processor.format_index_summary_md(indices)

        # 生成K线图
        kline_images = []
        kline_type = self.kline_type_combo.currentData()
        kline_days = self.config.get("push_strategy", {}).get("kline_days", 30)
        show_ma = self.config.get("push_strategy", {}).get("include_ma", True)

        for idx in indices:
            idx_id = idx.get("id")
            # 尝试从缓存获取，否则同步获取
            cache_key = (idx_id, kline_type)
            df = self.kline_cache.get(cache_key)
            if df is None or df.empty:
                raw = self.client.get_kline_data(idx_id, kline_type)
                df = self.processor.parse_kline_data(raw)
                self.kline_cache[cache_key] = df

            if not df.empty:
                name = idx.get("name", "")
                type_label_map = {"1day": "日线", "1week": "周线", "1month": "月线"}
                type_label = type_label_map.get(kline_type, kline_type)
                img = self.drawer.draw_kline(
                    df,
                    title=f"{name} - {type_label}K线",
                    ma_periods=[5, 10, 20] if show_ma else [],
                    volume=True,
                    tail_days=kline_days,
                )
                kline_images.append(img)

        # 后台推送
        self._push_thread = PushDataThread(self.pusher, md_content, kline_images)
        self._push_thread.finished.connect(self._on_push_finished)
        self._push_thread.error.connect(self._on_push_error)
        self._push_thread.start()

    def _on_push_finished(self, result: Dict[str, bool]):
        """推送完成回调"""
        text_ok = result.get("text", False)
        img_ok = result.get("images", [])

        total = 1 + len(img_ok)
        success = (1 if text_ok else 0) + sum(1 for x in img_ok if x)

        if success == total:
            self.lbl_status.setText(f"推送成功！({total}条消息)")
            QMessageBox.information(self, "推送成功", f"成功推送 {total} 条消息。")
        else:
            self.lbl_status.setText(f"推送部分成功: {success}/{total}")
            QMessageBox.warning(self, "推送结果", f"成功 {success}/{total} 条消息。")

    def _on_push_error(self, error_msg: str):
        self.lbl_status.setText("推送失败")
        QMessageBox.critical(self, "推送失败", f"推送过程中发生错误:\n{error_msg}")

    # ============================================================
    #  导出功能
    # ============================================================

    def export_kline_image(self):
        """导出当前K线图为PNG文件"""
        if not self.current_selected_index:
            QMessageBox.information(self, "提示", "请先选择一个指数。")
            return

        filepath, _ = QFileDialog.getSaveFileName(
            self, "保存K线图", f"{self.current_selected_index.get('name', 'kline')}.png",
            "PNG图片 (*.png);;所有文件 (*)",
        )
        if not filepath:
            return

        idx_id = self.current_selected_index.get("id")
        kline_type = self.kline_type_combo.currentData()
        df = self.kline_cache.get((idx_id, kline_type))
        if df is None or df.empty:
            QMessageBox.warning(self, "提示", "暂无K线数据可导出。")
            return

        saved = self.drawer.draw_kline_to_file(
            df, filepath,
            title=self.current_selected_index.get("name", ""),
            tail_days=self.kline_days_spin.value(),
        )
        self.lbl_status.setText(f"K线图已导出: {saved}")
        QMessageBox.information(self, "导出成功", f"K线图已保存至:\n{saved}")

    # ============================================================
    #  自动刷新
    # ============================================================

    def toggle_auto_refresh(self, checked: bool):
        """切换自动刷新"""
        interval = self.config.get("ui", {}).get("refresh_interval", 60)
        if checked:
            self.refresh_timer.start(interval * 1000)
            self.act_auto_refresh.setText(f"自动刷新: 开 ({interval}s)")
            self.lbl_countdown.setText(f"每{interval}秒刷新")
            logger.info("自动刷新已开启，间隔 %d 秒", interval)
        else:
            self.refresh_timer.stop()
            self.act_auto_refresh.setText("自动刷新: 关")
            self.lbl_countdown.setText("")
            logger.info("自动刷新已关闭")

    def auto_refresh(self):
        """自动刷新回调"""
        self.refresh_all_data()

    # ============================================================
    #  关闭事件
    # ============================================================

    def closeEvent(self, event):
        """窗口关闭事件"""
        self.refresh_timer.stop()
        event.accept()
