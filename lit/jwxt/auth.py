"""登录认证原子能力：AES-ECB 密码加密、验证码获取与 OCR 识别（语义等价自根 jwxt.py）。"""
import ddddocr
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

_ocr = None


def _init_ocr():
    global _ocr
    if _ocr is None:
        _ocr = ddddocr.DdddOcr(show_ad=False)
    return _ocr


def _get_captcha_ocr(client):
    image = client.get_captcha_image()
    return _init_ocr().classification(image)


def recognize_captcha(image: bytes) -> str:
    return _init_ocr().classification(image)


def _encrypt_password(password, verifycode):
    key = (verifycode * 4).encode("utf-8")
    cipher = AES.new(key, AES.MODE_ECB)
    encrypted = cipher.encrypt(pad(password.encode("utf-8"), AES.block_size))
    return encrypted.hex()