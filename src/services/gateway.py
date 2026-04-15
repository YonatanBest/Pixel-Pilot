from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

import websockets

from config import Config

logger = logging.getLogger("pixelpilot.gateway")


class GatewayServer:
    def __init__(
        self,
        *,
        live_session,
        host: Optional[str] = None,
        port: Optional[int] = None,
        auth_token: Optional[str] = None,
        command_timeout_s: Optional[float] = None,
    ) -> None:
        self.live_session = live_session
        self.host = str(host or Config.GATEWAY_HOST)
        self.port = int(port or Config.GATEWAY_PORT)
        self.auth_token = (
            str(auth_token).strip()
            if auth_token is not None
            else str(Config.GATEWAY_TOKEN or "").strip()
        )
        self.command_timeout_s = max(
            5.0,
            float(command_timeout_s or Config.GATEWAY_COMMAND_TIMEOUT_SECONDS),
        )
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._shutdown_event: Optional[asyncio.Event] = None

    def attach_live_session(self, live_session) -> None:
        self.live_session = live_session

    async def _execute_live_turn(self, command: str) -> dict[str, Any]:
        session = self.live_session
        if session is None:
            return {
                "ok": False,
                "error": "live_unavailable",
                "message": "PixelPilot Live is unavailable.",
                "text": "",
            }
        return await asyncio.to_thread(
            session.submit_text_and_wait,
            command,
            self.command_timeout_s,
        )

    @staticmethod
    def _build_command(command: str, params: dict[str, Any]) -> str:
        clean = str(command or "").strip()
        if not params:
            return clean
        suffix = " ".join(f"{key}: {value}" for key, value in params.items())
        return f"{clean} {suffix}".strip()

    async def handler(self, websocket) -> None:
        client_info = websocket.remote_address
        logger.info("Gateway client connected: %s", client_info)

        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    await websocket.send(json.dumps({"error": "Invalid JSON"}))
                    continue

                if self.auth_token and str(data.get("auth") or "") != self.auth_token:
                    logger.warning("Unauthorized gateway access attempt from %s", client_info)
                    await websocket.send(json.dumps({"error": "Unauthorized"}))
                    continue

                command = str(data.get("command") or "").strip()
                params = data.get("params") or {}
                if not isinstance(params, dict):
                    params = {}

                if not command:
                    await websocket.send(json.dumps({"error": "No command provided"}))
                    continue

                full_command = self._build_command(command, params)
                logger.info("Gateway executing: %s", full_command)

                try:
                    result = await self._execute_live_turn(full_command)
                    response = {
                        "status": "ok" if result.get("ok") else "failed",
                        "result": bool(result.get("ok")),
                        "output": str(result.get("text") or result.get("message") or ""),
                        "error": str(result.get("error") or ""),
                        "params": params,
                    }
                    await websocket.send(json.dumps(response))
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Gateway execution error")
                    await websocket.send(
                        json.dumps({"error": f"Execution failed: {exc}"})
                    )
        except websockets.exceptions.ConnectionClosed:
            logger.info("Gateway client disconnected: %s", client_info)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Gateway handler error: %s", exc)

    async def serve(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._shutdown_event = asyncio.Event()
        logger.info("Starting Gateway Server on ws://%s:%s", self.host, self.port)
        async with websockets.serve(self.handler, self.host, self.port):
            await self._shutdown_event.wait()

    def start(self) -> None:
        try:
            asyncio.run(self.serve())
        except KeyboardInterrupt:
            logger.info("Gateway server stopped by user")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Gateway server failed to start: %s", exc)

    def stop(self) -> None:
        if self._loop and self._shutdown_event is not None:
            self._loop.call_soon_threadsafe(self._shutdown_event.set)
