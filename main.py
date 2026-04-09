# -*- coding: utf-8 -*-
"""
CS饰品指数分析工具 - 主入口

使用方法:
  1. pip install -r requirements.txt
  2. python main.py                    # 启动GUI界面
  3. python main.py --push             # 仅执行推送（不启动GUI）
  4. python main.py --push --only NAME  # 推送指定指数（按name_key）

项目结构:
  cs_index_analyzer/
  ├── main.py                  # 主入口（本文件）
  ├── config/
  │   └── config.yaml          # 配置文件
  ├── modules/
  │   ├── __init__.py          # 模块包初始化
  │   ├── api_client.py        # CSQAQ API客户端
  │   ├── data_processor.py    # 数据解析与处理
  │   ├── chart_drawer.py      # K线图绑制
  │   ├── wecom_pusher.py      # 企业微信推送
  │   └── ui_main.py           # PyQt5 GUI界面
  ├── requirements.txt         # Python依赖
  └── .vscode/
      ├── settings.json        # VSCode设置
      └── launch.json          # VSCode调试配置
"""

import argparse
import logging
import os
import sys
import yaml
from logging.handlers import RotatingFileHandler
from typing import Dict, Any
from PyQt5.QtCore import Qt

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def setup_logging(log_config: Dict[str, Any]):
    """配置日志"""
    level_str = log_config.get("level", "INFO")
    level = getattr(logging, level_str.upper(), logging.INFO)

    fmt = log_config.get("format", "%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    formatter = logging.Formatter(fmt)

    # 控制台
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(formatter)

    # 文件
    handlers = [console]
    log_file = log_config.get("file", "logs/app.log")
    if log_file:
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        fh = RotatingFileHandler(
            log_file,
            maxBytes=log_config.get("max_size", 10 * 1024 * 1024),
            backupCount=log_config.get("backup_count", 5),
            encoding="utf-8",
        )
        fh.setLevel(level)
        fh.setFormatter(formatter)
        handlers.append(fh)

    # 避免重复添加handler
    if not logging.getLogger().handlers:
        logging.basicConfig(level=level, handlers=handlers)
    else:
        for handler in handlers:
            logging.getLogger().addHandler(handler)
            logging.getLogger().setLevel(level)


def load_config(config_path: str) -> Dict[str, Any]:
    """加载配置文件"""
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"配置文件不存在: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    return config


def create_components(config: Dict[str, Any]):
    """创建各功能组件（工厂方法，方便后续替换实现）"""
    from modules import CSQAQClient, DataProcessor, ChartDrawer, WeComPusher

    api_cfg = config["api"]
    client = CSQAQClient(
        base_url=api_cfg["base_url"],
        api_token=api_cfg["api_token"],
        timeout=api_cfg.get("timeout", 30),
        retry_times=api_cfg.get("retry_times", 3),
        retry_delay=api_cfg.get("retry_delay", 2),
    )

    processor = DataProcessor()

    drawer = ChartDrawer(
        style_config=config.get("ui", {}).get("chart_style", {}),
    )

    wecom_cfg = config.get("wecom", {})
    pusher = WeComPusher(
        webhook_url=wecom_cfg["webhook_url"],
        msg_type=wecom_cfg.get("msg_type", "markdown"),
        image_quality=wecom_cfg.get("image_quality", 85),
    )

    return client, processor, drawer, pusher


# ============================================================
#  CLI推送模式（无GUI）
# ============================================================

def run_push_mode(config: Dict[str, Any], only_name_key: str = None):
    """
    命令行推送模式 - 适用于定时任务/cron

    Args:
        config: 配置字典
        only_name_key: 仅推送指定name_key的指数（None=全部）
    """
    logger = logging.getLogger(__name__)
    client, processor, drawer, pusher = create_components(config)

    logger.info("=" * 50)
    logger.info("开始执行CLI推送模式")
    logger.info("=" * 50)

    # 1. 获取首页数据
    logger.info("正在获取首页数据...")
    resp = client.get_current_data()
    indices = processor.parse_current_data(resp)

    if not indices:
        logger.error("未获取到任何指数数据，退出")
        return

    # 2. 过滤
    strategy = config.get("push_strategy", {})
    threshold = strategy.get("alert_threshold", 0)
    target_keys = strategy.get("target_indices", [])

    filtered = processor.filter_by_threshold(indices, threshold)
    filtered = processor.filter_by_name_keys(filtered, target_keys)

    # 指定单个指数
    if only_name_key:
        filtered = [idx for idx in filtered if idx.get("name_key") == only_name_key]
        if not filtered:
            logger.error("未找到 name_key='%s' 的指数", only_name_key)
            return

    logger.info("将推送 %d 个指数", len(filtered))

    # 3. 生成Markdown文本
    md_content = processor.format_index_summary_md(filtered)

    # 4. 生成K线图
    kline_images = []
    kline_type = config.get("push_strategy", {}).get("kline_days", 30)
    push_kline_type = "1day"

    for idx in filtered:
        idx_id = idx.get("id")
        logger.info("获取 %s (ID=%d) 的K线数据...", idx.get("name"), idx_id)

        raw = client.get_kline_data(idx_id, push_kline_type)
        df = processor.parse_kline_data(raw)

        if not df.empty:
            name = idx.get("name", "")
            img = drawer.draw_kline(
                df,
                title=f"{name} K线",
                ma_periods=[5, 10, 20],
                volume=True,
                tail_days=kline_type,
            )
            kline_images.append(img)

    # 5. 推送
    logger.info("开始推送至企业微信...")
    result = pusher.push_index_report(md_content, kline_images)

    text_ok = result.get("text", False)
    img_results = result.get("images", [])
    total = 1 + len(img_results)
    success = (1 if text_ok else 0) + sum(1 for x in img_results if x)

    if success == total:
        logger.info("推送完成！全部成功 (%d/%d)", success, total)
    else:
        logger.warning("推送完成: 部分成功 (%d/%d)", success, total)


# ============================================================
#  GUI模式
# ============================================================

def run_gui_mode(config: Dict[str, Any]):
    """启动GUI界面"""
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QApplication
    from modules.ui_main import MainWindow

    # 高DPI支持 - 使用环境变量方式（PyQt5 5.6+推荐）
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # 应用深色/浅色主题
    theme = config.get("ui", {}).get("theme", "default")
    if theme == "dark":
        from PyQt5.QtGui import QPalette, QColor
        palette = QPalette()
        palette.setColor(QPalette.Window, QColor(30, 30, 30))
        palette.setColor(QPalette.WindowText, QColor(220, 220, 220))
        palette.setColor(QPalette.Base, QColor(25, 25, 25))
        palette.setColor(QPalette.AlternateBase, QColor(35, 35, 35))
        palette.setColor(QPalette.ToolTipBase, QColor(25, 25, 25))
        palette.setColor(QPalette.ToolTipText, QColor(220, 220, 220))
        palette.setColor(QPalette.Text, QColor(220, 220, 220))
        palette.setColor(QPalette.Button, QColor(45, 45, 45))
        palette.setColor(QPalette.ButtonText, QColor(220, 220, 220))
        palette.setColor(QPalette.BrightText, QColor(255, 0, 0))
        palette.setColor(QPalette.Link, QColor(42, 130, 218))
        palette.setColor(QPalette.Highlight, QColor(42, 130, 218))
        palette.setColor(QPalette.HighlightedText, QColor(0, 0, 0))
        app.setPalette(palette)

    # 创建组件
    client, processor, drawer, pusher = create_components(config)

    # 创建主窗口
    window = MainWindow(config, client, processor, drawer, pusher)
    window.show()

    sys.exit(app.exec())


# ============================================================
#  入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="CS饰品指数分析工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py                  # 启动GUI界面
  python main.py --push           # CLI模式：推送全部指数
  python main.py --push --only init   # CLI模式：仅推送饰品指数
        """,
    )
    parser.add_argument(
        "--config", "-c",
        default=os.path.join(PROJECT_ROOT, "config", "config.yaml"),
        help="配置文件路径 (默认: config/config.yaml)",
    )
    parser.add_argument(
        "--push", "-p",
        action="store_true",
        help="CLI推送模式（不启动GUI）",
    )
    parser.add_argument(
        "--only", "-o",
        metavar="NAME_KEY",
        help="仅推送指定name_key的指数（需配合 --push 使用）",
    )

    args = parser.parse_args()

    # 加载配置
    config = load_config(args.config)

    # 配置日志
    setup_logging(config.get("logging", {}))
    logger = logging.getLogger(__name__)
    logger.info("CS饰品指数分析工具启动")
    logger.info("配置文件: %s", args.config)

    # 执行对应模式
    if args.push:
        logger.info("运行模式: CLI推送")
        run_push_mode(config, only_name_key=args.only)
    else:
        logger.info("运行模式: GUI界面")
        run_gui_mode(config)


if __name__ == "__main__":
    main()
