from typing import Any, Dict, List, Literal, Mapping, Optional
from pydantic import BaseModel, Field, model_validator


class Message(BaseModel):
    role: str = Field(..., description="Message role: 'user' or 'assistant'")
    content: str = Field(..., description="Message content")
    ts: Optional[str] = Field(None, description="ISO timestamp")
    turn: Optional[int] = Field(None, description="Turn number in conversation")


class Memory(BaseModel):
    id: str = Field(..., description="Unique memory ID")
    text: str = Field(..., description="Memory text content")
    type: Optional[str] = Field(None, description="Memory type label")
    stream: Literal["rough", "learnings"] = Field("rough", description="Memory stream bucket")
    embedding: Optional[List[float]] = Field(None, description="Stored embedding vector")
    ts: str = Field(..., description="ISO timestamp")
    session_id: str = Field(..., description="Source session ID")
    turn: int = Field(..., description="Turn number")
    scene_id: Optional[str] = Field(None, description="Parent scene ID for the interaction that produced this memory")
    provenance: Dict[str, str] = Field(..., description="Original user/assistant messages")
    source_event_ids: List[str] = Field(default_factory=list, description="Trace event lineage")
    source_agent: Optional[str] = Field(None, description="Agent namespace that owns this memory")
    source_type: str = Field("unknown", description="Source: user|assistant|code|mixed")
    source_reliability: float = Field(0.5, description="Reliability score 0.0-1.0")
    verification_status: str = Field("unverified", description="unverified|verified|rejected")
    verification_method: Optional[str] = Field(None, description="How verification was performed")
    speaker_focus: Optional[Literal["kuwo", "karu", "shared", "system"]] = Field(
        None, description="Primary perspective captured by the memory"
    )
    memory_kind: Optional[
        Literal["user_fact", "user_preference", "task", "decision", "commitment", "outcome", "relationship", "workflow", "issue"]
    ] = Field(None, description="High-level memory category for readable recall")
    h: float = Field(0.0, description="Current activation of this memory-neuron (evolves during retrieval)")
    tau: float = Field(0.5, description="Time constant controlling decay rate (0.05-0.95, adapts over time)")
    incoming_weights: Optional[Dict[str, float]] = Field(None, description="Synaptic weights from other memories into this one")
    outgoing_weights: Optional[Dict[str, float]] = Field(None, description="Synaptic weights from this memory to other memories")


class Session(BaseModel):
    id: str = Field(..., description="Unique session ID")
    created_at: str = Field(..., description="ISO timestamp of creation")
    messages: List[Message] = Field(default_factory=list, description="Conversation messages")


class SceneMessage(BaseModel):
    role: Literal["user", "assistant", "system"] = Field(..., description="Message role inside the scene")
    content: str = Field(..., description="Message content")
    message_id: Optional[str] = Field(None, description="Optional message identifier from the source trace")
    event_id: Optional[str] = Field(None, description="Optional source event identifier")


class SceneToolCall(BaseModel):
    name: str = Field(..., description="Tool name")
    call_id: Optional[str] = Field(None, description="Optional source tool call identifier")
    status: str = Field("unknown", description="Compact status such as success, error, or unknown")
    summary: str = Field("", description="Short human-readable summary of the tool call")
    file_paths: List[str] = Field(default_factory=list, description="Relevant file paths mentioned by the tool call")
    excerpt: Optional[str] = Field(None, description="Small excerpt of useful output, never the full raw output")
    event_id: Optional[str] = Field(None, description="Optional source event identifier")


EvidenceStatus = Literal["complete", "partial"]


class SceneReference(BaseModel):
    """The lightweight address returned by memory search for a scene."""

    scene_id: str = Field(..., description="Scene identifier used to fetch full evidence")
    evidence_status: EvidenceStatus = Field("partial", description="Whether scene evidence is complete or partial")
    evidence_version: int = Field(0, description="Version of the scene evidence contract")
    missing_source_event_ids: List[str] = Field(
        default_factory=list,
        description="Source event IDs that could not be recovered for this scene",
    )


