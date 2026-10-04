#!/usr/bin/env python3
"""传输安全：路径与工作区限制、凭据边界、安全进程启动、脱敏、安全日志。

这一模块里的每条规矩都对应一次真出过的事故或一条真被复现过的指控，
出处记在 Q2C-BOUNDARY-AUDIT.md（§A-6、§A-7）。三条最硬的：

1. **q2c 不持有凭据**。不读钥匙串、不复制 token、不把密钥写进任何文件；
   只回答"这一侧能不能无人值守完成一次最小认证调用"，且**判不出来按不可用**。
2. **子进程必须自成进程组，且到点整组清掉**。只杀叶子会留下孙子进程继续写，
   这条在实验根里真炸过（孤儿/嵌套子进程那组回归）。
3. **写盘前先过脱敏**。事件流与回执原件都可能出现"半句话是凭据"的形状；
   trace 是通信事实的记录，不是凭据的仓库。
"""

from __future__ import annotations

import errno
import os
import re
import signal
import subprocess
import sys
import time

# 会被"看一眼就知道是凭据"的形状。注意这是**兜底**，不是"有它就能随便打印原文"的许可。
SECRET_PATTERNS = (
    (re.compile(r"(?i)\b(authorization)\b\s*[:=]\s*\S+"), r"\1: <redacted>"),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-]{8,}"), r"\1 <redacted>"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}"), "sk-<redacted>"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AKIA<redacted>"),
    (re.compile(r"(?i)\b((?:api[_-]?key|secret|token|password|passwd|cookie)\b\s*[:=]\s*)[^\s,;]+"),
     r"\1<redacted>"),
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"), "ghp_<redacted>"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "xox?-<redacted>"),
    (re.compile(r"eyJ[A-Za-z0-9_\-]{12,}\.[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-]{4,}"), "<jwt-redacted>"),
)

# 凭据类文件名：日志里连路径都不该整条带走
SECRET_FILENAMES = (
    "credentials", "credential", "token", "tokens", "secret", "secrets",
    "cookie", "cookies", ".netrc", "id_rsa", "id_ed25519", "auth.json",
    "secrets.json", "keychain",
)

MAX_LOG_CHARS = 2000


def redact(text) -> str:
    """把已知凭据形状换成占位符。**只在写盘与回显前调用一次**，调用方不许拿它当免死金牌。

    非字符串输入按 repr 走一遍再截断：事件流里塞进 dict 是常见事故形状。
    """
    if text is None:
        return ""
    s = text if isinstance(text, str) else repr(text)
    for pat, rep in SECRET_PATTERNS:
        s = pat.sub(rep, s)
    if len(s) > MAX_LOG_CHARS:
        s = s[:MAX_LOG_CHARS] + "…<truncated %d>" % (len(s) - MAX_LOG_CHARS)
    return s


def contains_secret_shape(text) -> bool:
    """只做"有没有凭据形状"的判定，**不返回内容**。用于日志前的开关。"""
    if not isinstance(text, str):
        return False
    return any(p.search(text) for p, _ in SECRET_PATTERNS)


def looks_like_credential_path(path: str) -> bool:
    low = (path or "").lower()
    base = os.path.basename(low)
    if any(tok in low for tok in ("keychain", "/.ssh/", "/library/application support")):
        return True
    return any(base == t or t in base.split(".") for t in SECRET_FILENAMES)


# ---------------------------------------------------------------------------
# 工作区与路径
# ---------------------------------------------------------------------------


class PathError(Exception):
    """路径/工作区不合格 ⇒ 零副作用拒绝。"""

    side_effects = 0

    def __init__(self, code, detail=""):
        super().__init__("%s%s" % (code, (": " + detail) if detail else ""))
        self.code = code
        self.detail = detail


def real_home(home: str | None = None) -> str:
    """q2c 的根。默认 `~/.q2c`，可用 `Q2C_HOME` 指到别处（测试一律显式给）。

    realpath 而不是 abspath：macOS 上 /var 是 /private/var 的软链，
    临时目录下的副本会被读成两枚不同的根（实验根里 K25 就是这么假红的）。
    """
    h = (home or os.environ.get("Q2C_HOME", "") or os.path.join(os.path.expanduser("~"), ".q2c"))
    return os.path.realpath(os.path.expanduser(h))


