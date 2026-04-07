# -*- coding: utf-8 -*-
"""
企业微信推送模块 - 负责通过Webhook推送消息和图片

核心功能:
  1. 发送 Markdown 格式文本消息
  2. 发送图片消息（K线图）
  3. 组合发送：先发文本摘要 + 再发K线图

企业微信Webhook文档:
  - 文本消息: {"msgtype":"text", "text":{"content":"xxx"}}
  - Markdown消息: {"msgtype":"markdown", "markdown":{"content":"xxx"}}
  - 图片消息: {"msgtype":"image", "image":{"base64":"xxx", "md5":"xxx"}}

注意: 企业微信Webhook每条消息有大小限制（图片最大2MB），需要做好压缩处理。
"""

import base64
import hashlib
import logging
import os
import tempfile
from typing import Optional, List, Dict, Any

import requests
from PIL import Image

logger = logging.getLogger(__name__)


class WeComPusher:
    """企业微信Webhook推送器"""

    # 企业微信图片大小限制 (2MB)
    MAX_IMAGE_SIZE = 2 * 1024 * 1024

    def __init__(self, webhook_url: str, msg_type: str = "markdown",
                 image_quality: int = 85):
        """
        初始化推送器

        Args:
            webhook_url: 企业微信机器人Webhook地址
            msg_type: 默认消息类型 ("markdown" / "text")
            image_quality: 图片JPEG压缩质量(1-100)
        """
        self.webhook_url = webhook_url
        self.msg_type = msg_type
        self.image_quality = image_quality

    # ============================================================
    #  文本消息推送
    # ============================================================

    def send_text(self, content: str, mentioned_list: Optional[List[str]] = None) -> bool:
        """
        发送文本消息

        Args:
            content: 文本内容
            mentioned_list: @指定人列表（userId 或 "@all"）

        Returns:
            是否发送成功
        """
        payload: Dict[str, Any] = {
            "msgtype": "text",
            "text": {
                "content": content,
            }
        }
        if mentioned_list:
            payload["text"]["mentioned_list"] = mentioned_list

        return self._post(payload)

    def send_markdown(self, content: str) -> bool:
        """
        发送Markdown消息（推荐使用）

        Args:
            content: Markdown格式文本

        Returns:
            是否发送成功
        """
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "content": content,
            }
        }
        return self._post(payload)

    # ============================================================
    #  图片消息推送
    # ============================================================

    def send_image_bytes(self, image_bytes: bytes, quality: Optional[int] = None) -> bool:
        """
        发送图片消息（从字节流）

        Args:
            image_bytes: 图片原始字节流
            quality: 压缩质量（None使用默认值）

        Returns:
            是否发送成功
        """
        quality = quality or self.image_quality
        compressed = self._compress_image(image_bytes, quality)

        b64 = base64.b64encode(compressed).decode("utf-8")
        md5 = hashlib.md5(compressed).hexdigest()

        payload = {
            "msgtype": "image",
            "image": {
                "base64": b64,
                "md5": md5,
            }
        }
        return self._post(payload)

    def send_image_file(self, filepath: str, quality: Optional[int] = None) -> bool:
        """
        发送图片消息（从文件路径）

        Args:
            filepath: 图片文件路径
            quality: 压缩质量

        Returns:
            是否发送成功
        """
        with open(filepath, "rb") as f:
            image_bytes = f.read()
        return self.send_image_bytes(image_bytes, quality)

    # ============================================================
    #  组合推送（指数数据 + K线图）
    # ============================================================

    def push_index_report(self, md_content: str,
                          kline_images: Optional[List[bytes]] = None) -> Dict[str, bool]:
        """
        推送完整的指数报告（Markdown文本 + K线图）

        Args:
            md_content: Markdown格式的指数概览文本
            kline_images: K线图片字节流列表（每张对应一个子指数）

        Returns:
            {"text": True/False, "images": [True/False, ...]}
        """
        results = {"text": False, "images": []}

        # 1. 发送文本摘要
        logger.info("发送指数概览文本...")
        results["text"] = self.send_markdown(md_content)

        # 2. 逐个发送K线图
        if kline_images:
            for i, img_bytes in enumerate(kline_images):
                logger.info("发送第 %d/%d 张K线图...", i + 1, len(kline_images))
                ok = self.send_image_bytes(img_bytes)
                results["images"].append(ok)

        return results

    # ============================================================
    #  内部方法
    # ============================================================

    def _post(self, payload: Dict[str, Any]) -> bool:
        """
        发送HTTP POST请求到Webhook

        Args:
            payload: 消息体

        Returns:
            是否发送成功
        """
        try:
            resp = requests.post(
                self.webhook_url,
                json=payload,
                timeout=15,
            )
            resp.raise_for_status()
            result = resp.json()

            if result.get("errcode") == 0:
                logger.info("消息推送成功 (type=%s)", payload.get("msgtype"))
                return True
            else:
                logger.error("消息推送失败: errcode=%s, errmsg=%s",
                             result.get("errcode"), result.get("errmsg"))
                return False

        except requests.exceptions.Timeout:
            logger.error("Webhook请求超时")
            return False
        except requests.exceptions.ConnectionError as e:
            logger.error("Webhook连接失败: %s", e)
            return False
        except Exception as e:
            logger.error("消息推送异常: %s", e, exc_info=True)
            return False

    def _compress_image(self, image_bytes: bytes, quality: int) -> bytes:
        """
        压缩图片以满足企业微信大小限制

        策略:
          1. 如果原始图片已小于限制，直接返回
          2. 如果超过限制，逐步降低质量直到满足要求
          3. 如果仍超限，缩小尺寸

        Args:
            image_bytes: 原始图片字节
            quality: 初始JPEG质量

        Returns:
            压缩后的图片字节
        """
        # 未超限直接返回
        if len(image_bytes) <= self.MAX_IMAGE_SIZE:
            return image_bytes

        logger.info("图片大小 %d bytes 超过限制，开始压缩...", len(image_bytes))

        try:
            img = Image.open(io.BytesIO(image_bytes))

            # 转换为RGB（处理RGBA/P模式）
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")

            buf = io.BytesIO()
            current_quality = quality

            # 逐步降低质量
            while current_quality >= 20:
                buf.seek(0)
                buf.truncate()
                img.save(buf, format="JPEG", quality=current_quality, optimize=True)
                result = buf.getvalue()

                if len(result) <= self.MAX_IMAGE_SIZE:
                    logger.info("压缩成功: quality=%d, %d -> %d bytes",
                                current_quality, len(image_bytes), len(result))
                    return result
                current_quality -= 10

            # 缩小尺寸
            scale = 0.8
            while scale >= 0.3:
                new_w = int(img.width * scale)
                new_h = int(img.height * scale)
                resized = img.resize((new_w, new_h), Image.LANCZOS)

                buf.seek(0)
                buf.truncate()
                resized.save(buf, format="JPEG", quality=60, optimize=True)
                result = buf.getvalue()

                if len(result) <= self.MAX_IMAGE_SIZE:
                    logger.info("缩放压缩成功: scale=%.1f, %d -> %d bytes",
                                scale, len(image_bytes), len(result))
                    return result
                scale -= 0.1

            # 最终兜底
            logger.warning("图片压缩后仍超限，使用最低质量版本")
            return buf.getvalue()

        except Exception as e:
            logger.error("图片压缩失败: %s，返回原始数据", e)
            return image_bytes


# 需要导入 io
import io
