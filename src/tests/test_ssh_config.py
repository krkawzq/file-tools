import pytest

from file_tools import SshClient


def test_ssh_constructor_validates_scalar_connection_fields() -> None:
    with pytest.raises(ValueError, match="host is required"):
        SshClient("", port=22, username="user")
    with pytest.raises(ValueError, match="user is required"):
        SshClient("host", port=22, username="")
    with pytest.raises(ValueError, match="port"):
        SshClient("host", port=0, username="user")
