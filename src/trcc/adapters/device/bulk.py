"""Re-export stub — moved to adapters/transport/adapter_bulk.py."""
from trcc.adapters.transport.adapter_bulk import *  # noqa: F401,F403
from trcc.adapters.transport.adapter_bulk import (  # noqa: F401 — private names for tests
    _HANDSHAKE_PAYLOAD,
    _HANDSHAKE_READ_SIZE,
    _HANDSHAKE_TIMEOUT_MS,
)
