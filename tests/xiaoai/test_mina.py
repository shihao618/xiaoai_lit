import json

import pytest

from lit.xiaoai import constants
from lit.xiaoai.credentials import Credentials
from lit.xiaoai.exceptions import (
    CommandRejectedError,
    CredentialRejectedError,
    ResponseFormatError,
)
from lit.xiaoai.mina import (
    MiNAClient,
    SpeakerDevice,
    is_speaker_hardware,
    parse_play_status,
)


class StubHTTP:
    def __init__(self, handler=None):
        self.calls = []
        self.handler = handler

    def request(self, method, url, *, params=None, form=None, cookies=None,
                timeout=None, session=None, parse_json=True, headers=None):
        call = {"method": method, "url": url, "params": params, "form": form,
                "cookies": cookies, "timeout": timeout, "headers": headers}
        self.calls.append(call)
        if self.handler:
            return self.handler(call)
        return {}, None


def make_cred():
    return Credentials(user_id="42", pass_token="pt", device_id="dev",
                       service_token="st")


def test_device_list_parsing_and_api_dict():
    payload = {"code": 0, "data": [
        {"deviceID": "d1", "name": "小爱音箱", "hardware": "LX06",
         "address": "1.2.3.4"},
        {"deviceID": "d2", "name": "台灯", "hardware": "MJCTD01",
         "address": "1.2.3.5"},
    ]}
    http = StubHTTP(handler=lambda c: (payload, None))
    devices = MiNAClient(http).device_list(make_cred())
    assert devices[0].to_api_dict() == {
        "deviceID": "d1", "name": "小爱音箱", "hardware": "LX06",
        "address": "1.2.3.4", "is_speaker": True}
    assert devices[1].to_api_dict()["name"] == "台灯"
    assert devices[1].to_api_dict()["is_speaker"] is False
    call = http.calls[0]
    assert call["url"] == constants.MINA_BASE_URL + "/admin/v2/device_list"
    assert call["params"]["master"] == "0"
    assert call["params"]["requestId"].startswith("app_ios_")


def test_device_list_empty_ok():
    http = StubHTTP(handler=lambda c: ({"code": 0, "data": []}, None))
    assert MiNAClient(http).device_list(make_cred()) == []


def test_device_list_bad_format():
    http = StubHTTP(handler=lambda c: ({"code": 0}, None))
    with pytest.raises(ResponseFormatError):
        MiNAClient(http).device_list(make_cred())


def test_missing_credentials_rejected():
    http = StubHTTP()
    with pytest.raises(CredentialRejectedError):
        MiNAClient(http).device_list(None)
    with pytest.raises(CredentialRejectedError):
        MiNAClient(http).device_list(Credentials(user_id="42", service_token=""))


def test_request_id_format():
    http = StubHTTP(handler=lambda c: ({"code": 0, "data": []}, None))
    MiNAClient(http).device_list(make_cred())
    request_id = http.calls[0]["params"]["requestId"]
    assert request_id.startswith("app_ios_")
    assert len(request_id) == len("app_ios_") + 30
    assert request_id.isalnum() or request_id.replace("_", "a").isalnum()


def test_tts_command_assembly():
    captured = {}

    def handler(call):
        captured.update(call)
        return {"code": 0, "data": {"code": 0}}, None

    http = StubHTTP(handler=handler)
    MiNAClient(http).text_to_speech(make_cred(), "dev1", "你好，上课了")
    assert captured["url"] == constants.MINA_BASE_URL + "/remote/ubus"
    form = captured["form"]
    assert form["method"] == "text_to_speech"
    assert form["path"] == "mibrain"
    assert json.loads(form["message"]) == {"text": "你好，上课了"}
    assert form["deviceId"] == "dev1"
    assert form["requestId"].startswith("app_ios_")
    assert captured["cookies"] == {"userId": "42", "serviceToken": "st"}
    assert captured["timeout"] == constants.MINA_TIMEOUT
    assert captured["headers"] == {"User-Agent": constants.MINA_USER_AGENT}

















