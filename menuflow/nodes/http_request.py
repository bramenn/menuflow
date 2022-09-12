from __future__ import annotations

from typing import Dict, List

from aiohttp import ClientSession
from attr import dataclass, ib
from ruamel.yaml.comments import CommentedMap

from mautrix.util.config import RecursiveDict

from .input import Case
from .node import Node


@dataclass
class Response:
    cases: List[Case] = ib(metadata={"json": "cases"}, factory=list)


@dataclass
class HTTPRequest(Node):
    method: str = ib(metadata={"json": "method"})
    url: str = ib(metadata={"json": "url"})
    response: Response = ib(default=None, metadata={"json": "response"})
    variables: Dict = ib(metadata={"json": "variables"}, factory=dict)
    query_params: Dict = ib(metadata={"json": "query_params"}, factory=dict)
    headers: Dict = ib(metadata={"json": "headers"}, factory=dict)
    data: Dict = ib(metadata={"json": "data"}, factory=dict)

    # async fuking_list(self)

    async def request(self, session: ClientSession) -> None:

        self.log.debug(self.variables)

        response = await session.request(
            self.method, self.url, headers=self.headers, params=self.query_params, data=self.data
        )

        # Tulir and its magic since time immemorial
        response_data = RecursiveDict(CommentedMap(**await response.json()))

        self.log.debug(response_data[self.variables["headers"]])
        self.log.debug(response_data[self.variables["origin"]])
        self.log.debug(response_data[self.variables["url"]])

        # for variable in self.variables:
