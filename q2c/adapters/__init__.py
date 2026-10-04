"""随包适配器注册表。

用法：`from q2c import adapters; adapters.import_builtin(); adapters.build("codex", cfg)`。
先 `import_builtin()` 再 `build()`，避免"哪个适配器在册"取决于 import 顺序。
"""

from .base import (  # noqa: F401
    Adapter,
    AdapterError,
    Handle,
    CONTRACT_METHODS,
    STATUS_ALIVE,
    STATUS_GONE,
    STATUS_UNKNOWN,
    STATUS_NEVER,
    build,
    known,
    register,
)
from .base import import_builtin  # noqa: F401