def test_inner_code_rejected():
    http = StubHTTP(handler=lambda c: ({"code": 0, "data": {"code": -500}}, None))
    with pytest.raises(CommandRejectedError):
        MiNAClient(http).text_to_speech(make_cred(), "dev", "x")


def test_top_level_unknown_code_rejected():
    http = StubHTTP(handler=lambda c: ({"code": 1}, None))
    with pytest.raises(CommandRejectedError):
        MiNAClient(http).device_list(make_cred())


# ---------- 播放状态递归解析（移植 speaker_test.go 场景） ----------

def test_parse_status_nested_json_strings():
    inner = json.dumps({"volume": 35, "status": 1})
    mid = json.dumps({"info": inner})
    status = parse_play_status({"data": mid}, 0)
    assert status.volume == 35
    assert status.state == "playing"


def test_parse_status_double_nested_with_string_status():
    inner = json.dumps({"volume_level": "40", "playStatus": 2})
    status = parse_play_status({"data": {"info": inner}}, 0)
    assert status.volume == 40
    assert status.state == "paused"


def test_parse_status_string_state_value():
    status = parse_play_status({"play_status": "PLAYING"}, 0)
    assert status.state == "playing"
    status = parse_play_status({"status": "Paused"}, 0)
    assert status.state == "paused"


def test_parse_status_volume_aliases():
    assert parse_play_status({"volumeLevel": "60"}, 0).volume == 60
    assert parse_play_status({"volume_level": 40}, 0).volume == 40
    assert parse_play_status({"volume": 0}, 0).volume == 0
    assert parse_play_status({"volume": 101}, 0).volume is None
    assert parse_play_status({"volume": "abc"}, 0).volume is None


def test_parse_status_too_deep_terminates_safely():
    deep = "12345"
    for _ in range(10):
        deep = json.dumps({"info": deep})
    status = parse_play_status(deep, 0)
    assert status.volume is None
    assert status.state == ""


def test_parse_status_non_dict_returns_empty():
    status = parse_play_status([1, 2, 3], 0)
    assert status.volume is None


def test_get_play_status_flow():
    payload = {"code": 0, "data": {"code": 0, "info": json.dumps(
        {"volume": 50, "status": 1})}}
    http = StubHTTP(handler=lambda c: (payload, None))
    status = MiNAClient(http).get_play_status(make_cred(), "dev")
    assert status.volume == 50
    assert status.state == "playing"
    form = http.calls[0]["form"]
    assert form["method"] == "player_get_play_status"
    assert form["path"] == "mediaplayer"
    assert form["message"] == "{}"


def test_get_play_status_unrecognized():
    http = StubHTTP(handler=lambda c: ({"code": 0, "data": {}}, None))
    with pytest.raises(ResponseFormatError):
        MiNAClient(http).get_play_status(make_cred(), "dev")

# ---------- N4 音箱身份判定 ----------

def test_is_speaker_hardware_positive_matrix():
    assert is_speaker_hardware("LX06", "") is True
    assert is_speaker_hardware("L05B", None) is True
    assert is_speaker_hardware("XXSPEAKERXX", "") is True  # speaker 大小写不敏感
    assert is_speaker_hardware("XXSoundXX", "") is True
    assert is_speaker_hardware(None, "小爱同学") is True
    assert is_speaker_hardware("", "客厅音箱") is True


def test_is_speaker_hardware_negative_matrix():
    assert is_speaker_hardware("MJCTD01", "台灯") is False
    assert is_speaker_hardware("soundbar", "电视") is False  # Sound 大小写敏感
    assert is_speaker_hardware(None, None) is False
    assert is_speaker_hardware(None, "扫地机器人") is False


def test_to_api_dict_is_speaker_consistent():
    speaker = SpeakerDevice("d1", "小爱音箱", "LX06", "1.1.1.1").to_api_dict()
    lamp = SpeakerDevice("d2", "台灯", "MJCTD01", "2.2.2.2").to_api_dict()
    assert speaker["is_speaker"] is is_speaker_hardware("LX06", "小爱音箱")
    assert speaker["is_speaker"] is True
    assert lamp["is_speaker"] is is_speaker_hardware("MJCTD01", "台灯")
    assert lamp["is_speaker"] is False