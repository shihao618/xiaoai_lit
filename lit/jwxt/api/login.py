"""登录 API：验证码获取/OCR、AES-ECB 密码加密、POST /new/login（吸收原 auth.py 全部职责）。

协议零变化迁移；OCR 与 AES 为本模块内部协作者（C8 条文 4）；
session 注入形态，不自建连接。锁定语义解析归 client+lock 协作，
本模块保持纯请求。
"""
import time

import ddddocr
import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

_ocr = None


def _init_ocr():
    global _ocr
    if _ocr is None:
        _ocr = ddddocr.DdddOcr(show_ad=False)
    return _ocr


def get_captcha_image(session: requests.Session, base_url: str) -> bytes:
    """GET /yzm（时间戳防缓存），返回验证码图片字节。"""
    url = f"{base_url}/yzm?d={int(time.time() * 1000)}"
    resp = session.get(url)
    return resp.content


def recognize_captcha(image: bytes) -> str:
    """ddddocr 全局单例识别（既有初始化语义保持）。"""
    return _init_ocr().classification(image)


def encrypt_password(password: str, verifycode: str) -> str:
    """AES-ECB 加密（验证码 ×4 作密钥，语义等价自原 auth.py 零改动）。"""
    key = (verifycode * 4).encode("utf-8")
    cipher = AES.new(key, AES.MODE_ECB)
    encrypted = cipher.encrypt(pad(password.encode("utf-8"), AES.block_size))
    return encrypted.hex()


def do_login(session: requests.Session, base_url: str, account: str,
             encrypted_pwd: str, verifycode: str) -> dict:
    """POST /new/login（data: account/pwd/verifycode；timeout=15）。

    返回原始响应 dict（code/message 由 client 编排消费）；
    网络异常自然上抛。
    """
    resp = session.post(
        f"{base_url}/new/login",
        data={"account": account, "pwd": encrypted_pwd, "verifycode": verifycode},
        timeout=15,
    )
    return resp.json()