def within(root: str, path: str) -> bool:
    """`path` 是否在 `root` 之内（含 root 自身）。用分解后的路径段比，不用 startswith：
    `/tmp/a` 是 `/tmp/ab` 的字符串前缀，但不是它内部。"""
    try:
        r = os.path.realpath(root)
        p = os.path.realpath(path)
        rel = os.path.relpath(p, r)
    except (ValueError, OSError):
        return False
    return rel == "." or (not rel.startswith("..") and os.path.sep not in rel.split(".", 1)[0])


def check_ref_path(field: str, value: str, home: str, allow_outside: bool = False) -> str:
    """校验 workspace_ref / repo_ref / 产物 ref 三类路径字段。

    规则（SECURITY.md §4）：
      · 空值放行（这些字段是可选的）；
      · 非绝对路径拒绝（相对路径会被按某个不确定的 cwd 解读）；
      · 含 `..` 段拒绝；
      · 指向凭据类文件拒绝；
      · 默认必须落在 q2c 根之内，`allow_outside=True` 时只查前三条。
    """
    if not value:
        return ""
    if not os.path.isabs(value):
        raise PathError("REF_NOT_ABSOLUTE", "%s=%r 必须是绝对路径" % (field, value))
    if looks_like_credential_path(value):
        raise PathError("REF_IS_CREDENTIAL_PATH", "%s=%r 指向凭据类位置" % (field, value))
    raw_parts = os.path.expanduser(value).split(os.path.sep)
    if ".." in raw_parts:
        # 必须在解析**之前**查：realpath 会把 `..` 吃掉，解析后再找就永远查不到，
        # 于是"穿越"这一类全靠运气拦住。
        raise PathError("REF_HAS_DOTDOT", "%s=%r 含 .. 段" % (field, value))
    norm = os.path.realpath(os.path.expanduser(value))
    if ".." in norm.split(os.path.sep):
        raise PathError("REF_HAS_DOTDOT", "%s=%r 解析后仍有 .. 段" % (field, value))
    if not allow_outside and not within(home, norm):
        raise PathError("REF_OUTSIDE_HOME",
                        "%s=%r 不在 q2c 根 %s 内（要放开：配置 Q2C_ALLOW_OUTSIDE_REFS=1）"
                        % (field, value, home))
    return norm


def workspace_allowed(workspace_ref: str, allowed: list | None) -> bool:
    """工作区白名单：配置里没有它 ⇒ 这条交接不派发。"""
    if not allowed:
        return False
    w = os.path.realpath(os.path.expanduser(workspace_ref or ""))
    return any(within(os.path.realpath(os.path.expanduser(a)), w) for a in allowed if a)


# ---------------------------------------------------------------------------
# 凭据边界
# ---------------------------------------------------------------------------

# 探针名单：只回答"能不能无人值守完成一次最小认证调用"。
# 硬规矩（继承实验根 K76 那一族，六条）：
#   ①不读任何钥匙串/Secret/Token/密码/Cookie；
#   ②不调用任何会弹界面的取密钥命令；
#   ③不改钥匙串、不重置默认钥匙串、不自动修登录；
#   ④只回 rc／耗时／分类；
#   ⑤判不出来（含超时）一律按不可用；
#   ⑥stdout 直接丢弃——凭据连"进内存再落原件"的机会都没有。
CRED_PROBE_DEFAULTS = {
    "codex": ["/bin/sh", "-c", "command -v codex >/dev/null 2>&1 && codex login status"],
    "qoder": ["/bin/sh", "-c", "command -v qoderclicn >/dev/null 2>&1 && qoderclicn --version"],
}

CRED_READING_COMMANDS = (
    "dump-keychain", "find-generic-password", "find-internet-password",
    "security -g", "security find-", "pbpaste",
)

READY = "READY"
CREDENTIAL_UNAVAILABLE = "CREDENTIAL_UNAVAILABLE"
UNKNOWN_KIND = "UNKNOWN_KIND"


