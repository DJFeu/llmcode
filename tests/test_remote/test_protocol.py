from llm_code.remote.server import RemoteServer
from llm_code.remote.client import RemoteClient


def test_server_init():
    server = RemoteServer(host="localhost", port=9999)
    assert server._port == 9999


def test_client_init():
    client = RemoteClient("ws://localhost:9999")
    assert "ws://" in client._url


def test_client_auto_prefix():
    client = RemoteClient("localhost:9999")
    assert client._url == "ws://localhost:9999"
