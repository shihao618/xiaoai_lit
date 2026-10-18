import json

import pytest

from xiaoai import constants
from xiaoai.credentials import Credentials
from xiaoai.exceptions import (
    CommandRejectedError,
    CredentialRejectedError,
    ResponseFormatError,
)
from xiaoai.mina import MiNAClient, parse_play_status


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
        "address": "1.2.3.4"}
    assert devices[1].to_api_dict()["name"] == "台灯"
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


def test_play_url_defaults_to_play_music():
    captured = {}

    def handler(call):
        captured.update(call)
        return {"code": 0, "data": {"code": 0}}, None

    http = StubHTTP(handler=handler)
    MiNAClient(http).play_url(make_cred(), "dev1", "https://example.com/a.mp3")
    form = captured["form"]
    assert form["method"] == "player_play_music"
    assert form["path"] == "mediaplayer"
    message = json.loads(form["message"])
    assert message["startaudioid"] == "1582971365183456177"
    music = json.loads(message["music"])
    assert music["play_behavior"] == "REPLACE_ALL"
    item = music["payload"]["audio_items"][0]
    assert item["stream"]["url"] == "https://example.com/a.mp3"


def test_play_url_play_music_for_l05b():
    captured = {}

    def handler(call):
        captured.update(call)
        return {"code": 0, "data": {"code": 0}}, None

    http = StubHTTP(handler=handler)
    MiNAClient(http).play_url(make_cred(), "dev1", "https://example.com/a.mp3",
                              hardware="L05B")
    assert captured["form"]["method"] == "player_play_music"


def test_play_url_play_music_with_tail_two_items():
    captured = {}

    def handler(call):
        captured.update(call)
        return {"code": 0, "data": {"code": 0}}, None

    http = StubHTTP(handler=handler)
    MiNAClient(http).play_url(make_cred(), "dev1", "https://example.com/a.mp3",
                              hardware="L05B",
                              tail_url="https://example.com/silence.mp3")
    form = captured["form"]
    assert form["method"] == "player_play_music"
    music = json.loads(json.loads(form["message"])["music"])
    items = music["payload"]["audio_items"]
    assert len(items) == 2
    assert items[0]["stream"]["url"] == "https://example.com/a.mp3"
    assert items[1]["stream"]["url"] == "https://example.com/silence.mp3"


def test_play_url_legacy_for_other_hardware():
    captured = {}

    def handler(call):
        captured.update(call)
        return {"code": 0, "data": {"code": 0}}, None

    http = StubHTTP(handler=handler)
    MiNAClient(http).play_url(make_cred(), "dev1", "https://example.com/a.mp3",
                              hardware="LX06")
    form = captured["form"]
    assert form["method"] == "player_play_url"
    assert json.loads(form["message"]) == {
        "media": "app_ios", "url": "https://example.com/a.mp3", "type": 2}


def test_stop_playback_assembly():
    captured = {}

    def handler(call):
        captured.update(call)
        return {"code": 0, "data": {"code": 0}}, None

    http = StubHTTP(handler=handler)
    MiNAClient(http).stop_playback(make_cred(), "dev1")
    form = captured["form"]
    assert form["method"] == "player_play_operation"
    assert form["path"] == "mediaplayer"
    assert json.loads(form["message"]) == {"action": "pause", "media": "app_ios"}


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