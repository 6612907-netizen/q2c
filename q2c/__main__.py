"""`python3 -m q2c …` 的入口。与 `bin/q2c` 等价。"""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
