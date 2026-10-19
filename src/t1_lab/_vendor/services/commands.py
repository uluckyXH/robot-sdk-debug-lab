"""Durable command dispatch independent of HTTP request handling."""

import asyncio
import logging

from t1_lab._vendor.contracts.catalog import BridgeError

LOG = logging.getLogger("roboweave.bridge")


class Commands:
    def __init__(self, adapter, ledger):
        self.adapter, self.ledger, self.tasks = adapter, ledger, set()
        self.motion = None

    async def submit(self, value):
        receipt, new = self.ledger.begin(value)
        if not new:
            return receipt
        if self.tasks:
            return self.ledger.finish(
                value["command_id"], {"status": "failed", "detail": "命令派发忙；未发送，无自动排队"}
            )
        task = asyncio.create_task(self.dispatch(value))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        # HTTP cancellation must not silently lose the durable command result.
        return await asyncio.shield(task)

    async def dispatch(self, value):
        try:
            operation = (
                self.motion.dispatch(value)
                if value["kind"] == "motion_preset"
                else self.adapter.command(value["kind"], value["arguments"], value["command_id"])
            )
            result = await asyncio.wait_for(operation, 4)
        except BridgeError as exc:
            result = {"status": "failed", "detail": exc.detail, "execution_confirmed": False}
        except asyncio.TimeoutError:
            result = {
                "status": "unknown",
                "detail": "SDK 调用超时；执行状态未知，不重发",
                "execution_confirmed": False,
            }
        except asyncio.CancelledError:
            self.ledger.finish(
                value["command_id"], {"status": "unknown", "detail": "桥接退出；执行状态未知，不重发"}
            )
            raise
        except Exception:
            result = {
                "status": "unknown",
                "detail": "SDK 调用异常；执行状态未知，不重发",
                "execution_confirmed": False,
            }
        LOG.info("command kind=%s status=%s", value["kind"], result["status"])
        return self.ledger.finish(value["command_id"], result)

    async def close(self):
        if self.tasks:
            _, pending = await asyncio.wait(self.tasks, timeout=5)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
