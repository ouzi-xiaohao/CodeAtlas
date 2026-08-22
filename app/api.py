from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from app.container import Container
from app.models import (
    AuditEvent,
    ChangeInputType,
    ConfirmationRequest,
    ConfirmationResponse,
    ImpactRequest,
    ImpactResponse,
    IngestRequest,
    IngestResponse,
    QueryRequest,
    QueryResponse,
    PullRequestSnapshot,
    PullRequestUrlRequest,
    UserContext,
)
from app.security import current_user
from app.security import create_confirmation


router = APIRouter()


def get_container(request: Request) -> Container:
    return request.app.state.container


@router.get("/health")
async def health(container: Container = Depends(get_container)):
    return {
        "status": "ok",
        "mode": "external" if container.settings.enable_external_services else "local",
        "embedding_backend": type(container.retriever.embedder).__name__,
        "reranker_backend": type(container.retriever.reranker).__name__,
        "indexed_chunks": container.retriever.size,
        "graph_nodes": len(container.graph.nodes),
    }


@router.post("/query", response_model=QueryResponse)
async def query(
    payload: QueryRequest,
    user: UserContext = Depends(current_user),
    container: Container = Depends(get_container),
) -> QueryResponse:
    return await container.knowledge_workflow.answer(payload, user)


@router.post("/impact", response_model=ImpactResponse)
async def impact(
    payload: ImpactRequest,
    user: UserContext = Depends(current_user),
    container: Container = Depends(get_container),
) -> ImpactResponse:
    if user.repositories and payload.repository not in user.repositories:
        raise HTTPException(status_code=403, detail="Repository is outside the user's ACL")
    try:
        if payload.input_type.value == "pull_request" and payload.value.startswith(
            "https://github.com/"
        ):
            snapshot = await container.github.pull_request(payload.value)
            payload = payload.model_copy(
                update={"input_type": ChangeInputType.DIFF, "value": snapshot.diff}
            )
        return await container.impact_workflow.analyze(payload, user)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/pull-requests/inspect", response_model=PullRequestSnapshot)
async def inspect_pull_request(
    payload: PullRequestUrlRequest,
    user: UserContext = Depends(current_user),
    container: Container = Depends(get_container),
) -> PullRequestSnapshot:
    try:
        snapshot = await container.github.pull_request(payload.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if user.repositories and snapshot.repository not in user.repositories:
        raise HTTPException(status_code=403, detail="Repository is outside the user's ACL")
    return snapshot


@router.post("/ingest", response_model=IngestResponse)
async def ingest(
    payload: IngestRequest,
    user: UserContext = Depends(current_user),
    container: Container = Depends(get_container),
) -> IngestResponse:
    if payload.team not in user.teams:
        raise HTTPException(status_code=403, detail="Cannot index content for another team")
    if user.repositories and payload.repository not in user.repositories:
        raise HTTPException(status_code=403, detail="Repository is outside the user's ACL")
    try:
        files = container.parser.discover(payload.paths)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    all_chunks, all_nodes, all_edges = [], [], []
    skipped = 0
    changed_paths: set[str] = set()
    for file in files:
        try:
            chunks, nodes, edges = container.parser.parse(file, payload.repository, payload.team)
            all_chunks.extend(chunks)
            all_nodes.extend(nodes)
            all_edges.extend(edges)
            changed_paths.add(file.relative_to(container.parser.root).as_posix())
        except (OSError, UnicodeError, ValueError):
            skipped += 1
    # Delete changed paths first to avoid stale symbols after an incremental re-index.
    await container.retriever.delete_by_repository_paths(payload.repository, changed_paths)
    await container.retriever.upsert(all_chunks)
    await container.graph.upsert(all_nodes, all_edges)
    await container.audit.record(
        AuditEvent(
            action="repository.ingest",
            actor=user.user_id,
            resource=payload.repository,
            allowed=True,
            details={"paths": sorted(changed_paths), "chunks": len(all_chunks)},
        )
    )
    return IngestResponse(
        repository=payload.repository,
        indexed_chunks=len(all_chunks),
        indexed_entities=len(all_nodes),
        skipped_files=skipped,
    )


@router.post("/webhooks/git")
async def git_webhook(
    request: Request,
    x_webhook_token: str = Header(default=""),
    container: Container = Depends(get_container),
):
    """Receive a normalized webhook and incrementally re-index changed paths."""
    expected = container.settings.write_confirmation_secret
    if not x_webhook_token or x_webhook_token != expected:
        raise HTTPException(status_code=403, detail="Invalid webhook token")
    body = await request.json()
    repository = str(body.get("repository", "")).strip()
    paths = [str(Path(path).as_posix()) for path in body.get("changed_paths", [])]
    if not repository or not paths:
        raise HTTPException(status_code=400, detail="repository and changed_paths are required")
    # Reuse the endpoint's core service with a constrained service identity.
    result = await ingest(
        IngestRequest(repository=repository, paths=paths, team=body.get("team", "default")),
        UserContext(user_id="git-webhook", teams={body.get("team", "default")}),
        container,
    )
    return result


@router.get("/audit")
async def audit(
    limit: int = 100,
    user: UserContext = Depends(current_user),
    container: Container = Depends(get_container),
):
    if "platform-admin" not in user.teams:
        raise HTTPException(status_code=403, detail="platform-admin team required")
    return await container.audit.recent(min(limit, 500))


@router.post("/confirmations", response_model=ConfirmationResponse)
async def confirmation(
    payload: ConfirmationRequest,
    x_user_confirmed: str = Header(default="false"),
    user: UserContext = Depends(current_user),
    container: Container = Depends(get_container),
) -> ConfirmationResponse:
    """Trusted UI calls this only after showing operation details to the user."""
    confirmed = x_user_confirmed.lower() == "true"
    await container.audit.record(
        AuditEvent(
            action="write.confirmation",
            actor=user.user_id,
            resource=payload.resource,
            allowed=confirmed,
            details={"requested_action": payload.action},
        )
    )
    if not confirmed:
        raise HTTPException(status_code=428, detail="Explicit user confirmation is required")
    return ConfirmationResponse(
        token=create_confirmation(payload.action, payload.resource, expires_in=300),
        expires_in=300,
    )
