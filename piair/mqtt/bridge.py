"""Publish discovery and state, and accept the update button."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from piair.config import AirConfig
from piair.mqtt.discovery import DiscoveryContext, build_component_payloads
from piair.mqtt.memory import Incoming, MqttTransport, MqttWill
from piair.mqtt.state_payload import dumps, state_payload
from piair.mqtt.topics import Topics
from piair.reading import Reading
from piair.state import StateStore

log = logging.getLogger("piair.mqtt")


class MqttBridge:
    def __init__(
        self,
        config: AirConfig,
        transport: MqttTransport,
        store: StateStore,
        *,
        context: DiscoveryContext | None = None,
        on_update: Callable[[], Awaitable[None]] | None = None,
        on_connected: Callable[[], None] | None = None,
    ) -> None:
        self.config = config
        self.transport = transport
        self.store = store
        self.context = context or DiscoveryContext(support_url=config.support_url())
        self.on_update = on_update
        self.on_connected = on_connected
        self.topics = Topics(config)
        self.connected = False
        self.reading = Reading()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._reconnect = asyncio.Event()
        self._pending: dict[str, tuple[str, bool, int]] = {}
        self._last_error = ""

    def update_config(self, config: AirConfig) -> bool:
        previous = self.config
        self.config = config
        self.topics = Topics(config)
        if getattr(self.transport, "config", None) is not None:
            self.transport.config = config  # type: ignore[attr-defined]
        self.context = DiscoveryContext(
            ip=self.context.ip,
            hw_version=self.context.hw_version,
            sw_version=self.context.sw_version,
            support_url=config.support_url(),
        )
        reconnect = previous.mqtt.connection_key(previous.device.id) != config.mqtt.connection_key(
            config.device.id
        )
        if reconnect:
            self._reconnect.set()
        return reconnect

    async def start(self) -> None:
        self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        self._stop.set()
        self._reconnect.set()
        if self.transport.connected:
            try:
                await self.transport.publish(self.topics.status(), "offline", retain=True, qos=1)
            except Exception:
                log.debug("could not publish offline", exc_info=True)
            await self.transport.disconnect()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def run(self) -> None:
        delay = self.config.mqtt.reconnect.min_delay_s
        while not self._stop.is_set():
            try:
                await self._on_connect()
                delay = self.config.mqtt.reconnect.min_delay_s
                if self._reconnect.is_set():
                    await self.transport.disconnect()
                else:
                    await self._pump()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("mqtt disconnected: %s", exc)
                self._last_error = str(exc)
            self.connected = False
            if self._stop.is_set():
                break
            if self._reconnect.is_set():
                self._reconnect.clear()
                delay = self.config.mqtt.reconnect.min_delay_s
                continue
            try:
                await asyncio.wait_for(self._reconnect.wait(), timeout=delay)
            except (asyncio.TimeoutError, TimeoutError):
                delay = min(delay * 2, self.config.mqtt.reconnect.max_delay_s)
            self._reconnect.clear()

    async def publish_state(self, reading: Reading | None = None) -> None:
        if reading is not None:
            self.reading = reading
        payload = dumps(state_payload(self.config, self.reading))
        await self._publish(self.topics.state(), payload, retain=True, qos=1)

    async def publish_discovery(self) -> None:
        payloads = build_component_payloads(self.config, self.context)
        current = set(payloads)
        for topic, payload in payloads.items():
            await self._publish(topic, dumps(payload), retain=True, qos=1)
        for topic in set(self.store.discovery_topics) - current:
            await self._publish(topic, "", retain=True, qos=1)
        self.store.discovery_topics = sorted(current)
        self.store.save()

    async def _on_connect(self) -> None:
        if not self.transport.connected:
            await self.transport.connect(MqttWill(self.topics.status(), "offline", retain=True, qos=1))
        await self.transport.subscribe(f"{self.topics.base}/cmd/#")
        await self.transport.subscribe(self.topics.birth())
        self._pending.clear()
        self.connected = True
        self._last_error = ""
        if not self.store.device_id_locked:
            self.store.device_id_locked = True
            self.store.save()
        if self.on_connected:
            self.on_connected()
        await self.publish_discovery()
        await self.publish_state()
        await self._publish(self.topics.status(), "online", retain=True, qos=1)
        await self._flush()

    async def _pump(self) -> None:
        incoming = self.transport.messages()
        message_task = asyncio.create_task(self._next_message(incoming))
        wake_task = asyncio.create_task(self._reconnect.wait())
        try:
            while not self._stop.is_set():
                done, _pending = await asyncio.wait(
                    {message_task, wake_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if wake_task in done or self._stop.is_set():
                    return
                message = message_task.result()
                if message is None:
                    return
                await self._on_message(message)
                message_task = asyncio.create_task(self._next_message(incoming))
        finally:
            for task in (message_task, wake_task):
                if not task.done():
                    task.cancel()
            if self.transport.connected:
                await self.transport.disconnect()
            self.connected = False

    async def _next_message(self, incoming: Any) -> Incoming | None:
        try:
            message = await incoming.__anext__()
        except StopAsyncIteration:
            return None
        return message if isinstance(message, Incoming) else None

    async def _on_message(self, message: Incoming) -> None:
        if message.topic == self.topics.birth():
            if message.payload == "online":
                await self.publish_discovery()
                await self.publish_state()
                await self._publish(self.topics.status(), "online", retain=True, qos=1)
            return
        if message.retain:
            return
        if message.topic == self.topics.command("update") and message.payload == "PRESS":
            if self.on_update is not None:
                await self.on_update()

    async def _publish(self, topic: str, payload: str, *, retain: bool, qos: int) -> None:
        if not self.transport.connected:
            self._pending[topic] = (payload, retain, qos)
            return
        await self.transport.publish(topic, payload, retain=retain, qos=qos)

    async def _flush(self) -> None:
        pending = self._pending
        self._pending = {}
        for topic, (payload, retain, qos) in pending.items():
            await self.transport.publish(topic, payload, retain=retain, qos=qos)
