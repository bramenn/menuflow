from mautrix.client import Client as MatrixClient, SyncStream


class MenuFlowMatrixClient(MatrixClient):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
