"""小爱音箱服务常量（协议参数移植自 MiCast-Go 实测组合）。"""

USER_AGENT = "APP/com.xiaomi.mihome APPV/6.0.103 iosPassportSDK/3.9.0 iOS/14.4 miHSTS"
MINA_USER_AGENT = (
    "MiHome/6.0.103 (com.xiaomi.mihome; build:6.0.103.1; iOS 14.4.0) "
    "Alamofire/6.0.103 MICO/iOSApp/appStore/6.0.103"
)

ACCOUNT_BASE_URL = "https://account.xiaomi.com"
MINA_BASE_URL = "https://api2.mina.mi.com"

# MIoT 原生播报（xiaomiio 服务）：api.io.mi.com 的 miotspec 接口，
# siid=5（Intelligent Speaker）aiid=3 为 Play Text（文字直接播报），
# aiid=4 为 Execute Text Directive（模拟语音指令）。与 MiNA 分属不同服务令牌。
MIOT_BASE_URL = "https://api.io.mi.com/app"
MIOT_USER_AGENT = (
    "iOS-14.4-6.0.103-iPhone12,3--D7744744F7AF32F0544445285880DD63E47D9BE9"
    "-8816080-84A3F44E137B71AE-iPhone"
)
MIOT_PROTOCOL_HEADER = "PROTOCAL-HTTP2"
MIOT_TIMEOUT = 20
MIOT_LOGIN_TIMEOUT = 20
MIOT_SPEAKER_SIID = 5
MIOT_PLAY_TEXT_AIID = 3
MIOT_EXECUTE_TEXT_AIID = 4
MIOT_PLAY_CONTROL_SIID = 3
MIOT_PAUSE_AIID = 3
MIOT_PLAYING_STATE_PIID = 1
MIOT_PLAYING = 1

TRUSTED_DOMAINS = ("xiaomi.com", "mi.com")

QR_START_TIMEOUT = 12
QR_POLL_TIMEOUT = 22
MINA_TIMEOUT = 30

MAX_REDIRECTS = 8
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
JSON_PREFIX = "&&&START&&&"

CODE_CREDENTIAL_EXPIRED = 70016
CODE_CREDENTIAL_REJECTED = 70001
CODE_RATE_LIMITED = 70022

QR_SESSION_TTL_SECONDS = 300
QR_LIMITED_RETRY_DELAY = 15
QR_NETWORK_RETRY_DELAY = 10

SDK_VERSION = "accountsdk-18.8.15"
REQUEST_ID_PREFIX = "app_ios_"
REQUEST_ID_RANDOM_LENGTH = 30

STATE_NO_CREDENTIAL = "no_credential"
STATE_LOGGED_IN = "logged_in"
STATE_PENDING_VERIFY = "pending_verify"
STATE_REJECTED = "rejected"