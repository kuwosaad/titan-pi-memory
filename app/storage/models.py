from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


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
    messages: List[SceneMessage] = Field(default_factory=list, description="Ordered messages that make up the scene")
    tool_calls: List[SceneToolCall] = Field(default_factory=list, description="Compact tool calls that happened inside the scene")
    extraction_user_text: str = Field(..., description="User-side text passed into the extractor")
    extraction_assistant_text: str = Field(..., description="Assistant-side text passed into the extractor")
    used_context_fallback: bool = Field(False, description="Whether approximate user context fallback was used")
    ts: str = Field(..., description="Scene timestamp")


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
    messages: List[SceneMessage] = Field(default_factory=list, description="Ordered messages that make up the scene")
    tool_calls: List[SceneToolCall] = Field(default_factory=list, description="Compact tool calls that happened inside the scene")
    extraction_user_text: str = Field(..., description="User-side text passed into the extractor")
    extraction_assistant_text: str = Field(..., description="Assistant-side text passed into the extractor")
    used_context_fallback: bool = Field(False, description="Whether approximate user context fallback was used")
    ts: str = Field(..., description="Scene timestamp")


class MemoriesResponse(BaseModel):
    memories: List[Memory] = Field(..., description="Memory records")
    count: int = Field(..., description="Total memory count")
