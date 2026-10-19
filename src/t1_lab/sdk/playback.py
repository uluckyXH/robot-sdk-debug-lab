"""Explicit upload playback through the vendor's PCM TCP endpoint."""

import asyncio
import hashlib

from t1_lab._vendor.contracts.catalog import BridgeError

MAX_UPLOAD = 16 * 1024 * 1024
MAX_PCM = 48000 * 2 * 120


class TcpAudioPlayer:
    def __init__(self, sdk):
        self.sdk, self.task, self.writer, self.identifier = sdk, None, None, None

    async def decode(self, data, format):
        if not data or len(data) > MAX_UPLOAD:
            raise BridgeError(422, "音频文件须为 1 字节至 16 MiB")
        if format == "pcm":
            if len(data) % 2 or len(data) > MAX_PCM:
                raise BridgeError(422, "PCM 须为 48 kHz / mono / s16le，最多 120 秒")
            return data
        if format not in ("wav", "mp3", "ogg", "flac"):
            raise BridgeError(422, "支持 WAV / MP3 / OGG / FLAC / 原始 PCM")
        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-protocol_whitelist",
            "pipe",
            "-f",
            format,
            "-i",
            "pipe:0",
            "-t",
            "121",
            "-ac",
            "1",
            "-ar",
            "48000",
            "-f",
            "s16le",
            "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            pcm, error = await asyncio.wait_for(process.communicate(data), 20)
            if process.returncode or not pcm:
                raise BridgeError(422, "无法解码音频：" + error.decode(errors="replace")[:300])
            if len(pcm) > MAX_PCM:
                raise BridgeError(422, "音频最长 120 秒")
            return pcm
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    async def start(self, data, format, command_id):
        self.sdk.interfaces.guard("TcpAudioPlayback")
        args = {"format": format, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        receipt, new = self.sdk.ledger.begin(
            {
                "command_id": command_id,
                "device_uid": self.sdk.config.device_uid,
                "kind": "sdk:TcpAudioPlayback",
                "arguments": args,
            }
        )
        if not new:
            return receipt
        if self.task and not self.task.done():
            return self.sdk.ledger.finish(
                command_id,
                {
                    "status": "failed",
                    "detail": "已有音频正在发送，请先结束当前播放",
                    "execution_confirmed": False,
                },
            )
        self.identifier = command_id
        self.task = asyncio.create_task(self._send(data, format, command_id))
        return self.sdk.ledger.finish(
            command_id, {"status": "running", "detail": "正在解码并发送音频", "execution_confirmed": False}
        )

    async def _send(self, data, format, command_id):
        sent = 0
        try:
            pcm = await self.decode(data, format)
            self.sdk.interfaces.guard("TcpAudioPlayback")
            _, self.writer = await asyncio.wait_for(
                asyncio.open_connection(self.sdk.settings.robot_ip, self.sdk.settings.playback_port), 3
            )
            clock = asyncio.get_running_loop().time()
            for offset in range(0, len(pcm), 9600):
                chunk = pcm[offset : offset + 9600]
                self.writer.write(chunk)
                await asyncio.wait_for(self.writer.drain(), 3)
                sent += len(chunk)
                await asyncio.sleep(max(0, clock + sent / 96000 - asyncio.get_running_loop().time()))
            result = {"status": "accepted", "detail": "音频数据发送完成；机器人播放完成事件不可用"}
        except asyncio.CancelledError:
            result = {"status": "stopped", "detail": "已停止继续发送；机器人已缓冲的音频可能仍在播放"}
        except Exception as exc:
            result = {
                "status": "unknown" if sent else "failed",
                "detail": str(exc)[:400] or type(exc).__name__,
            }
        finally:
            if self.writer:
                self.writer.close()
                try:
                    await asyncio.wait_for(self.writer.wait_closed(), 2)
                except (OSError, asyncio.TimeoutError):
                    pass
                self.writer = None
        self.sdk.ledger.finish(
            command_id, {**result, "bytes_sent": sent, "execution_confirmed": False, "output_finished": True}
        )

    async def stop(self, command_id):
        if self.identifier == command_id and self.task and not self.task.done():
            self.task.cancel()
            await self.task
        return self.sdk.receipt(command_id)

    async def close(self):
        if self.task and not self.task.done():
            self.task.cancel()
            await self.task