def probe_credential(kind: str, env: dict | None = None) -> tuple:
    """返回 (verdict, note)。未知 kind ⇒ UNKNOWN_KIND（fail-closed，不当 codex 处理）。"""
    env = env if env is not None else os.environ
    try:
        timeout = float(env.get("Q2C_CRED_TIMEOUT", "") or 25)
    except ValueError:
        timeout = 25.0
    probe = (env.get("Q2C_CRED_PROBE", "") or "").strip()
    if probe:
        argv = ["/bin/sh", "-c", probe.replace("$KIND", kind)]
    else:
        argv = CRED_PROBE_DEFAULTS.get(kind)
        if argv is None:
            return UNKNOWN_KIND, "kind=%r 不在册（只认 %s）" % (kind, "/".join(sorted(CRED_PROBE_DEFAULTS)))
    for tok in CRED_READING_COMMANDS:
        if tok in " ".join(argv):
            # 探针里含"取密钥"形状的命令＝越界。拒绝执行，不改写成别的近似命令。
            return CREDENTIAL_UNAVAILABLE, "探针含取密钥命令 %r，拒绝执行（q2c 不持有凭据）" % tok
    child_env = strip_runtime_env(dict(env))
    t0 = time.time()
    try:
        q = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=timeout, env=child_env)
    except subprocess.TimeoutExpired:
        return CREDENTIAL_UNAVAILABLE, "探针超过 %gs 未返回＝无法证明可无人值守完成（超时不折成可用）" % timeout
    except Exception as exc:
        return CREDENTIAL_UNAVAILABLE, "探针起不来：%s" % type(exc).__name__
    secs = time.time() - t0
    if q.returncode == 0:
        return READY, "rc=0 用时 %.1fs" % secs
    return CREDENTIAL_UNAVAILABLE, "rc=%s 用时 %.1fs" % (q.returncode, secs)


# Qoder 的 SDK 入口变量会让被无头拉起的子 CLI 要求 stream-json（真事故：叫起来就失败）。
QODER_SDK_ENV_KEYS = (
    "QODER_AGENT_SDK_ENTRYPOINT", "QODER_AGENT_SDK_VERSION", "QODER_SDK_AUTH_PAYLOAD_FILE",
    "QODERCLI_RUNTIME_PACKAGING", "QODER_WORKER_RUNTIME_ASSET_ROOT", "QODER_WORKER_CWD",
)


def strip_runtime_env(env: dict) -> dict:
    for k in QODER_SDK_ENV_KEYS:
        env.pop(k, None)
    return env


# ---------------------------------------------------------------------------
# 安全进程启动 ＋ 进程组清理
# ---------------------------------------------------------------------------


def popen_group(argv, **kw) -> subprocess.Popen:
    """子进程自成进程组（start_new_session），返回 Popen。

    为什么不能省：`shell=True` 起的那条链里，杀掉 shell 只会留下孙子继续写盘。
    实验根里的孤儿/嵌套子进程回归就是这条的代价。
    """
    kw.setdefault("stdin", subprocess.DEVNULL)
    if isinstance(argv, str):
        return subprocess.Popen(argv, shell=True, preexec_fn=os.setsid, **kw)
    return subprocess.Popen(list(argv), preexec_fn=os.setsid, **kw)


def kill_group(pid: int, grace_s: float = 2.0) -> str:
    """整组清理：TERM → 等 grace → KILL。返回处置结果（不静默）。"""
    try:
        pgid = os.getpgid(int(pid))
    except (ProcessLookupError, ValueError, TypeError, OSError):
        return "no-such-process"
    for sig, label in ((signal.SIGTERM, "term"), (signal.SIGKILL, "kill")):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return "already-gone"
        except PermissionError:
            return "permission-denied"
        except OSError as exc:
            if exc.errno == errno.ESRCH:
                return "already-gone"
            return "error:%s" % type(exc).__name__
        deadline = time.time() + grace_s
        while time.time() < deadline:
            try:
                done, _ = os.waitpid(-pgid if False else int(pid), os.WNOHANG)  # noqa: F632
            except ChildProcessError:
                return "reaped-after-%s" % label
            except OSError:
                pass
            try:
                os.kill(int(pid), 0)
            except ProcessLookupError:
                return "gone-after-%s" % label
            time.sleep(0.05)
    return "killed-group"


def group_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (ProcessLookupError, TypeError, ValueError):
        return False
    except PermissionError:
        return True


def pid_state(pid, start_marker: str | None) -> str:
    """pid 三态：alive / gone / unknown（**永不**把 unknown 折成 gone）。

    双判：pid ＋ 启动时刻。PID 会被回收复用，只看 pid 会把"别人的进程"
    认成"我那一腿还在跑"，于是该等的不等、该停的不停。
    读不出来（没权限／ps 不可用）一律 unknown ⇒ 上层必须停手，不许重投。
    """
    if pid in (None, "", 0, "0"):
        return "never_started"
    try:
        pid_i = int(pid)
    except (TypeError, ValueError):
        return "unknown"
    if not group_alive(pid_i):
        return "gone"
    if not start_marker:
        return "unknown"          # 有进程但没登记启动时刻＝认不出是不是它
    now_marker = _proc_start_marker(pid_i)
    if now_marker is None:
        return "unknown"
    if now_marker == start_marker:
        return "alive"
    return "reused"               # pid 在，但不是当初那个进程


