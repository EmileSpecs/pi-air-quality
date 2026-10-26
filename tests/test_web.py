"""Login gate and the unauthenticated health check."""

import asyncio
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from piair.config import ConfigStore
from piair.web.server import create_app

EXAMPLE = Path(__file__).resolve().parents[1] / "config" / "config.example.yaml"


def _store(tmp_path: Path) -> ConfigStore:
    config = tmp_path / "config.yaml"
    secrets = tmp_path / "secrets.yaml"
    config.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    secrets.write_text('mqtt_password: "x"\nadmin_password: "secret-admin"\n', encoding="utf-8")
    return ConfigStore(config, secrets)


def test_health_is_open_and_status_requires_login(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = create_app(
            _store(tmp_path),
            snapshot=lambda: {
                "formaldehyde": "12.0 ppb",
                "formaldehyde_status": "ready",
                "temperature": "22.5 °C",
                "humidity": "45.0 %",
                "pm1": "—",
                "pm25": "—",
                "pm10": "—",
                "co2": "—",
                "particulate_enabled": False,
                "co2_enabled": False,
                "mqtt": "connected",
                "updated": "now",
                "version": "abc",
                "detail": "",
            },
        )
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            health = await client.get("/healthz")
            assert health.status == 200
            status = await client.get("/status", allow_redirects=False)
            assert status.status == 302
            login = await client.post("/login", data={"password": "secret-admin"})
            assert login.status == 200
            page = await client.get("/status")
            text = await page.text()
            assert "12.0 ppb" in text
            connection = await client.get("/connection")
            form = await connection.text()
            assert "lounge_air" in form
        finally:
            await client.close()

    asyncio.run(scenario())
