from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.api.routes.instances import create_instance_router
from server.instance import ExecutorInstance


class ExecutorRegistry:
    def __init__(self):
        self.registered = []

    def register(self, instance):
        self.registered.append(instance)


def test_register_instance_requires_nonce_and_records_client_ip():
    registry = ExecutorRegistry()
    router, _ = create_instance_router(lambda: "nonce-123", registry)
    app = FastAPI()
    app.include_router(router)

    with TestClient(app) as client:
        rejected = client.post(
            "/register",
            headers={"X-Nonce": "wrong"},
            json={"ip": "ignored", "port": 5000},
        )
        accepted = client.post(
            "/register",
            headers={"X-Nonce": "nonce-123"},
            json={"ip": "ignored", "port": 5001},
        )

    assert rejected.status_code == 401
    assert accepted.status_code == 200
    assert len(registry.registered) == 1
    instance = registry.registered[0]
    assert isinstance(instance, ExecutorInstance)
    assert instance.ip == "testclient"
    assert instance.port == 5001