class Scene(BaseModel):
    scene_id: str = Field(..., description="Unique scene ID")
    session_id: str = Field(..., description="Session the scene belongs to")
    turn: int = Field(..., description="Turn number assigned when the scene was saved")
    kind: Literal["message_exchange", "trace_packet", "raw_event"] = Field(..., description="Scene source type")
    scene_seq: Optional[int] = Field(None, description="Lossless scene order inside the source session")
    start_event_seq: Optional[int] = Field(None, description="First trace ledger sequence included in this scene")
    end_event_seq: Optional[int] = Field(None, description="Last trace ledger sequence included in this scene")
    anchor_event_id: Optional[str] = Field(None, description="Primary event that anchored scene creation")
    source_event_ids: List[str] = Field(default_factory=list, description="All known source events for this scene")
    raw_events: List[Dict[str, Any]] = Field(default_factory=list, description="Lossless raw events included in this scene chunk")
    evidence_version: int = Field(0, description="Version of the durable scene evidence contract")
    evidence_status: EvidenceStatus = Field("partial", description="Whether durable scene evidence is complete or partial")
    missing_source_event_ids: List[str] = Field(
        default_factory=list,
        description="Source event IDs that were expected but could not be recovered",
    )
    messages: List[SceneMessage] = Field(default_factory=list, description="Ordered messages that make up the scene")
    tool_calls: List[SceneToolCall] = Field(default_factory=list, description="Compact tool calls that happened inside the scene")
    extraction_user_text: str = Field(..., description="User-side text passed into the extractor")
    extraction_assistant_text: str = Field(..., description="Assistant-side text passed into the extractor")
    used_context_fallback: bool = Field(False, description="Whether approximate user context fallback was used")
    ts: str = Field(..., description="Scene timestamp")


    @model_validator(mode="after")
    def validate_evidence_contract(self) -> "Scene":
        validate_scene_evidence_payload(self.model_dump(mode="python"))
        return self


