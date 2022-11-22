from __future__ import annotations

from typing import Dict, Tuple

from aiohttp import BasicAuth, ClientSession
from aiohttp.client_exceptions import ContentTypeError
from attr import dataclass, ib
from jinja2 import Template
from mautrix.util.config import RecursiveDict
from ruamel.yaml.comments import CommentedMap

from ..jinja.jinja_template import jinja_env
from .switch import Switch


@dataclass
class HTTPRequest(Switch):
    method: str = ib(default=None, metadata={"json": "method"})
    url: str = ib(default=None, metadata={"json": "url"})
    variables: Dict = ib(metadata={"json": "variables"}, factory=dict)
    cookies: Dict = ib(metadata={"json": "cookies"}, factory=dict)
    query_params: Dict = ib(metadata={"json": "query_params"}, factory=dict)
    headers: Dict = ib(metadata={"json": "headers"}, factory=dict)
    basic_auth: Dict = ib(metadata={"json": "basic_auth"}, factory=dict)
    data: Dict = ib(metadata={"json": "data"}, factory=dict)

    @property
    def _headers(self) -> Dict[str, Template]:
        return {header: jinja_env.from_string(self.headers[header]) for header in self.headers}

    @property
    def _auth(self) -> Dict[str, Template]:
        return {
            item: jinja_env.from_string(self.basic_auth[item]) for item in self.basic_auth.__dict__
        }

    @property
    def _query_params(self) -> Dict:
        return {
            query_param: jinja_env.from_string(self.query_params[query_param])
            for query_param in self.query_params
        }

    @property
    def _data(self) -> Dict:

        if self.data:
            if not isinstance(self.data, list):
                return {
                    value: jinja_env.from_string(self.data[value]) for value in self.data.__dict__
                }

            for item in self.data:
                new_dict = {}
                for value in item.__dict__:
                    if not item[value]:
                        continue

                    new_dict[value] = jinja_env.from_string(item[value])

            return new_dict

    @property
    def _url(self) -> Template:
        return jinja_env.from_string(self.url)

    def _render(self, templates: Dict[str, Template], variables: Dict) -> Dict:
        if not templates:
            return
        try:
            return {item: templates[item].render(**variables) for item in templates}
        except Exception as e:
            self.log.exception(e)

    async def request(self, session: ClientSession) -> Tuple(int, str):

        request_body = {}

        if self.query_params:
            request_body["params"] = self._render(self._query_params, self.user._variables)

        if self.basic_auth:
            request_body["auth"] = BasicAuth(
                self._render(self._auth, self.user._variables)["login"],
                self._render(self._auth, self.user._variables)["password"],
            )

        if self.headers:
            request_body["auth"] = self._render(self._headers, self.user._variables)

        if self.data:
            request_body["json"] = self._render(self._data, self.user._variables)

        response = await session.request(
            self.method, self._url.render(**self.user._variables), **request_body
        )

        variables = {}
        o_connection = None

        if self.cookies:
            for cookie in self.cookies.__dict__:
                variables[cookie] = response.cookies.output(cookie)

        self.log.debug(
            f"node: {self.id} method: {self.method} url: {self.url} status: {response.status}"
        )

        # Tulir and its magic since time immemorial
        try:
            response_data = RecursiveDict(CommentedMap(**await response.json()))
        except ContentTypeError:
            response_data = {}

        if self.variables:
            for variable in self.variables.__dict__:
                try:
                    variables[variable] = response_data[self.variables[variable]]
                except KeyError:
                    pass

        if self.cases:
            o_connection = await self.get_case_by_id(id=str(response.status))

        if o_connection:
            await self.user.update_menu(
                node_id=o_connection, state="end" if not self.cases else None
            )

        if variables:
            await self.user.set_variables(variables=variables)

        return response.status, await response.text()
