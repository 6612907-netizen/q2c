#!/usr/bin/env python3
"""配置：`<home>/config.json` ＋ 环境变量覆盖。

q2c 不持有凭据（SECURITY.md §5）：配置里**不许**出现 token／密钥／Cookie，
`doctor` 会扫出来点名。凭据永远由对侧 CLI 自己管，q2c 只问一句"能不能无人值守完成一次最小认证"。

未知键不致命也不静默：记进 `unknown_keys` 由 `q2c doctor` 报出来。
未知**枚举取值**（适配器名／状态／消息类型）才是硬拒——那是行为契约，不是装饰。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from . import security

DEFAULTS = {
    "delivery_attempts_max": 2,
    "receive_timeout_s": 600,
    "default_receiver_adapter": "loopback",
    "default_sender_adapter": "loopback",
    "allowed_workspaces": [],
    "allow_refs_outside_home": False,
    "adapters": {},
}

CONFIG_FILE = "config.json"

SECRETISH = ("token", "secret", "password", "api_key", "apikey", "cookie", "credential")


class ConfigError(Exception):
    side_effects = 0

    def __init__(self, code, detail=""):
        super().__init__("%s%s" % (code, (": " + detail) if detail else ""))
        self.code = code
        self.detail = detail


@dataclass
class Config:
    home: str
    values: dict = field(default_factory=lambda: dict(DEFAULTS))
    unknown_keys: list = field(default_factory=list)
    source: str = ""

    def __getitem__(self, k):
        return self.values[k]

    def get(self, k, default=None):
        return self.values.get(k, default)

    def adapter_config(self, name: str) -> dict:
        cfg = dict(self.values.get("adapters", {}).get(name, {}) or {})
        cfg.setdefault("home", self.home)
        env_over = os.environ.get("Q2C_%s_CMD" % name.upper(), "").strip()
        if env_over:
            cfg["cmd"] = env_over
        probe = os.environ.get("Q2C_CRED_PROBE", "").strip()
        if probe:
            cfg["cred_probe"] = probe
        env = dict(os.environ)
        env.update({"Q2C_HOME": self.home})
        cfg["env"] = env
        return cfg


def path_for(home: str) -> str:
    return os.path.join(home, CONFIG_FILE)


def load(home: str | None = None) -> Config:
    h = security.real_home(home)
    cfg = Config(home=h)
    p = path_for(h)
    if os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as fh:
                doc = json.loads(fh.read() or "{}")
        except ValueError as exc:
            raise ConfigError("CONFIG_UNREADABLE", "%s（%s）" % (p, exc))
        except OSError as exc:
            raise ConfigError("CONFIG_UNREADABLE", "%s（%s）" % (p, type(exc).__name__))
        if not isinstance(doc, dict):
            raise ConfigError("CONFIG_UNREADABLE", "%s 顶层不是对象" % p)
        cfg.source = p
        for k, v in doc.items():
            if k in DEFAULTS:
                cfg.values[k] = v
            else:
                cfg.unknown_keys.append(k)
        _refuse_secrets(doc, p)
    # 环境变量覆盖（只覆盖这几枚，且只在显式设置时）
    if os.environ.get("Q2C_DELIVERY_ATTEMPTS_MAX", "").strip():
        cfg.values["delivery_attempts_max"] = _int("Q2C_DELIVERY_ATTEMPTS_MAX")
    if os.environ.get("Q2C_RECEIVE_TIMEOUT_S", "").strip():
        cfg.values["receive_timeout_s"] = _float("Q2C_RECEIVE_TIMEOUT_S")
    if os.environ.get("Q2C_ALLOW_OUTSIDE_REFS", "").strip() == "1":
        cfg.values["allow_outside_refs"] = True
    if not isinstance(cfg.values["delivery_attempts_max"], int) or cfg.values["delivery_attempts_max"] < 0:
        raise ConfigError("BAD_DELIVERY_ATTEMPTS_MAX",
                          repr(cfg.values["delivery_attempts_max"]))
    return cfg


def _refuse_secrets(doc, p):
    """配置文件里出现凭据形状 ⇒ 拒绝加载（不是"忽略那一项"，是整份不认）。

    为什么这么硬：q2c 不持有凭据是产品边界。若允许"顺手在这儿放个 token"，
    下一版就会有人写代码去读它，再下一版它就会出现在日志里。
    """
    hits = []
    for k in doc:
        if any(t in str(k).lower() for t in SECRETISH):
            hits.append(k)
    if hits:
        raise ConfigError("CONFIG_WOULD_HOLD_SECRETS",
                          "%s 里这些键看着像凭据：%s。q2c 不持有凭据，请交回对侧 CLI 自己管"
                          % (p, ",".join(sorted(hits))))


def _int(name):
    try:
        return int(os.environ[name].strip())
    except ValueError:
        raise ConfigError("BAD_ENV_INTEGER", "%s=%r" % (name, os.environ.get(name)))


def _float(name):
    try:
        return float(os.environ[name].strip())
    except ValueError:
        raise ConfigError("BAD_ENV_NUMBER", "%s=%r" % (name, os.environ.get(name)))


def write_default(home: str) -> str:
    p = path_for(home)
    if os.path.exists(p):
        raise ConfigError("CONFIG_EXISTS", p)
    os.makedirs(home, exist_ok=True)
    security.atomic_write(p, json.dumps(DEFAULTS, ensure_ascii=False, indent=1) + "\n")
    return p
