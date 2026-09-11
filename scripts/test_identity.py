"""Tests for how an unnamed commenter is identified.

The rule being pinned down is small but easy to get wrong in a way nobody
notices until a site is full of comments called `192.0.2.7`:

* a blank nickname resolves to the deployment's anonymous name, **never** to the
  visitor's address;
* the address is a separate field, so the policy that decides whether to publish
  it can only be applied at serialization time — once it has been baked into the
  name there is no taking it back.

So the tests are written as the three questions the widget asks the server:
what name is this, is it a chosen one, and would you show the address with it.

Run directly (``python scripts/test_identity.py``) or with pytest.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from settings import DEFAULT_ANONYMOUS_NAME, Settings  # noqa: E402

IP = "203.0.113.7"

passed = 0
failed = 0


def check(label: str, condition: bool, extra: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  [PASS] {label}")
    else:
        failed += 1
        print(f"  [FAIL] {label} {extra}".rstrip())


def _settings(**overrides) -> Settings:
    base = dict(default_author="anonymous", anonymous_name=DEFAULT_ANONYMOUS_NAME)
    base.update(overrides)
    return Settings(**base)


def test_anonymous_is_the_default() -> None:
    print("--- 默认：匿名名字，不暴露地址")
    s = _settings()
    check("留空得到匿名名字", s.display_author("", IP) == "匿名用户", s.display_author("", IP))
    check("不是 IP", s.display_author("", IP) != IP)
    check("被识别为匿名", s.is_anonymous(s.display_author("", IP)))
    check("默认只在匿名时显示 IP", s.shows_author_ip(True) is True)
    check("已署名时不显示 IP", s.shows_author_ip(False) is False)
    check("不预填昵称框", s.suggested_author(IP) == "", repr(s.suggested_author(IP)))
    check("空白字符也算留空", s.display_author("   ", IP) == "匿名用户")
    check("默认匿名名字可读且有内容", bool(DEFAULT_ANONYMOUS_NAME.strip()))


def test_a_chosen_name_wins() -> None:
    print("--- 填了昵称就用昵称")
    s = _settings()
    check("昵称优先", s.display_author("小明", IP) == "小明")
    check("昵称两边空白被去掉", s.display_author("  小明  ", IP) == "小明")
    check("填了昵称就不算匿名", s.is_anonymous("小明") is False)
    # A visitor who literally calls themselves 匿名用户 is anonymous by the only
    # test available to us, which is also the harmless direction.
    check("自称匿名名字则视为匿名", s.is_anonymous("匿名用户") is True)


def test_custom_anonymous_name() -> None:
    print("--- 匿名名字可配置")
    s = _settings(anonymous_name="游客")
    check("留空得到自定义名字", s.display_author("", IP) == "游客")
    check("is_anonymous 跟着改", s.is_anonymous("游客") and not s.is_anonymous("匿名用户"))


def test_fixed_default_nickname() -> None:
    print("--- 也可以指定一个固定默认昵称")
    s = _settings(default_author="站内读者")
    check("留空时用固定昵称", s.display_author("", IP) == "站内读者")
    check("它不是匿名占位", s.is_anonymous("站内读者") is False)
    check("此时不显示 IP", s.shows_author_ip(False) is False)
    # A literal default is a real name the operator chose, so pre-filling it is
    # the intended behaviour rather than the accident it is in the anonymous mode.
    check("会预填昵称框", s.suggested_author(IP) == "站内读者")


def test_legacy_ip_mode() -> None:
    print("--- 旧模式：把 IP 当昵称")
    s = _settings(default_author="ip")
    check("留空得到 IP", s.display_author("", IP) == IP)
    check("预填 IP", s.suggested_author(IP) == IP)
    check("IP 名字不算匿名占位", s.is_anonymous(IP) is False)
    # Not anonymous, so the default policy would withhold the badge — which is
    # right, because the name already is the address.
    check("默认不再重复显示地址", s.shows_author_ip(False) is False)


def test_ip_visibility_policies() -> None:
    print("--- IP 可见性三档")
    always = _settings(show_author_ip="always")
    check("always：匿名也显示", always.shows_author_ip(True) is True)
    check("always：署名也显示", always.shows_author_ip(False) is True)

    never = _settings(show_author_ip="never")
    check("never：匿名也不显示", never.shows_author_ip(True) is False)
    check("never：署名也不显示", never.shows_author_ip(False) is False)
    # Nothing about "never" may leak through the *name* either; that is the
    # reason display_author never returns an address unless asked to.
    check("never：名字里也不含地址", never.display_author("", IP) != IP)

    anonymous = _settings(show_author_ip="anonymous")
    check("anonymous：只对匿名显示", anonymous.shows_author_ip(True) and not anonymous.shows_author_ip(False))


def test_bad_configuration_is_loud() -> None:
    print("--- 非法取值直接报错")
    import os

    import settings as settings_module

    for value, ok in (("always", True), ("NEVER", True), ("anonymous", True), ("sometimes", False), ("", True)):
        os.environ["MKC_SHOW_AUTHOR_IP"] = value
        try:
            settings_module._env_choice("MKC_SHOW_AUTHOR_IP", "anonymous", ("anonymous", "always", "never"))
            raised = False
        except ValueError:
            raised = True
        check(f"取值 {value!r} {'被接受' if ok else '被拒绝'}", raised is not ok)
    # `_env_choice` lower-cases, so an upper-case setting file still works.
    os.environ["MKC_SHOW_AUTHOR_IP"] = "NEVER"
    check("大小写不敏感",
          settings_module._env_choice("MKC_SHOW_AUTHOR_IP", "anonymous", ("anonymous", "always", "never")) == "never")
    os.environ.pop("MKC_SHOW_AUTHOR_IP", None)


def main() -> int:
    test_anonymous_is_the_default()
    print()
    test_a_chosen_name_wins()
    print()
    test_custom_anonymous_name()
    print()
    test_fixed_default_nickname()
    print()
    test_legacy_ip_mode()
    print()
    test_ip_visibility_policies()
    print()
    test_bad_configuration_is_loud()

    print(f"\n{'=' * 46}")
    print(f"结果：{passed} 通过 / {failed} 失败")
    print(f"{'=' * 46}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
