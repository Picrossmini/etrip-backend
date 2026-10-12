"""测试夹具：把「假 DeepSeek」和「真实后端」都在测试进程里跑成 HTTP 服务，
再用 requests 走真网络调后端——这才是接口测试，而不是函数调用。
"""

import os
import socket
import threading
import time
from typing import Optional

import pytest
import requests
import uvicorn


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class _Server:
    """在后台线程里跑一个 uvicorn 服务。"""

    def __init__(self, app, port: int, ready_path: str):
        config = uvicorn.Config(
            app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"
        )
        self.server = uvicorn.Server(config)
        self.port = port
        self.ready_path = ready_path
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self, timeout: float = 15.0) -> None:
        self.thread.start()
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self.thread.is_alive():
                raise RuntimeError("server thread exited early")
            try:
                requests.get(
                    f"http://127.0.0.1:{self.port}{self.ready_path}", timeout=0.5
                )
                return
            except requests.RequestException:
                time.sleep(0.05)
        raise RuntimeError(f"server on port {self.port} did not become ready")

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)


class ApiClient:
    """测试用的后端客户端。"""

    def __init__(self, base_url: str, token: str, stub, stub_port: int):
        self.base_url = base_url
        self.token = token
        self.stub = stub
        self.stub_port = stub_port

    def set_mode(self, mode: str) -> None:
        self.stub.state["mode"] = mode

    @property
    def upstream_calls(self) -> int:
        return self.stub.state["calls"]

    def post(
        self,
        body,
        *,
        token: Optional[str] = "test-token",
        idempotency_key: Optional[str] = None,
        path: str = "/api/itinerary",
    ):
        headers = {}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        return requests.post(
            f"{self.base_url}{path}", json=body, headers=headers, timeout=15
        )

    def get(self, path: str):
        return requests.get(f"{self.base_url}{path}", timeout=15)


@pytest.fixture(scope="session")
def stub_app():
    from tests import stub_deepseek

    stub_deepseek.reset()
    port = _free_port()
    server = _Server(stub_deepseek.app, port, "/healthz")
    server.start()
    yield stub_deepseek, port
    server.stop()


@pytest.fixture(scope="session")
def api(stub_app):
    stub, stub_port = stub_app

    # 把后端指向假 DeepSeek。必须在 import app.main 之前设好。
    os.environ["DEEPSEEK_BASE_URL"] = f"http://127.0.0.1:{stub_port}/v1"
    os.environ["DEEPSEEK_API_KEY"] = "test-key"
    os.environ["DEEPSEEK_MODEL"] = "deepseek-chat"
    os.environ["DEEPSEEK_TIMEOUT"] = "1"
    os.environ["ALLOWED_TOKENS"] = "test-token"

    from app.main import app as backend_app

    port = _free_port()
    server = _Server(backend_app, port, "/health")
    server.start()
    yield ApiClient(f"http://127.0.0.1:{port}", "test-token", stub, stub_port)
    server.stop()


@pytest.fixture(autouse=True)
def _reset_stub(api):
    """每条用例前把假上游恢复到默认状态，保证互不影响。"""
    api.set_mode("ok")
    api.stub.state["calls"] = 0
    yield
    api.set_mode("ok")
