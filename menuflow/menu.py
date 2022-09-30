from __future__ import annotations

from typing import TYPE_CHECKING, Any, AsyncGenerator, Awaitable, Callable
from collections import defaultdict
import asyncio
import logging

from aiohttp import ClientSession

from mautrix.client import Client, InternalEventType
from mautrix.errors import MatrixInvalidToken
from mautrix.types import (
    DeviceID,
    EventFilter,
    EventType,
    Filter,
    FilterID,
    Membership,
    MessageEvent,
    PresenceState,
    RoomEventFilter,
    RoomFilter,
    StateFilter,
    StrippedStateEvent,
    SyncToken,
    UserID,
)
from mautrix.util.logging import TraceLogger

from .db import Client as DBClient
from .jinja.jinja_template import FILTERS
from .matrix import MenuFlowMatrixClient
from .user import User

if TYPE_CHECKING:
    from .__main__ import MenuFlow


class MenuClient(DBClient):
    menuflow: "MenuFlow" = None
    cache: dict[UserID, Client] = {}
    _async_get_locks: dict[Any, asyncio.Lock] = defaultdict(lambda: asyncio.Lock())
    log: TraceLogger = logging.getLogger("menuflow.client")

    http_client: ClientSession = None

    started: bool
    sync_ok: bool

    def __init__(
        self,
        id: UserID,
        homeserver: str,
        access_token: str,
        device_id: DeviceID,
        enabled: bool = False,
        next_batch: SyncToken = "",
        filter_id: FilterID = "",
        sync: bool = True,
        autojoin: bool = True,
        online: bool = True,
    ) -> None:
        super().__init__(
            id=id,
            homeserver=homeserver,
            access_token=access_token,
            device_id=device_id,
            enabled=bool(enabled),
            next_batch=next_batch,
            filter_id=filter_id,
            sync=bool(sync),
            autojoin=bool(autojoin),
            online=bool(online),
        )
        self._postinited = False

    @classmethod
    def init_cls(cls, menuflow: "MenuFlow") -> None:
        cls.menuflow = menuflow

    def _make_client(
        self, homeserver: str | None = None, token: str | None = None, device_id: str | None = None
    ) -> MenuFlowMatrixClient:
        return MenuFlowMatrixClient(
            mxid=self.id,
            base_url=homeserver or self.homeserver,
            token=token or self.access_token,
            client_session=self.http_client,
            log=self.log,
            crypto_log=self.log.getChild("crypto"),
            loop=self.menuflow.loop,
            device_id=device_id or self.device_id,
            sync_store=self,
            state_store=self.menuflow.state_store,
        )

    def postinit(self) -> None:
        if self._postinited:
            raise RuntimeError("postinit() called twice")
        self._postinited = True
        self.cache[self.id] = self
        self.log = self.log.getChild(self.id)
        self.http_client = ClientSession(loop=self.maubot.loop)
        self.started = False
        self.sync_ok = True
        self.client = self._make_client()
        # if self.enable_crypto:
        #     self._prepare_crypto()
        # else:
        #     self.crypto_store = None
        #     self.crypto = None
        self.client.ignore_initial_sync = True
        self.client.ignore_first_sync = True
        self.client.presence = PresenceState.ONLINE if self.online else PresenceState.OFFLINE
        if self.autojoin:
            self.client.add_event_handler(EventType.ROOM_MEMBER, self.handle_invite)

        self.client.add_event_handler(EventType.ROOM_MESSAGE, self.handle_message)
        self.client.add_event_handler(InternalEventType.SYNC_ERRORED, self._set_sync_ok(False))
        self.client.add_event_handler(InternalEventType.SYNC_SUCCESSFUL, self._set_sync_ok(True))

    def _set_sync_ok(self, ok: bool) -> Callable[[dict[str, Any]], Awaitable[None]]:
        async def handler(data: dict[str, Any]) -> None:
            self.sync_ok = ok

        return handler

    async def start(self, try_n: int | None = 0) -> None:
        try:
            if try_n > 0:
                await asyncio.sleep(try_n * 10)
            await self._start(try_n)
        except Exception:
            self.log.exception("Failed to start")

    async def _start(self, try_n: int | None = 0) -> None:
        if self.started:
            self.log.warning("Ignoring start() call to started client")
            return
        try:
            await self.client.versions()
            whoami = await self.client.whoami()
        except MatrixInvalidToken as e:
            self.log.error(f"Invalid token: {e}. Disabling client")
            self.enabled = False
            await self.update()
            return
        except Exception as e:
            if try_n >= 8:
                self.log.exception("Failed to get /account/whoami, disabling client")
                self.enabled = False
                await self.update()
            else:
                self.log.warning(
                    f"Failed to get /account/whoami, retrying in {(try_n + 1) * 10}s: {e}"
                )
                _ = asyncio.create_task(self.start(try_n + 1))
            return
        if whoami.user_id != self.id:
            self.log.error(f"User ID mismatch: expected {self.id}, but got {whoami.user_id}")
            self.enabled = False
            await self.update()
            return
        elif whoami.device_id and self.device_id and whoami.device_id != self.device_id:
            self.log.error(
                f"Device ID mismatch: expected {self.device_id}, but got {whoami.device_id}"
            )
            self.enabled = False
            await self.update()
            return
        if not self.filter_id:
            self.filter_id = await self.client.create_filter(
                Filter(
                    room=RoomFilter(
                        timeline=RoomEventFilter(
                            limit=50,
                            lazy_load_members=True,
                        ),
                        state=StateFilter(
                            lazy_load_members=True,
                        ),
                    ),
                    presence=EventFilter(
                        not_types=[EventType.PRESENCE],
                    ),
                )
            )
            await self.update()
        # if self.crypto:
        #     await self._start_crypto()
        self.start_sync()
        self.started = True
        self.log.info("Client started")

    def start_sync(self) -> None:
        if self.sync:
            self.start(self.filter_id)

    def stop_sync(self) -> None:
        self.stop()

    async def stop(self) -> None:
        if self.started:
            self.started = False
            await self.stop_plugins()
            self.stop_sync()

    async def clear_cache(self) -> None:
        self.stop_sync()
        self.filter_id = FilterID("")
        self.next_batch = SyncToken("")
        await self.update()
        self.start_sync()

    async def handle_invite(self, evt: StrippedStateEvent) -> None:
        if evt.state_key == self.id and evt.content.membership == Membership.INVITE:
            await self.client.join_room(evt.room_id)

    async def handle_message(self, evt: StrippedStateEvent) -> None:
        # Ignore bot messages
        if evt.sender in self.config["users_ignore"] or evt.sender == evt.client.mxid:
            return

        try:
            user = await User.get_by_user_id(user_id=evt.sender)
            user.flow = self.menu
        except Exception as e:
            self.log.exception(e)
            return

        if not user:
            return

        await self.algorithm(user=user, evt=evt)

    async def algorithm(self, user: User, evt: MessageEvent) -> None:
        """If the user is in the input state, then set the variable to the user's input,
        and if the node has an output connection, then update the menu to the output connection.
        Otherwise, run the node and update the menu to the output connection.
        If the node is an input node and the user is not in the input state,
        then show the message and update the menu to the node's id and set the state to input.
        If the node is a message node, then show the message and if the node has an output connection,
        then update the menu to the output connection and run the algorithm again

        Parameters
        ----------
        user : User
            User - the user object
        evt : MessageEvent
            The event that triggered the algorithm.

        Returns
        -------
            The return value is the result of the last expression in the function body.

        """

        # This is the case where the user is in the input state.
        # In this case, the variable is set to the user's input, and if the node has an output connection,
        # then the menu is updated to the output connection.
        # Otherwise, the node is run and the menu is updated to the output connection.
        if user.state == "input":
            await user.set_variable(user.node.variable, evt.content.body)

            if user.node.o_connection:
                await user.update_menu(context=user.node.o_connection)
            else:
                o_connection = await user.node.run(user=user)
                await user.update_menu(context=o_connection)

        # This is the case where the user is not in the input state and the node is an input node.
        # In this case, the message is shown and the menu is updated to the node's id and the state is set to input.
        if user.node.type == "input" and user.state != "input":
            await user.node.show_message(
                variables=user._variables, room_id=evt.room_id, client=evt.client
            )
            self.log.debug(f"Input {user.node}")
            await user.update_menu(context=user.node.id, state="input")
            return

        # Showing the message and updating the menu to the output connection.
        if user.node.type == "message":
            await user.node.show_message(
                variables=user._variables, room_id=evt.room_id, client=evt.client
            )
            self.log.debug(f"Message {user.node}")

            if user.node.o_connection is None:
                return

            await user.update_menu(context=user.node.o_connection)

        if user.node.type == "http_request":
            self.log.debug(f"HTTPRequest {user.node}")

            await user.node.request(user=user, session=evt.client.api.session)

        await self.algorithm(user=user, evt=evt)

    async def delete(self) -> None:
        try:
            del self.cache[self.id]
        except KeyError:
            pass
        await super().delete()

    @classmethod
    async def all(cls) -> AsyncGenerator[MenuClient, None]:
        users = await super().all()
        user: cls
        for user in users:
            try:
                yield cls.cache[user.id]
            except KeyError:
                user.postinit()
                yield user
