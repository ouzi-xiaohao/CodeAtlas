from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

from app.models import PullRequestFile, PullRequestSnapshot


GITHUB_PR_PATH = re.compile(r"^/([^/]+)/([^/]+)/pull/(\d+)/?$")


def parse_github_pr_url(url: str) -> tuple[str, str, int]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc.lower() not in {"github.com", "www.github.com"}:
        raise ValueError("Only https://github.com/<owner>/<repo>/pull/<number> is supported")
    match = GITHUB_PR_PATH.match(parsed.path)
    if not match:
        raise ValueError("Invalid GitHub pull request URL")
    owner, repository, number = match.groups()
    return owner, repository.removesuffix(".git"), int(number)


class GitHubRepositoryClient:
    def __init__(
        self,
        api_url: str = "https://api.github.com",
        token: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.token = token
        self._client = client

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
            "User-Agent": "CodeAtlas/0.1",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def pull_request(self, url: str) -> PullRequestSnapshot:
        owner, repository, number = parse_github_pr_url(url)
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=20.0, headers=self._headers())
        try:
            metadata_response = await client.get(
                f"{self.api_url}/repos/{owner}/{repository}/pulls/{number}",
                headers=self._headers(),
            )
            metadata_response.raise_for_status()
            metadata = metadata_response.json()
            files = await self._pull_request_files(client, owner, repository, number)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise ValueError("Pull request not found or GitHub token has no access") from exc
            if exc.response.status_code == 403:
                raise ValueError("GitHub API rate limit or repository permission denied") from exc
            raise ValueError(f"GitHub API returned {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise ValueError(f"Unable to connect to GitHub: {exc}") from exc
        finally:
            if owns_client:
                await client.aclose()

        diff_parts = []
        for item in files:
            diff_parts.extend(
                [
                    f"diff --git a/{item.filename} b/{item.filename}",
                    f"--- a/{item.filename}",
                    f"+++ b/{item.filename}",
                    item.patch,
                ]
            )
        return PullRequestSnapshot(
            owner=owner,
            repository=repository,
            number=number,
            title=str(metadata.get("title", "")),
            url=url,
            base_sha=str(metadata.get("base", {}).get("sha", "")),
            head_sha=str(metadata.get("head", {}).get("sha", "")),
            files=files,
            diff="\n".join(diff_parts),
        )

    async def _pull_request_files(
        self, client: httpx.AsyncClient, owner: str, repository: str, number: int
    ) -> list[PullRequestFile]:
        files: list[PullRequestFile] = []
        for page in range(1, 31):
            response = await client.get(
                f"{self.api_url}/repos/{owner}/{repository}/pulls/{number}/files",
                params={"per_page": 100, "page": page},
                headers=self._headers(),
            )
            response.raise_for_status()
            payload = response.json()
            files.extend(
                PullRequestFile.model_validate({**item, "patch": item.get("patch") or ""})
                for item in payload
            )
            if len(payload) < 100:
                break
        return files
