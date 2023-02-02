from __future__ import annotations

import asyncio
from datetime import datetime
from typing import List

from attr import dataclass, ib
from jinja2 import Template
from mautrix.types import SerializableAttrs

from ..matrix import MatrixClient
from ..utils.util import Util
from .message import Message
from .switch import Case, Switch


@dataclass
class InactivityOptions(SerializableAttrs):
    chat_timeout: int = ib(default=None, metadata={"json": "chat_timeout"})
    warning_message: str = ib(default=None, metadata={"json": "warning_message"})
    time_between_attempts: int = ib(default=None, metadata={"json": "time_between_attempts"})
    attempts: int = ib(default=None, metadata={"json": "attempts"})
    closing_message: str = ib(default=None, metadata={"json": "closing_message"})


@dataclass
class Input(Switch, Message):
    """
    ## Input

    An input type node allows sending a message formatted with jinja variables
    and capturing the response to transit to another node according to the validation.

    content:

    ```
    - id: i1
      type: input
      text: 'Enter a number'
      variable: opt
      validation: '{{ opt.isdigit() }}'
      cases:
      - id: true
        o_connection: m1
      - id: false
        o_connection: m2
      - id: default
        o_connection: m3
    ```
    """

    variable: str = ib(default=None, metadata={"json": "variable"})
    cases: List[Case] = ib(metadata={"json": "cases"}, factory=list)
    inactivity_options: InactivityOptions = ib(
        default=None, metadata={"json": "inactivity_options"}
    )

    @property
    def _inactivity_message(self) -> Template:
        return self.render_data(self.inactivity_options.warning_message)

    @property
    def _closing_message(self) -> Template:
        return self.render_data(self.inactivity_options.closing_message)

    async def inicativity_task(self, client: MatrixClient):
        """It spawns a task to harass the client to enter information to input option

        Parameters
        ----------
        client : MatrixClient
            The MatrixClient object

        """
        self.log.debug(f"Inactivity loop starts in room: {self.room.room_id}")
        asyncio.create_task(self.timeout_active_chats(client=client), name=self.room.room_id)

    async def timeout_active_chats(self, client: MatrixClient):
        """It sends messages in time intervals to communicate customer
        that not entered information to input option.

        Parameters
        ----------
        client : MatrixClient
            The Matrix client object.

        """

        try:
            # wait the given time to start the task
            await asyncio.sleep(self.inactivity_options.chat_timeout)
        except Exception as e:
            self.log.debug(f"{e}")

        count = 0
        while True:
            self.log.debug(f"Inactivity loop: {datetime.now()} -> {self.room.room_id}")
            if self.inactivity_options.attempts == count:
                self.log.debug(f"INACTIVITY TRIES COMPLETED -> {self.room.room_id}")
                if self.inactivity_options.closing_message:
                    await self.show_message(client=client, message=self._closing_message)
                await self.room.update_menu(node_id="start", state=None)
                break

            await self.show_message(client=client, message=self._inactivity_message)
            await asyncio.sleep(self.inactivity_options.time_between_attempts)
            count += 1

    async def cancel_inactivity_task(self):
        """It cancels the inactivity task that is running in the background"""

        task = await Util.get_tasks_by_name(self.room.room_id)
        if task:
            task.cancel()
            self.log.debug(f"TASK CANCEL -> {self.room.room_id}")