def _proc_start_marker(pid: int) -> str | None:
    """取进程启动时刻（ps 的 lstart）。取不到返回 None，由调用方记 unknown。"""
    try:
        p = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=5)
    except Exception:
        return None
    if p.returncode != 0:
        return None
    val = (p.stdout or "").strip()
    return val or None


# ---------------------------------------------------------------------------
# 安全落盘
# ---------------------------------------------------------------------------


def atomic_write(path: str, data: str) -> None:
    """同目录临时件 + os.replace：读者永远只会看到"旧的完整件"或"新的完整件"。

    直接 open(w) 覆盖会让并发读者读到半截台账，半截 JSON 会被下游读成"没有记录"。
    """
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, ".%s.tmp-%d" % (os.path.basename(path), os.getpid()))
    # 0600：载荷是不透明正文，桥必须**逐字**投出去，所以不能在落盘时脱敏
    # （脱了敏就不是同一条消息了）。能做的物理决定是权限：状态件只属于这个用户。
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def refuse_overwrite(path: str, suffix: str = ".failed-") -> str | None:
    """已有原件 ⇒ 改名留住，返回新名字；不在 ⇒ None。

    失败证据不许被下一次成功覆盖（真事故：第一次失败的回执在第二次成功后再也找不回来）。
    """
    if not os.path.isfile(path):
        return None
    stem, ext = os.path.splitext(path)
    k = 1
    while os.path.isfile("%s%s%d%s" % (stem, suffix, k, ext)):
        k += 1
    kept = "%s%s%d%s" % (stem, suffix, k, ext)
    os.replace(path, kept)
    return kept


def safe_argv(cmd: str, home: str, **fmt) -> list:
    """把配置里的命令串解析成 argv，并把**开头那个可执行名**按根解析。

    规则：绝对路径放行；相对名只允许在 `home/bin` 或 PATH 里找到；都找不到 ⇒ 拒绝。
    配置里写 `./evil` 之类的相对路径直指根外，走不到这里（PathError）。
    """
    raw = (cmd or "").strip()
    if not raw:
        raise PathError("EMPTY_COMMAND", "命令串为空")
    try:
        parts = _shlex_split(raw)
    except ValueError as exc:
        raise PathError("BAD_COMMAND", str(exc))
    head, rest = parts[0], parts[1:]
    resolved = resolve_executable(head, home)
    try:
        rest = [x.format(**fmt) for x in rest]
    except (KeyError, IndexError) as exc:
        raise PathError("BAD_TEMPLATE", "命令模板占位符不成立：%s" % exc)
    return [resolved] + rest


def resolve_executable(head: str, home: str) -> str:
    if os.path.isabs(head):
        if not os.path.isfile(head) or not os.access(head, os.X_OK):
            raise PathError("NOT_EXECUTABLE", head)
        return head
    if os.path.sep in head:
        cand = os.path.join(home, head)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return os.path.realpath(cand)
        raise PathError("NOT_EXECUTABLE", head)
    from shutil import which
    found = which(head)
    if not found:
        raise PathError("NOT_ON_PATH", head)
    return found


def _shlex_split(raw: str) -> list:
    import shlex
    return [t for t in shlex.split(raw) if t]


def redact_env_for_log(env: dict) -> list:
    """回显配置时用：只给键名与"是否含凭据形状"，不给值。"""
    out = []
    for k in sorted(env):
        if any(t in k.lower() for t in ("token", "secret", "password", "key", "credential", "cookie")):
            out.append("%s=<redacted>" % k)
        else:
            out.append("%s" % k)
    return out


SHELL_CANDIDATES = ("/bin/zsh", "/bin/bash", "/bin/sh")


def posix_shell() -> str:
    """返回一个**绝对路径**的 POSIX shell；一个都没有 ⇒ PathError（不退回裸 `zsh`）。

    两个理由：
      · 裸名字在受限 PATH 下起不来 ⇒ 捕获件是空的 ⇒ 被读成"对方没回话"（在册收尾项）；
      · 硬写 `/bin/zsh` 让 Linux 容器／最小镜像直接跑不了（GitHub hosted runner 上就撞过）。
    """
    for cand in SHELL_CANDIDATES:
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    raise PathError("NO_POSIX_SHELL",
                    "找不到可用的 shell（试过 %s）；不退回裸名字，那是无声失败"
                    % " ".join(SHELL_CANDIDATES))


def stderr_line(msg: str) -> None:
    sys.stderr.write(redact(msg).rstrip() + "\n")
