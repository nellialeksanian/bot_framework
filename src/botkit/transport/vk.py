from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import secrets
from collections import OrderedDict
from typing import Any

import httpx

from .base import Attachment, Button, CallbackQuery, IncomingMessage, split_text
from .common import BaseAdapter

logger = logging.getLogger(__name__)


class VKAdapter(BaseAdapter):
    platform = "vk"
    message_limit = 4096

    def __init__(
        self,
        token: str | None = None,
        *,
        bot: Any = None,
        download_client: httpx.AsyncClient | None = None,
        **kwargs: Any,
    ):
        super().__init__(**kwargs)
        from vkbottle import Bot

        self.native = bot or Bot(token=token)
        self.api = self.native.api
        self.labeler = self.native.labeler
        self._owns_bot = bot is None
        self._owns_downloads = download_client is None
        self._downloads = download_client or httpx.AsyncClient(timeout=60, follow_redirects=False)
        self._users: OrderedDict[int, tuple[str | None, str | None]] = OrderedDict()
        self.labeler.message()(self._receive)
        self.labeler.raw_event("message_event")(self._receive_callback)

    async def call_api(self, method: str, **params: Any) -> Any:
        """Full VK API by dotted method name; .api also exposes typed SDK methods."""

        async def call() -> Any:
            from vkbottle import VKAPIError

            for attempt in range(3):
                try:
                    response = await self.api.request(method, params)
                    return response["response"]
                except VKAPIError as exc:
                    if exc.code not in {6, 9} or attempt == 2:
                        raise
                    await asyncio.sleep(2**attempt)

        return await self._operation(method, call)

    @staticmethod
    def _keyboard(buttons: list[list[Button]]) -> str:
        rows = []
        for row in buttons:
            items = []
            for button in row:
                if button.url:
                    action = {"type": "open_link", "label": button.text, "link": button.url}
                else:
                    action = {
                        "type": "callback",
                        "label": button.text,
                        "payload": json.dumps({"data": button.data}, ensure_ascii=False),
                    }
                items.append({"action": action})
            rows.append(items)
        return json.dumps({"inline": True, "buttons": rows}, ensure_ascii=False)

    async def _send_one(self, chat_id: str, text: str, *, buttons: Any = None, **options: Any) -> str:
        if buttons is not None:
            options["keyboard"] = self._keyboard(buttons)
        options.setdefault("random_id", secrets.randbelow(2**31 - 1) + 1)
        result = await self.call_api("messages.send", peer_id=int(chat_id), message=text, **options)
        return str(result)

    async def _type(self, chat_id: str) -> None:
        await self.call_api("messages.setActivity", peer_id=int(chat_id), type="typing")

    async def send_attachment(
        self,
        chat_id: str,
        data: bytes,
        *,
        filename: str = "file.bin",
        kind: str = "document",
        caption: str = "",
        **options: Any,
    ) -> str:
        from vkbottle import DocMessagesUploader, PhotoMessageUploader

        if kind == "photo":
            uploader = PhotoMessageUploader(self.api, attachment_name=filename)
        elif kind in {"document", "audio", "video"}:
            # Generic audio/video files are documents in the portable API. Use
            # native uploaders for playable VK music/video (different permissions).
            uploader = DocMessagesUploader(self.api)
        else:
            raise ValueError(f"Unsupported VK attachment kind: {kind}")
        upload_params = {"title": filename} if kind != "photo" else {}
        attachment = await self._operation(
            "upload", lambda: uploader.upload(data, peer_id=int(chat_id), **upload_params)
        )
        return str(
            await self.call_api(
                "messages.send",
                peer_id=int(chat_id),
                message=caption,
                attachment=attachment,
                random_id=secrets.randbelow(2**31 - 1) + 1,
                **options,
            )
        )

    async def edit(
        self,
        chat_id: str,
        message_id: str,
        text: str,
        *,
        buttons: list[list[Button]] | None = None,
        **options: Any,
    ) -> Any:
        if len(split_text(text, self.message_limit)) != 1:
            raise ValueError("An edited message must fit in one message")
        if buttons is not None:
            options["keyboard"] = self._keyboard(buttons)
        return await self.call_api(
            "messages.edit", peer_id=int(chat_id), message_id=int(message_id), message=text, **options
        )

    async def delete(self, chat_id: str, message_id: str, **options: Any) -> Any:
        return await self.call_api(
            "messages.delete", peer_id=int(chat_id), message_ids=message_id, delete_for_all=1, **options
        )

    async def answer_callback(self, callback: CallbackQuery, text: str = "", **options: Any) -> Any:
        event_data = json.dumps({"type": "show_snackbar", "text": text}, ensure_ascii=False) if text else None
        return await self.call_api(
            "messages.sendMessageEventAnswer",
            event_id=callback.callback_id,
            user_id=int(callback.user_id),
            peer_id=int(callback.chat_id),
            event_data=event_data,
            **options,
        )

    async def _download(self, url: str) -> bytes:
        if not url.startswith("https://"):
            raise ValueError("VK attachment download requires an HTTPS URL")
        async with self._downloads.stream("GET", url) as response:
            response.raise_for_status()
            if int(response.headers.get("content-length", "0")) > self.max_attachment_bytes:
                raise ValueError("Attachment exceeds the configured size limit")
            result = bytearray()
            async for chunk in response.aiter_bytes():
                if len(result) + len(chunk) > self.max_attachment_bytes:
                    raise ValueError("Attachment exceeds the configured size limit")
                result.extend(chunk)
            return bytes(result)

    async def _identity(self, user_id: int) -> tuple[str | None, str | None]:
        if user_id <= 0:
            return None, None
        if user_id in self._users:
            self._users.move_to_end(user_id)
            return self._users[user_id]
        try:
            users = await self.call_api("users.get", user_ids=str(user_id), fields="screen_name")
            user = users[0]
            identity = (
                user.get("screen_name"),
                " ".join(filter(None, [user.get("first_name"), user.get("last_name")])),
            )
            self._users[user_id] = identity
            if len(self._users) > 512:
                self._users.popitem(last=False)
            return identity
        except Exception as exc:
            logger.warning("VK user profile unavailable (%s)", type(exc).__name__)
            return None, None

    async def normalize(self, message: Any) -> IncomingMessage:
        attachments = []
        for item in message.attachments or []:
            kind = item.type.value if hasattr(item.type, "value") else item.type
            value = getattr(item, kind, None)
            url, mime, filename, size = None, "application/octet-stream", None, None
            if kind == "doc" and value:
                kind, url, filename, size = "document", value.url, value.title, value.size
                mime = mimetypes.guess_type(filename or "")[0] or mime
            elif kind == "photo" and value and value.sizes:
                url = max(value.sizes, key=lambda p: p.width * p.height).url
                mime = "image/jpeg"
            elif kind == "audio_message" and value:
                kind, url, mime = (
                    "audio",
                    value.link_ogg or value.link_mp3,
                    "audio/ogg" if value.link_ogg else "audio/mpeg",
                )
            elif kind == "audio" and value:
                url, mime = value.url, "audio/mpeg"
            if url:
                attachments.append(
                    Attachment(
                        kind=kind,
                        mime_type=mime,
                        filename=filename,
                        size=size,
                        reader=lambda url=url: self._download(url),
                        max_bytes=self.max_attachment_bytes,
                        platform_id=str(getattr(value, "id", "")),
                    )
                )
        username, name = await self._identity(message.from_id)
        return IncomingMessage(
            platform=self.platform,
            user_id=str(message.from_id),
            chat_id=str(message.peer_id),
            text=message.text or "",
            attachments=attachments,
            platform_username=username,
            platform_display_name=name,
            message_id=str(message.id),
            raw=message,
        )

    async def _receive(self, message: Any) -> None:
        await self.dispatch(await self.normalize(message))

    async def _receive_callback(self, event: dict[str, Any]) -> None:
        value = event["object"]
        payload = value.get("payload") or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                payload = {"data": payload}
        await self.dispatch_callback(
            CallbackQuery(
                platform=self.platform,
                callback_id=value["event_id"],
                user_id=str(value["user_id"]),
                chat_id=str(value["peer_id"]),
                data=str(payload.get("data", "")),
                message_id=str(value.get("conversation_message_id", "")),
                raw=event,
            )
        )

    async def run(self) -> None:
        # Own the task lifetime around SDK polling; bound in-flight updates and
        # retrieve every exception instead of leaving fire-and-forget tasks.
        pending: set[asyncio.Task] = set()
        semaphore = asyncio.Semaphore(64)

        async def process(update: dict[str, Any]) -> None:
            try:
                await self.native.process_event(update, self.api)
            except Exception as exc:
                logger.error("VK event handler failed (%s)", type(exc).__name__)
            finally:
                semaphore.release()

        try:
            async for event in self.native.polling.listen():
                for update in event.get("updates", []):
                    await semaphore.acquire()
                    task = asyncio.create_task(process(update))
                    pending.add(task)
                    task.add_done_callback(pending.discard)
        finally:
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            await self.aclose()

    async def aclose(self) -> None:
        await super().aclose()
        if self._owns_downloads:
            await self._downloads.aclose()
        if self._owns_bot:
            await self.api.http_client.close()
