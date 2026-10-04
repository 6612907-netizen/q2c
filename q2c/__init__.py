"""q2c — 可靠的 AI 编码 Agent 交接桥（协议 q2c/1；版本号只在 q2c/_version.py 一处）。

产品边界（PROTOCOL.md §0）：q2c 保证**交接**，不保证**结果**。
本包只含传输事实：投递／关联／幂等／送达确认／重投／会话映射／适配器／产物引用／
传输恢复／通信跟踪／传输安全。工作流状态、项目调度、CI 真相、代码质量判断、
评审批准、放行决定一律不在本包（分类依据见仓库根 Q2C-BOUNDARY-AUDIT.md）。
"""

from .protocol import (  # noqa: F401
    PROTOCOL_VERSION,
    ENVELOPE_FIELDS,
    MESSAGE_TYPES,
    TRANSPORT_STATES,
    TERMINAL_STATES,
    FAILURE_CLASSES,
    ARTIFACT_TYPES,
    FORBIDDEN_VALUES,
    ProtocolError,
    Envelope,
    ArtifactRef,
    make_request,
    next_state,
)

from ._version import __version__  # 唯一真源在 q2c/_version.py（打包从这里取，别再写一遍）

__all__ = ["__version__", "PROTOCOL_VERSION"]
