from asyncio import AbstractEventLoop

from aiohttp import web

from ..config import Config


def init(cfg: Config, loop: AbstractEventLoop) -> web.Application:
    app = web.Application(loop=loop, client_max_size=100 * 1024 * 1024)
    return app
