from types import SimpleNamespace

from app.db.bootstrap import _restore_gitee_tokens


def test_placeholder_token_is_removed_when_deployment_token_is_missing(monkeypatch):
    runtime = SimpleNamespace(
        REPOSITORY_TOKENS={"repo-1": "demo-token-not-returned"},
        REPOSITORIES={
            "repo-1": SimpleNamespace(
                token_configured=True,
                token_alias="gitee-prod-token",
            )
        },
    )
    monkeypatch.delenv("GITEE_TOKEN", raising=False)

    _restore_gitee_tokens(runtime, [{"id": "repo-1"}])

    assert runtime.REPOSITORY_TOKENS == {}
    assert runtime.REPOSITORIES["repo-1"].token_configured is False
    assert runtime.REPOSITORIES["repo-1"].token_alias is None


def test_deployment_token_replaces_placeholder(monkeypatch):
    runtime = SimpleNamespace(
        REPOSITORY_TOKENS={"repo-1": "demo-token-not-returned"},
        REPOSITORIES={
            "repo-1": SimpleNamespace(
                token_configured=True,
                token_alias="gitee-prod-token",
            )
        },
    )
    monkeypatch.setenv("GITEE_TOKEN", "real-token-from-env")

    _restore_gitee_tokens(runtime, [{"id": "repo-1"}])

    assert runtime.REPOSITORY_TOKENS == {"repo-1": "real-token-from-env"}
    assert runtime.REPOSITORIES["repo-1"].token_configured is True
