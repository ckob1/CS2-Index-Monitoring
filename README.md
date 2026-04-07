# CS2-Index-Monitoring
CS Index Monitoring Script Based on Python


                cs_index_analyzer/
                ├── main.py                    # 主入口（GUI模式 / CLI推送模式）
                ├── config/
                │   └── config.yaml            # 所有配置（API、推送、UI样式、日志）
                ├── modules/
                │   ├── __init__.py            # 模块包
                │   ├── api_client.py          # CSQAQ API客户端（缓存/重试/鉴权）
                │   ├── data_processor.py      # 数据解析 / MA均线 / Markdown文本生成
                │   ├── chart_drawer.py        # 专业K线图绑制（mplfinance + 中文字体）
                │   ├── wecom_pusher.py        # 企业微信推送（文本+图片+自动压缩）
                │   └── ui_main.py             # PyQt5 GUI界面
                ├── requirements.txt           # Python依赖清单
                ├── .vscode/
                │   ├── launch.json            # 3种调试配置
                │   └── settings.json          # VSCode Python设置
                └── .gitignore



                    # 1. 安装依赖
                    cd cs_index_analyzer
                    python3 -m venv .venv && source .venv/bin/activate
                    pip install -r requirements.txt

                    # 2. 启动GUI界面
                    python main.py

                    # 3. CLI推送模式（适合cron定时任务）
                    python main.py --push                        # 推送全部指数
                    python main.py --push --only init            # 仅推送饰品指数
                    python main.py --push --only knives          # 仅推送匕首指数