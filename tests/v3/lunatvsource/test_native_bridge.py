from pathlib import Path

from app.plugins.lunatvsource import LunaTVSource
from app.plugins.lunatvsource.cms import AppleCmsClient, CmsSource, _result_from_item
from app.plugins.lunatvsource.downloader import DownloadQueue
import app.plugins.lunatvsource as plugin_module
import app.plugins.lunatvsource.downloader as downloader_module


def test_discover_accepts_native_keyword_and_stops_after_first_source(monkeypatch):
    calls = []

    class Client:
        def search(self, query, **kwargs):
            calls.append((query, kwargs))
            return []

    plugin = object.__new__(LunaTVSource)
    plugin._enabled = True
    plugin._ai = type("Ai", (), {"normalize": lambda self, query, *args: (query, {})})()
    plugin._logger = type("Logger", (), {"warning": lambda *args: None})()
    monkeypatch.setattr(plugin, "_client", lambda: Client())
    assert plugin.api_discover(keyword="示例电影") == {"success": True, "data": []}
    assert calls == [("示例电影", {"limit": 30, "stop_after_first_source": True})]


def test_discover_source_declares_native_search_field(monkeypatch):
    class DiscoverMediaSource:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    schemas = type("Schemas", (), {"DiscoverMediaSource": DiscoverMediaSource})
    monkeypatch.setattr(plugin_module, "_schemas", schemas)
    monkeypatch.setattr(plugin_module, "_HostMediaSource", type("MediaSource", (), {}))
    plugin = object.__new__(LunaTVSource)
    plugin._enabled = True
    plugin._logger = type("Logger", (), {"debug": lambda *args: None})()
    monkeypatch.setattr(plugin, "_host_media_source", lambda: "lunatv")
    event_data = type("EventData", (), {"extra_sources": []})()
    plugin._discover_source(type("Event", (), {"event_data": event_data})())
    source = event_data.extra_sources[0]
    assert source.filter_params == {"keyword": ""}
    assert source.filter_ui[0]["props"]["model"] == "keyword"


def test_search_can_stop_after_first_source_with_results():
    client = AppleCmsClient([
        CmsSource(key="first", name="首选", api="https://first.example/vod"),
        CmsSource(key="second", name="备用", api="https://second.example/vod"),
    ])
    called = []

    def fake_request(source, **params):
        called.append(source.key)
        return {"list": [{
            "vod_id": source.key,
            "vod_name": "示例电影",
            "type_name": "电影",
            "vod_play_from": "在线播放",
            "vod_play_url": "正片$https://example.test/movie.m3u8",
        }]}

    client._request = fake_request
    assert [item.source_key for item in client.search(
        "示例电影", stop_after_first_source=True
    )] == ["first"]
    assert set(called) == {"first"}


def test_ffmpeg_explicitly_sets_mp4_muxer_for_part_file(monkeypatch, tmp_path: Path):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return type("Completed", (), {"returncode": 0, "stderr": "", "stdout": ""})()

    monkeypatch.setattr(downloader_module.subprocess, "run", fake_run)
    DownloadQueue._run_ffmpeg(
        "ffmpeg", "https://example.test/video.m3u8", tmp_path / "movie.mp4.part"
    )
    command = captured["command"]
    assert command[command.index("-f") + 1] == "mp4"


def test_native_resource_search_returns_marked_download_items(monkeypatch):
    class TorrentInfo:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class Client:
        def search(self, query, **kwargs):
            return [_result_from_item(
                CmsSource("demo", "演示源", "https://cms.example/vod", "https://cms.example"),
                {
                    "vod_id": "42",
                    "vod_name": "示例剧",
                    "vod_year": "2024",
                    "type_name": "电视剧",
                    "vod_play_from": "在线播放",
                    "vod_play_url": "01$https://example.test/01.m3u8",
                },
            )]

    monkeypatch.setattr(plugin_module, "_schemas", type("Schemas", (), {"TorrentInfo": TorrentInfo}))
    plugin = object.__new__(LunaTVSource)
    plugin._enabled = True
    plugin._ai = type("Ai", (), {"normalize": lambda self, query, *args: (query, {})})()
    plugin._logger = type("Logger", (), {"warning": lambda *args: None})()
    plugin._resource_search_lock = __import__("threading").RLock()
    plugin._resource_search_cache = {}
    monkeypatch.setattr(plugin, "_client", lambda: Client())
    monkeypatch.setattr(plugin, "_host_media_source", lambda: "lunatv")
    items = plugin.search_torrents(site={"id": 1}, keyword="示例剧", page=0)
    assert len(items) == 1
    assert items[0].site_name == "LunaTV"
    assert items[0].title.endswith("S01E01")
    assert plugin._decode_resource_token(items[0].enclosure)["url"].endswith("01.m3u8")


def test_native_download_is_enqueued_into_serial_queue(tmp_path: Path):
    data = {}
    plugin = object.__new__(LunaTVSource)
    plugin._config = {}
    plugin._queue = DownloadQueue(data.get, data.__setitem__, lambda *_: None)
    token = plugin._resource_token({
        "url": "https://example.test/movie.m3u8",
        "title": "示例电影",
        "year": "2024",
        "media_type": "movie",
        "season": 1,
        "episode": 1,
        "media_id": "demo:42",
    })
    result = plugin.download(token, tmp_path)
    assert result[0] == "LunaTVSource"
    assert result[1]
    tasks = plugin._queue.list_tasks()
    assert len(tasks) == 1
    assert tasks[0]["url"] == "https://example.test/movie.m3u8"
