"""Durable idempotency: record dispatch intent before touching a service."""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid

from t1_lab._vendor.contracts.catalog import BridgeError, COMMITS, now

COMMAND_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")


class Ledger:
    def __init__(self, config, limit=10000):
        self.epoch = uuid.uuid4().hex
        self.limit = limit
        path = Path(config.journal)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = open(path.with_suffix(path.suffix + ".lock"), "a")
        os.chmod(self.lock.name, 0o600)
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock.close()
            raise ValueError("此命令日志已由另一桥接进程使用") from None
        self.db = sqlite3.connect(path)
        os.chmod(path, 0o600)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS commands (id TEXT PRIMARY KEY, digest TEXT NOT NULL, receipt TEXT NOT NULL)"
        )
        self.db.execute("CREATE TABLE IF NOT EXISTS motion_epochs (epoch TEXT PRIMARY KEY)")
        identity = json.dumps(
            [config.bridge_id, config.device_uid, config.family, config.simulated, COMMITS[config.family]]
        )
        existing = self.db.execute("SELECT value FROM meta WHERE key='identity'").fetchone()
        if existing and existing[0] != identity:
            self.close()
            raise ValueError("命令日志属于其他设备或模式；不得跨设备复用")
        self.db.execute("INSERT OR IGNORE INTO meta VALUES ('identity', ?)", (identity,))
        self.motion_pending = set()
        last_motion = self.db.execute("SELECT value FROM meta WHERE key='motion_last_started'").fetchone()
        self.motion_last_started = float(last_motion[0]) if last_motion else None
        fence = self.db.execute("SELECT value FROM meta WHERE key='motion_fence'").fetchone()
        self.motion_fence = json.loads(fence[0]) if fence else None
        if self.motion_fence:
            self.db.execute(
                "INSERT OR IGNORE INTO motion_epochs VALUES (?)", (self.motion_fence["connection_epoch"],)
            )
        for row in self.db.execute("SELECT id, receipt FROM commands").fetchall():
            receipt = json.loads(row[1])
            if receipt["status"] == "dispatching" or (
                (
                    receipt.get("kind") in ("motion_request", "motion_stop", "sdk:TcpAudioPlayback")
                    or receipt.get("kind", "").startswith("sdk-stream:")
                )
                and receipt["status"] in ("accepted", "running", "stop_requested")
                and not receipt.get("output_finished")
            ):
                receipt.update(status="unknown", detail="桥接重启时命令未决；不会重发", updated_at=now())
                self.db.execute("UPDATE commands SET receipt=? WHERE id=?", (json.dumps(receipt), row[0]))
            self._motion_pending(receipt)
        self.db.commit()

    def begin(self, body):
        command_id = body["command_id"]
        if not isinstance(command_id, str) or not COMMAND_ID.fullmatch(command_id):
            raise BridgeError(422, "command_id 格式无效")
        digest = hashlib.sha256(
            json.dumps(
                body, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            ).encode()
        ).hexdigest()
        row = self.db.execute("SELECT digest, receipt FROM commands WHERE id=?", (command_id,)).fetchone()
        if row:
            if digest != row[0]:
                raise BridgeError(409, "相同 command_id 对应不同命令内容")
            return json.loads(row[1]), False
        if not command_id.startswith(self.epoch + ":") or len(command_id) <= len(self.epoch) + 1:
            raise BridgeError(409, "命令 epoch 已失效或缺失；不能证明旧命令可安全重试")
        if self.db.execute("SELECT count(*) FROM commands").fetchone()[0] >= self.limit:
            raise BridgeError(503, "命令日志已满；保留去重证据并拒绝新命令")
        receipt = {
            "command_id": command_id,
            "kind": body["kind"],
            "status": "dispatching",
            "execution_confirmed": False,
            "feedback_source": "bridge-journal",
            "detail": "派发意图已持久化",
            "updated_at": now(),
        }
        with self.db:
            self.db.execute(
                "INSERT INTO commands VALUES (?, ?, ?)", (command_id, digest, json.dumps(receipt))
            )
        self._motion_pending(receipt)
        return receipt, True

    def finish(self, command_id, result):
        receipt = self.get(command_id)
        receipt.update(result, updated_at=now())
        with self.db:
            self.db.execute("UPDATE commands SET receipt=? WHERE id=?", (json.dumps(receipt), command_id))
        self._motion_pending(receipt)
        return receipt

    def _motion_pending(self, receipt):
        if receipt.get("kind") not in ("motion_preset", "motion_request"):
            return
        if receipt["status"] in ("dispatching", "unknown", "accepted", "running", "stop_requested") and not (
            receipt.get("operator_observed_end") or receipt.get("output_finished")
        ):
            self.motion_pending.add(receipt["command_id"])
        else:
            self.motion_pending.discard(receipt["command_id"])

    def motion_started(self, command_id, preset, timestamp):
        # Both the durable command intent and the persistent rate limit precede dispatch.
        receipt = self.get(command_id)
        receipt.update(preset=preset, dispatch_started_at=now())
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO meta VALUES ('motion_last_started', ?)", (str(timestamp),)
            )
            self.db.execute("UPDATE commands SET receipt=? WHERE id=?", (json.dumps(receipt), command_id))
        self.motion_last_started = timestamp

    def fence_motion(self, scope, *, advance=True):
        """A stop with no active task still revokes older in-flight requests durably."""
        incoming = {key: scope[key] for key in ("control_revision", "binding_revision", "connection_epoch")}
        previous = self.motion_fence
        if previous is not None:
            if incoming["control_revision"] < previous["control_revision"]:
                raise BridgeError(409, "运动控制代次已失效；拒绝迟到指令")
            if incoming["control_revision"] == previous["control_revision"]:
                if incoming != previous:
                    raise BridgeError(409, "相同控制代次不能更换设备绑定或连接 epoch")
                return False
            if incoming["binding_revision"] < previous["binding_revision"]:
                raise BridgeError(409, "设备绑定版本回退；拒绝旧数据库或迟到绑定")
        if not advance:
            raise BridgeError(409, "心跳不能建立或推进运动控制代次")
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES ('motion_fence', ?)", (json.dumps(incoming),))
        self.motion_fence = incoming
        return True

    def bind_motion(self, scope):
        epoch = scope["connection_epoch"]
        previous = self.motion_fence
        if previous and epoch != previous["connection_epoch"]:
            if self.db.execute("SELECT 1 FROM motion_epochs WHERE epoch=?", (epoch,)).fetchone():
                raise BridgeError(409, "此连接 epoch 已退休，不能恢复")
            if scope["control_revision"] <= previous["control_revision"]:
                raise BridgeError(409, "新连接必须使用更高的持久控制代次")
        if not self.db.execute("SELECT 1 FROM motion_epochs WHERE epoch=?", (epoch,)).fetchone():
            if self.db.execute("SELECT count(*) FROM motion_epochs").fetchone()[0] >= self.limit:
                raise BridgeError(503, "连接代次记录已满，保留拒绝旧连接的证据")
        changed = self.fence_motion(scope)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO motion_epochs VALUES (?)", (epoch,))
        return changed

    def get(self, command_id):
        row = self.db.execute("SELECT receipt FROM commands WHERE id=?", (command_id,)).fetchone()
        if row is None:
            raise BridgeError(404, "未知命令；不会据此重新派发")
        return json.loads(row[0])

    def close(self):
        self.db.close()
        self.lock.close()
