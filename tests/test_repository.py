import httpx
import pytest

from app.services.repository import GitHubRepositoryClient, parse_github_pr_url


def test_parse_github_pull_request_url():
    assert parse_github_pr_url("https://github.com/acme/shop/pull/42") == (
        "acme",
        "shop",
        42,
    )
    with pytest.raises(ValueError):
        parse_github_pr_url("https://example.com/acme/shop/pull/42")


@pytest.mark.asyncio
async def test_github_client_reads_and_normalizes_pull_request_diff():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/pulls/7"):
            return httpx.Response(
                200,
                json={
                    "title": "Change order status",
                    "base": {"sha": "base123"},
                    "head": {"sha": "head456"},
                },
            )
        return httpx.Response(
            200,
            json=[
                {
                    "filename": "src/OrderService.java",
                    "status": "modified",
                    "additions": 2,
                    "deletions": 1,
                    "patch": "@@ -1 +1 @@\n-public void old() {}\n+public void createOrder() {}",
                    "raw_url": "https://example.test/raw",
                }
            ],
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    repository = GitHubRepositoryClient(api_url="https://api.github.test", client=client)
    snapshot = await repository.pull_request("https://github.com/acme/shop/pull/7")
    await client.aclose()

    assert snapshot.title == "Change order status"
    assert snapshot.files[0].filename == "src/OrderService.java"
    assert "+++ b/src/OrderService.java" in snapshot.diff
    assert "+public void createOrder()" in snapshot.diff
