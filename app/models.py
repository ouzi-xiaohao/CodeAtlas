from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class SourceType(StrEnum):
    CODE = "code"
    DOCUMENT = "document"
    OPENAPI = "openapi"
    DDL = "ddl"
    ISSUE = "issue"
    COMMIT = "commit"
    TEST = "test"


class KnowledgeChunk(BaseModel):
    id: str
    repository: str
    team: str = "default"
    source_type: SourceType
    path: str
    symbol: str | None = None
    content: str
    line_start: int = 1
    line_end: int = 1
    metadata: dict[str, Any] = Field(default_factory=dict)


class Citation(BaseModel):
    source_type: SourceType
    label: str
    uri: str
    repository: str
    line_start: int | None = None
    line_end: int | None = None
    excerpt: str | None = None


class RetrievalHit(BaseModel):
    chunk: KnowledgeChunk
    score: float
    dense_rank: int | None = None
    sparse_rank: int | None = None


class GraphNode(BaseModel):
    id: str
    kind: str
    name: str
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    source: str
    relation: str
    target: str
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphPath(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class QueryRequest(BaseModel):
    question: str = Field(min_length=2, max_length=4000)
    repositories: list[str] = Field(default_factory=list)
    teams: list[str] = Field(default_factory=list)
    top_k: int = Field(default=8, ge=1, le=30)


class QueryResponse(BaseModel):
    answer: str
    citations: list[Citation]
    related_paths: list[GraphPath] = Field(default_factory=list)
    trace_id: str
    warnings: list[str] = Field(default_factory=list)


class ChangeInputType(StrEnum):
    PULL_REQUEST = "pull_request"
    COMMIT = "commit"
    DESCRIPTION = "description"
    DIFF = "diff"


class ImpactRequest(BaseModel):
    input_type: ChangeInputType
    value: str = Field(min_length=1, max_length=100_000)
    repository: str


class PullRequestFile(BaseModel):
    filename: str
    status: str
    additions: int = 0
    deletions: int = 0
    patch: str = ""
    raw_url: str | None = None


class PullRequestSnapshot(BaseModel):
    provider: str = "github"
    owner: str
    repository: str
    number: int
    title: str
    url: str
    base_sha: str
    head_sha: str
    files: list[PullRequestFile]
    diff: str


class PullRequestUrlRequest(BaseModel):
    url: str = Field(pattern=r"^https://github\.com/[^/]+/[^/]+/pull/\d+/?$")


class ImpactItem(BaseModel):
    kind: str
    name: str
    reason: str
    path: list[str] = Field(default_factory=list)


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ImpactResponse(BaseModel):
    summary: str
    changed_symbols: list[str]
    affected: list[ImpactItem]
    historical_issues: list[ImpactItem]
    suggested_tests: list[ImpactItem]
    risk_level: RiskLevel
    risk_reasons: list[str]
    citations: list[Citation]
    trace_id: str


class IngestRequest(BaseModel):
    repository: str
    paths: list[str] = Field(default_factory=list)
    team: str = "default"


class IngestResponse(BaseModel):
    repository: str
    indexed_chunks: int
    indexed_entities: int
    skipped_files: int


class UserContext(BaseModel):
    user_id: str
    teams: set[str] = Field(default_factory=lambda: {"default"})
    repositories: set[str] = Field(default_factory=set)


class AuditEvent(BaseModel):
    action: str
    actor: str
    resource: str
    allowed: bool
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ConfirmationRequest(BaseModel):
    action: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,80}$")
    resource: str = Field(min_length=1, max_length=500)


class ConfirmationResponse(BaseModel):
    token: str
    expires_in: int
