import sys
from types import SimpleNamespace

import file_tools.client.factory as factory_module
from file_tools.client.ssh import SshClient
from file_tools.client.ssh import flags_to_paramiko_options, parse_ssh_flags


def test_parse_ssh_flags() -> None:
    assert parse_ssh_flags("-X -A") == ("-X", "-A")
    assert parse_ssh_flags(["-Y", "-C"]) == ("-Y", "-C")
    opts = flags_to_paramiko_options(("-X", "-A", "-C"))
    assert opts["enable_x11"] is True
    assert opts["allow_agent"] is True
    assert opts["compress"] is True
    opts_y = flags_to_paramiko_options(("-Y",))
    assert opts_y["x11_trusted"] is True


class _RejectPolicy:
    pass


class _AutoAddPolicy:
    pass


class _FakeSftp:
    def normalize(self, path: str) -> str:
        return "/home/user"

    def close(self) -> None:
        pass


class _FakeParamikoClient:
    instances: list["_FakeParamikoClient"] = []

    def __init__(self) -> None:
        self.loaded_host_keys = False
        self.policy: object | None = None
        self.closed = False
        self.instances.append(self)

    def load_system_host_keys(self) -> None:
        self.loaded_host_keys = True

    def set_missing_host_key_policy(self, policy: object) -> None:
        self.policy = policy

    def connect(self, **kwargs: object) -> None:
        self.connect_kwargs = kwargs

    def open_sftp(self) -> _FakeSftp:
        return _FakeSftp()

    def get_transport(self) -> SimpleNamespace:
        return SimpleNamespace(is_active=lambda: True)

    def close(self) -> None:
        self.closed = True


def _fake_paramiko_module() -> SimpleNamespace:
    return SimpleNamespace(
        SSHClient=_FakeParamikoClient,
        RejectPolicy=_RejectPolicy,
        AutoAddPolicy=_AutoAddPolicy,
        AuthenticationException=type("AuthenticationException", (Exception,), {}),
    )


def test_ssh_rejects_unknown_host_keys_by_default(monkeypatch) -> None:
    _FakeParamikoClient.instances.clear()
    monkeypatch.setitem(sys.modules, "paramiko", _fake_paramiko_module())

    client = SshClient(
        "host",
        port=22,
        username="user",
        look_for_keys=False,
        allow_password_prompt=False,
    )
    raw = _FakeParamikoClient.instances[-1]

    assert raw.loaded_host_keys
    assert isinstance(raw.policy, _RejectPolicy)
    client.close()


def test_ssh_unknown_host_key_requires_explicit_opt_in(monkeypatch) -> None:
    _FakeParamikoClient.instances.clear()
    monkeypatch.setitem(sys.modules, "paramiko", _fake_paramiko_module())

    client = SshClient(
        "host",
        port=22,
        username="user",
        look_for_keys=False,
        allow_password_prompt=False,
        accept_unknown_host_key=True,
    )

    assert isinstance(_FakeParamikoClient.instances[-1].policy, _AutoAddPolicy)
    client.close()


def test_ssh_cache_closes_clients_and_does_not_store_plaintext_password(
    monkeypatch,
) -> None:
    class FakeCachedSshClient:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs
            self.is_active = True
            self.closed = False

        def close(self) -> None:
            self.closed = True

    factory_module.clear_client_cache()
    monkeypatch.setattr(factory_module, "SshClient", FakeCachedSshClient)
    first = factory_module.get_client(
        client="ssh",
        cwd="/work",
        ssh_host="host",
        ssh_port=22,
        ssh_user="user",
        ssh_password="plaintext-secret",
        allow_password_prompt=False,
    )
    second = factory_module.get_client(
        client="ssh",
        cwd="/work",
        ssh_host="host",
        ssh_port=22,
        ssh_user="user",
        ssh_password="plaintext-secret",
        allow_password_prompt=False,
    )

    assert first is second
    assert "plaintext-secret" not in repr(tuple(factory_module._SSH_CACHE))
    factory_module.clear_client_cache()
    assert first.closed
