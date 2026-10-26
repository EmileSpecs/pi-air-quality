"""Status and connection pages."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from aiohttp import web
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import ValidationError

from piair.config import AirConfig, ConfigError, ConfigStore, locked_device_id
from piair.web.auth import COOKIE, new_session, sign, unsign

TEMPLATES = Path(__file__).parent / "templates"


def create_app(
    store: ConfigStore,
    *,
    snapshot: Callable[[], dict[str, Any]],
    on_saved: Callable[[], Awaitable[None]] | None = None,
    on_update: Callable[[], Awaitable[bool]] | None = None,
    device_locked: Callable[[], bool] | None = None,
) -> web.Application:
    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(["html"]),
    )
    app = web.Application()
    app["store"] = store
    app["env"] = env
    app["snapshot"] = snapshot
    app["on_saved"] = on_saved
    app["on_update"] = on_update
    app["device_locked"] = device_locked or (lambda: False)
    app.router.add_get("/healthz", health)
    app.router.add_get("/login", login_page)
    app.router.add_post("/login", login_post)
    app.router.add_post("/logout", logout)
    app.router.add_get("/", status_page)
    app.router.add_get("/status", status_page)
    app.router.add_get("/status.json", status_json)
    app.router.add_post("/update", update_post)
    app.router.add_get("/connection", connection_page)
    app.router.add_post("/connection", connection_post)
    app.router.add_post("/connection/test", connection_test)
    return app


def _secret(store: ConfigStore) -> str:
    return store.config.admin.password or "piair"


def _session(request: web.Request) -> dict[str, Any] | None:
    store: ConfigStore = request.app["store"]
    token = request.cookies.get(COOKIE, "")
    if not token:
        return None
    return unsign(token, _secret(store))


def _require(request: web.Request) -> dict[str, Any]:
    session = _session(request)
    if session is None or not session.get("ok"):
        raise web.HTTPFound("/login")
    return session


def _check_csrf(data: Any, session: dict[str, Any]) -> None:
    if str(data.get("csrf") or "") != str(session.get("csrf") or ""):
        raise web.HTTPForbidden(text="form expired")


def _render(request: web.Request, template: str, session: dict[str, Any], **extra: Any) -> web.Response:
    store: ConfigStore = request.app["store"]
    env: Environment = request.app["env"]
    html = env.get_template(template).render(
        config=store.config,
        csrf=session.get("csrf", ""),
        locked=bool(request.app["device_locked"]()),
        **extra,
    )
    return web.Response(text=html, content_type="text/html")


def _done(path: str, notice: str = "Saved.") -> web.HTTPFound:
    from urllib.parse import quote

    raise web.HTTPFound(f"{path}?notice={quote(notice)}")


async def health(_request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def login_page(request: web.Request) -> web.Response:
    if _session(request):
        raise web.HTTPFound("/")
    env: Environment = request.app["env"]
    html = env.get_template("login.html").render(error=request.query.get("error"))
    return web.Response(text=html, content_type="text/html")


async def login_post(request: web.Request) -> web.Response:
    store: ConfigStore = request.app["store"]
    data = await request.post()
    password = str(data.get("password") or "")
    if password != (store.config.admin.password or ""):
        raise web.HTTPFound("/login?error=1")
    response = web.HTTPFound("/")
    response.set_cookie(
        COOKIE,
        sign(new_session(), _secret(store)),
        httponly=True,
        samesite="Lax",
        path="/",
        max_age=60 * 60 * 24 * 14,
    )
    raise response


async def logout(request: web.Request) -> web.Response:
    response = web.HTTPFound("/login")
    response.del_cookie(COOKIE, path="/")
    raise response


def _display(value: Any, suffix: str = "") -> str:
    if value is None or value == "":
        return "—"
    return f"{value}{suffix}"


async def status_page(request: web.Request) -> web.Response:
    session = _require(request)
    snap = request.app["snapshot"]()
    return _render(
        request,
        "status.html",
        session,
        snap=snap,
        notice=request.query.get("notice"),
        error=request.query.get("error"),
    )


async def status_json(request: web.Request) -> web.Response:
    session = _session(request)
    if session is None or not session.get("ok"):
        raise web.HTTPUnauthorized(text="sign in first")
    return web.json_response(request.app["snapshot"](), headers={"Cache-Control": "no-store"})


async def update_post(request: web.Request) -> web.Response:
    session = _require(request)
    data = await request.post()
    _check_csrf(data, session)
    store: ConfigStore = request.app["store"]
    update = store.config.update.model_copy(
        update={
            "auto": data.get("auto") == "on",
            "ref": str(data.get("ref") or "main").strip() or "main",
        }
    )
    config = store.config.model_copy(update={"update": update})
    error = await _save(request, config, dict(store.secrets))
    if error:
        raise web.HTTPFound(f"/status?error={_quote(error)}")
    if data.get("action") == "now":
        starter = request.app["on_update"]
        started = await starter() if starter else False
        if not started:
            raise web.HTTPFound("/status?error=" + _quote("The update script is not on this machine."))
        _done("/status", "Update started.")
    _done("/status", "Saved.")
    raise AssertionError("redirect")


def _quote(text: str) -> str:
    from urllib.parse import quote

    return quote(text)


async def connection_page(request: web.Request) -> web.Response:
    session = _require(request)
    return _render(
        request,
        "connection.html",
        session,
        notice=request.query.get("notice"),
        error=request.query.get("error"),
        form=_connection_form({}, request.app["store"].config),
    )


def _connection_form(data: Any, config: AirConfig) -> dict[str, str]:
    def posted(key: str, fallback: str) -> str:
        if key in data:
            return str(data.get(key) or "")
        return fallback

    return {
        "device_id": posted("device_id", config.device.id),
        "name": posted("name", config.device.name),
        "host": posted("host", config.mqtt.host),
        "port": posted("port", str(config.mqtt.port)),
        "username": posted("username", config.mqtt.username or ""),
        "base_topic": posted("base_topic", config.mqtt.base_topic),
        "discovery_prefix": posted("discovery_prefix", config.mqtt.discovery_prefix),
    }


async def connection_post(request: web.Request) -> web.Response:
    session = _require(request)
    data = await request.post()
    _check_csrf(data, session)
    store: ConfigStore = request.app["store"]
    form = _connection_form(data, store.config)
    try:
        device_id = locked_device_id(
            bool(request.app["device_locked"]()),
            store.config.device.id,
            str(data.get("device_id") or "").strip(),
        )
        port = int(str(data.get("port") or "1883"))
        config = store.config.model_copy(
            update={
                "device": store.config.device.model_copy(
                    update={
                        "id": device_id,
                        "name": str(data.get("name") or "").strip(),
                    }
                ),
                "mqtt": store.config.mqtt.model_copy(
                    update={
                        "host": str(data.get("host") or "").strip(),
                        "port": port,
                        "username": str(data.get("username") or "").strip() or None,
                        "password": store.secrets.get("mqtt_password", ""),
                        "base_topic": str(data.get("base_topic") or "piair").strip(),
                        "discovery_prefix": str(data.get("discovery_prefix") or "homeassistant").strip(),
                    }
                ),
            }
        )
        config = AirConfig.model_validate(config.model_dump())
    except (ValidationError, ConfigError, ValueError) as exc:
        return _render(request, "connection.html", session, error=_friendly(exc), form=form)
    secrets = dict(store.secrets)
    password = str(data.get("password") or "")
    if password:
        secrets["mqtt_password"] = password
        config = config.model_copy(
            update={"mqtt": config.mqtt.model_copy(update={"password": password})}
        )
    error = await _save(request, config, secrets)
    if error:
        return _render(request, "connection.html", session, error=error, form=form)
    _done("/connection")
    raise AssertionError("redirect")


async def connection_test(request: web.Request) -> web.Response:
    session = _require(request)
    data = await request.post()
    _check_csrf(data, session)
    store: ConfigStore = request.app["store"]
    form = _connection_form(data, store.config)
    notice = None
    error = None
    try:
        port = int(form["port"] or "1883")
        _reader, writer = await asyncio.wait_for(asyncio.open_connection(form["host"], port), 3)
        writer.close()
        await writer.wait_closed()
        notice = "Broker port accepted a TCP connection."
    except ValueError:
        error = "Port must be a number."
    except OSError as exc:
        error = f"Connection failed: {exc}"
    return _render(request, "connection.html", session, notice=notice, error=error, form=form)


def _friendly(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        messages = []
        for item in exc.errors():
            messages.append(str(item.get("msg") or "").removeprefix("Value error, "))
        return "; ".join(dict.fromkeys(messages)) or str(exc)
    return str(exc)


async def _save(request: web.Request, config: AirConfig, secrets: dict[str, Any]) -> str | None:
    store: ConfigStore = request.app["store"]
    try:
        store.save(config, secrets)
    except (OSError, ConfigError, ValidationError) as exc:
        return str(exc)
    callback = request.app["on_saved"]
    if callback is not None:
        await callback()
    return None