def validate_scene_evidence_payload(scene: Mapping[str, Any]) -> None:
    """Validate the invariants required before a scene can claim complete v1 evidence."""

    try:
        evidence_version = int(scene.get("evidence_version", 0) or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("evidence_version must be an integer") from exc
    evidence_status = str(scene.get("evidence_status") or "partial")
    if evidence_version < 0:
        raise ValueError("evidence_version cannot be negative")
    if evidence_status not in {"complete", "partial"}:
        raise ValueError("evidence_status must be 'complete' or 'partial'")
    if evidence_version == 0:
        if evidence_status != "partial":
            raise ValueError("legacy v0 scenes must remain partial")
        return
    if evidence_status != "complete":
        return
    if evidence_version != 1:
        raise ValueError("only evidence version 1 can be marked complete")

    scene_id = str(scene.get("scene_id") or "").strip()
    session_id = str(scene.get("session_id") or "").strip()
    source_event_ids = [str(value).strip() for value in scene.get("source_event_ids") or []]
    missing_event_ids = [str(value).strip() for value in scene.get("missing_source_event_ids") or []]
    raw_events = scene.get("raw_events") or []
    if not scene_id or not session_id:
        raise ValueError("complete v1 scenes require scene_id and session_id")
    if missing_event_ids:
        raise ValueError("complete v1 scenes cannot contain missing source event IDs")
    if not source_event_ids:
        raise ValueError("complete v1 scenes require source_event_ids")
    if not isinstance(raw_events, list) or len(raw_events) != len(source_event_ids):
        raise ValueError("complete v1 scenes require one raw event for every source event ID")

    raw_event_ids: List[str] = []
    raw_event_seqs: List[int] = []
    for raw_event in raw_events:
        if not isinstance(raw_event, Mapping):
            raise ValueError("complete v1 raw events must be objects")
        event_id = str(raw_event.get("event_id") or "").strip()
        if not event_id:
            raise ValueError("complete v1 raw events require event_id")
        if event_id in raw_event_ids:
            raise ValueError("complete v1 raw events cannot contain duplicate event IDs")
        try:
            event_seq = int(raw_event.get("seq"))
        except (TypeError, ValueError) as exc:
            raise ValueError("complete v1 raw events require integer seq values") from exc
        if event_seq <= 0:
            raise ValueError("complete v1 raw event seq values must be positive")
        raw_session_id = str(raw_event.get("session_id") or "").strip()
        if raw_session_id != session_id:
            raise ValueError("complete v1 raw events must belong to the scene session")
        raw_event_ids.append(event_id)
        raw_event_seqs.append(event_seq)

    if source_event_ids != raw_event_ids:
        raise ValueError("source_event_ids must exactly match ordered raw event IDs")
    if raw_event_seqs != sorted(raw_event_seqs) or len(set(raw_event_seqs)) != len(raw_event_seqs):
        raise ValueError("complete v1 raw events must be strictly ordered by seq")
    if str(scene.get("anchor_event_id") or "").strip() not in raw_event_ids:
        raise ValueError("complete v1 anchor_event_id must identify a raw event")
    if int(scene.get("start_event_seq") or 0) != raw_event_seqs[0]:
        raise ValueError("start_event_seq must match the first complete v1 raw event")
    if int(scene.get("end_event_seq") or 0) != raw_event_seqs[-1]:
        raise ValueError("end_event_seq must match the last complete v1 raw event")

    for message in scene.get("messages") or []:
        if not isinstance(message, Mapping):
            raise ValueError("complete v1 messages must be objects")
        message_event_id = str(message.get("event_id") or "").strip()
        if not message_event_id or message_event_id not in raw_event_ids:
            raise ValueError("complete v1 messages require source event provenance")
    for tool_call in scene.get("tool_calls") or []:
        if not isinstance(tool_call, Mapping):
            raise ValueError("complete v1 tool calls must be objects")
        tool_event_id = str(tool_call.get("event_id") or "").strip()
        if not tool_event_id or tool_event_id not in raw_event_ids:
            raise ValueError("complete v1 tool calls require source event provenance")


class TraceToolCall(BaseModel):
    name: str = Field(..., description="Tool name")
    args: Dict[str, Any] = Field(default_factory=dict, description="Tool arguments")
    result: Optional[Any] = Field(None, description="Tool result/output")


class TracePacketRequest(BaseModel):
    goal: str = Field(..., description="Task goal")
    thoughts: Optional[str] = Field(None, description="Agent reasoning or plan")
    tool_calls: List[TraceToolCall] = Field(default_factory=list, description="Tool calls")
    outcome: str = Field(..., description="Task outcome")
    session_id: Optional[str] = Field(None, description="Optional session ID")
    event_id: Optional[str] = Field(None, description="Optional dedupe event ID")
    save_intent: Optional[bool] = Field(None, description="Explicit intent to store memories")
    intent_phrase: Optional[str] = Field(None, description="Exact phrase that triggered saving")
    context: Optional[Dict[str, Any]] = Field(None, description="Extra context like repo, recent turns")


class TracePacketResponse(BaseModel):
    session_id: str = Field(..., description="Session ID")
    memory_status: str = Field(..., description="Memory pipeline status")
    recap: str = Field(..., description="Short recap of stored memories")
    stored: Optional[bool] = Field(None, description="Whether memories were stored")
    store_reason: Optional[str] = Field(None, description="Reason memories were skipped")


class TraceEvent(BaseModel):
    session_id: str = Field(..., description="Session the event belongs to")
    event_id: str = Field(..., description="Stable dedupe event id")
    event_type: str = Field(..., description="user_message|assistant_message|tool_call|file_edit|trace_packet")
    ts: Optional[str] = Field(None, description="ISO timestamp")
    payload: Dict[str, Any] = Field(default_factory=dict, description="Event payload")
    schema_version: str = Field("v1", description="Schema version for forward compatibility")


class IngestResult(BaseModel):
    status: Literal["ingested", "duplicate", "error"] = Field(..., description="Ingest status")
    session_id: str = Field(..., description="Session ID")
    event_id: str = Field(..., description="Event ID")
    message: str = Field(..., description="Ingest response message")
    seq: Optional[int] = Field(None, description="Ledger sequence if ingested")


class MemoriesResponse(BaseModel):
    memories: List[Memory] = Field(..., description="Memory records")
    count: int = Field(..., description="Total memory count")
