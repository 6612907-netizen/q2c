"""版本号的**唯一真源**。

为什么单独一枚文件（2026-10-04）：`pyproject.toml` 与 `q2c/__init__.py` 原先各写了一遍
`0.1.0`。那是双真源——发版时改一处忘一处，PyPI 上标的版本和代码里跑的版本就不是同一个，
而 `q2c version` 打出来的还是"看着对"的那一个，没人会发现问题。

现在 `pyproject.toml` 用 `[tool.setuptools.dynamic] version = {attr = "q2c._version.__version__"}`
**从这一行取**，不再自己写。取的是字面量（setuptools 走 AST 读，不 import），
所以这个文件必须只有一行赋值、不许 import 别的东西——
`tests/test_packaging.py::test_dynamic_version_resolves_to_the_one_literal` 钉的就是这个形状。

协议版本不在这里：`q2c/1` 由 `q2c/protocol.py::PROTOCOL_VERSION` 单独管（两者编号独立，
见 PROTOCOL.md §9 与 CHANGELOG 抬头那句）。
"""

__version__ = "0.1.1"
