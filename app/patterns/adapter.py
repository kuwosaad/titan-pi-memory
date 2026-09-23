"""Lightweight forwarding boundary for optional pattern features."""

from .schemas import (
    PatternApplicationCreateRequest,
    PatternApplicationOutcomeRequest,
    PatternCreateRequest,
    PatternEvidencePacketRequest,
    PatternMarkProcessedRequest,
)


def _api():
    from . import api

    return api


def get_pattern_status() -> dict:
    return _api().get_pattern_status()


def get_pattern_capabilities() -> dict:
    return _api().get_pattern_capabilities()


def list_patterns(**kwargs) -> dict:
    return _api().list_patterns(**kwargs)


def get_pattern(pattern_id: str) -> dict:
    return _api().get_pattern(pattern_id)


def create_pattern(req: PatternCreateRequest) -> dict:
    return _api().create_pattern(req)


def get_evidence_packet(req: PatternEvidencePacketRequest) -> dict:
    return _api().get_evidence_packet(req)


def accept_pattern(pattern_id: str) -> dict:
    return _api().accept_pattern(pattern_id)


def reject_pattern(pattern_id: str) -> dict:
    return _api().reject_pattern(pattern_id)


def restore_pattern(pattern_id: str) -> dict:
    return _api().restore_pattern(pattern_id)


def record_pattern_application(pattern_id: str, req: PatternApplicationCreateRequest) -> dict:
    return _api().record_pattern_application(pattern_id, req)


def list_pattern_applications(**kwargs) -> dict:
    return _api().list_pattern_applications(**kwargs)


def update_pattern_application(application_id: str, req: PatternApplicationOutcomeRequest) -> dict:
    return _api().update_pattern_application(application_id, req)


def mark_processed(req: PatternMarkProcessedRequest) -> dict:
    return _api().mark_processed(req)


def export_pattern_bundle(**kwargs) -> dict:
    from .bundle import export_pattern_bundle as export_pattern_bundle_impl

    return export_pattern_bundle_impl(**kwargs)


def import_pattern_bundle(bundle: dict, **kwargs) -> dict:
    from .bundle import import_pattern_bundle as import_pattern_bundle_impl

    return import_pattern_bundle_impl(bundle, **kwargs)


def build_pattern_graph(*, limit: int = 500) -> str:
    from .graph import build_pattern_graph as build_pattern_graph_impl

    return build_pattern_graph_impl(limit=limit)


def build_pattern_graph_data(*, limit: int = 500) -> dict:
    from .graph import build_pattern_graph_data as build_pattern_graph_data_impl

    return build_pattern_graph_data_impl(limit=limit)
