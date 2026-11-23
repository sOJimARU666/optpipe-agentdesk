"""Repository hygiene boundaries exercised in disposable, local Git repositories."""
from pathlib import Path
import subprocess

import pytest

import test_repo_hygiene as hygiene


def git(root, *args, data=None):
    return subprocess.run(["git", "-C", str(root), *args], input=data, capture_output=True, check=False)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    ignore = (hygiene.ROOT / ".gitignore").read_bytes()
    root = tmp_path / "repo"
    root.mkdir()
    assert git(root, "init", "-q").returncode == 0
    (root / ".gitignore").write_bytes(ignore)
    monkeypatch.setattr(hygiene, "ROOT", root)
    return root


def put(root, name, text, *, tracked=False):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if tracked:
        assert git(root, "add", "-f", "--", name).returncode == 0
    return path


@pytest.mark.parametrize("name", ["settings with spaces.txt", "line\nbreak.txt", "設定.txt"])
@pytest.mark.parametrize("tracked", [False, True], ids=["new", "tracked"])
def test_complete_git_names_are_scanned(repo, name, tracked):
    if "\n" in name and __import__("os").name == "nt":
        pytest.skip("Windows filenames cannot contain a newline")
    sentinel = "ghp_" + "a1B2" * 6
    put(repo, name, sentinel + "\n", tracked=tracked)
    assert name in {n for n, _ in hygiene._files()}
    with pytest.raises(AssertionError):
        hygiene.test_no_secrets_or_tokens()


def test_credential_diagnostics_never_echo_the_matched_value(repo):
    sentinel = "ghp_" + "c3D4" * 6
    put(repo, "credential-note.txt", sentinel + "\n", tracked=True)
    with pytest.raises(AssertionError) as caught:
        hygiene.test_no_secrets_or_tokens()
    diagnostic = str(caught.value)
    assert "credential-note.txt" in diagnostic
    assert sentinel not in diagnostic
    assert "c3D4" * 3 not in diagnostic


@pytest.mark.parametrize("name", [".env", ".env.local", "nested/.ENV.production", "private.pem", "signing.key", "id_ed25519"])
def test_local_credential_files_are_ignored(repo, name):
    put(repo, name, "local-only fixture\n")
    result = git(repo, "check-ignore", "--stdin", "-z", data=name.encode("utf-8") + b"\0")
    assert result.returncode == 0
    assert result.stdout == name.encode("utf-8") + b"\0"


@pytest.mark.parametrize("name", [".env", ".env.local", ".env.example.local", "nested/.ENV.production",
                                  "signing.key", "identity.pem", "private.p12", "identity.pfx", "id_ed25519"])
def test_force_tracked_credential_files_fail_even_with_harmless_content(repo, name):
    put(repo, name, "harmless fixture bytes\n", tracked=True)
    with pytest.raises(AssertionError):
        hygiene.test_no_secrets_or_tokens()


def test_exact_empty_environment_template_is_allowed(repo):
    name = "nested/.env.example"
    put(repo, name, '# Populate a local ignored file, not this template.\nAPP_MODE=\nAPI_KEY=""\nPASSWORD=\'\'\n', tracked=True)
    assert git(repo, "check-ignore", "--no-index", "--", name).returncode == 1
    hygiene.test_no_secrets_or_tokens()


def test_environment_template_exception_cannot_carry_populated_values(repo):
    put(repo, ".env.example", "APP_MODE=paper\n", tracked=True)
    with pytest.raises(AssertionError):
        hygiene.test_no_secrets_or_tokens()


@pytest.mark.parametrize("key", ["OPENAI_API_KEY", "DB_PASSWORD", "AWS_SECRET_ACCESS_KEY", "AUTH_TOKEN", "api_key"])
def test_unquoted_environment_style_credentials_are_rejected(repo, key):
    sentinel = "synthetic" + "Credential" + "42"
    put(repo, "configuration.txt", "export " + key + "=" + sentinel + "\n", tracked=True)
    with pytest.raises(AssertionError) as caught:
        hygiene.test_no_secrets_or_tokens()
    assert sentinel not in str(caught.value)


def test_empty_assignments_and_nonsecret_configuration_do_not_trigger(repo):
    put(repo, "configuration.txt", "API_KEY=\nPASSWORD=\"\"\nAPP_MODE=paper\nPORT=8765\n", tracked=True)
    hygiene.test_no_secrets_or_tokens()
