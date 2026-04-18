from __future__ import annotations

from fastapi import APIRouter, Request

from ..auth import CurrentAdmin, resolve_client_ip
from ..schemas import (
    AssistantActionRequestCreateRequest,
    AssistantApprovalRecordCreateRequest,
    AssistantChangeRecordCreateRequest,
    AssistantRequestDetailEnvelope,
    AssistantRequestListResponse,
    AssistantRequestStatusUpdateRequest,
    AssistantTriageDraftCreateRequest,
)
from ..services.assistant_service import (
    create_assistant_action_request,
    create_assistant_approval_record,
    create_assistant_change_record,
    create_assistant_triage_draft,
    get_admin_assistant_request_detail,
    list_admin_assistant_requests,
    update_assistant_request_status,
)


router = APIRouter(prefix="/api/admin/assistant", tags=["admin-assistant"])


@router.get("/requests", response_model=AssistantRequestListResponse)
def admin_assistant_request_list(request: Request, user=CurrentAdmin) -> AssistantRequestListResponse:
    del user
    return AssistantRequestListResponse(
        requests=list_admin_assistant_requests(request.app.state.settings)
    )


@router.get("/requests/{request_id}", response_model=AssistantRequestDetailEnvelope)
def admin_assistant_request_detail(
    request_id: int,
    request: Request,
    user=CurrentAdmin,
) -> AssistantRequestDetailEnvelope:
    del user
    return AssistantRequestDetailEnvelope(
        request=get_admin_assistant_request_detail(request.app.state.settings, request_id=request_id)
    )


@router.patch("/requests/{request_id}/status", response_model=AssistantRequestDetailEnvelope)
def admin_assistant_update_request_status(
    request_id: int,
    payload: AssistantRequestStatusUpdateRequest,
    request: Request,
    user=CurrentAdmin,
) -> AssistantRequestDetailEnvelope:
    updated = update_assistant_request_status(
        request.app.state.settings,
        request_id=request_id,
        status_value=payload.status,
        admin_note=payload.admin_note,
        actor=user,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return AssistantRequestDetailEnvelope(request=updated)


@router.post("/requests/{request_id}/triage-drafts", response_model=AssistantRequestDetailEnvelope)
def admin_assistant_create_triage_draft(
    request_id: int,
    payload: AssistantTriageDraftCreateRequest,
    request: Request,
    user=CurrentAdmin,
) -> AssistantRequestDetailEnvelope:
    updated = create_assistant_triage_draft(
        request.app.state.settings,
        request_id=request_id,
        created_by=payload.created_by,
        model_provider=payload.model_provider,
        model_name=payload.model_name,
        summary=payload.summary,
        classification=payload.classification,
        risk_level=payload.risk_level,
        confidence_level=payload.confidence_level,
        possible_duplicate_request_ids=payload.possible_duplicate_request_ids,
        suggested_next_step=payload.suggested_next_step,
        suggested_owner=payload.suggested_owner,
        needs_admin_approval=payload.needs_admin_approval,
        needs_external_access_approval=payload.needs_external_access_approval,
        reversibility_impact_if_action_taken=payload.reversibility_impact_if_action_taken,
        notes_for_admin=payload.notes_for_admin,
        actor=user,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return AssistantRequestDetailEnvelope(request=updated)


@router.post("/requests/{request_id}/action-requests", response_model=AssistantRequestDetailEnvelope)
def admin_assistant_create_action_request(
    request_id: int,
    payload: AssistantActionRequestCreateRequest,
    request: Request,
    user=CurrentAdmin,
) -> AssistantRequestDetailEnvelope:
    updated = create_assistant_action_request(
        request.app.state.settings,
        request_id=request_id,
        triage_draft_id=payload.triage_draft_id,
        created_by_type=payload.created_by_type,
        action_type=payload.action_type,
        target_scope=payload.target_scope,
        reason=payload.reason,
        proposed_plan=payload.proposed_plan,
        risk_level=payload.risk_level,
        requires_admin_approval=payload.requires_admin_approval,
        requires_external_access_approval=payload.requires_external_access_approval,
        reversibility_level=payload.reversibility_level,
        warning_if_not_fully_reversible=payload.warning_if_not_fully_reversible,
        status_value=payload.status,
        actor=user,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return AssistantRequestDetailEnvelope(request=updated)


@router.post("/action-requests/{action_request_id}/approval-records", response_model=AssistantRequestDetailEnvelope)
def admin_assistant_create_approval_record(
    action_request_id: int,
    payload: AssistantApprovalRecordCreateRequest,
    request: Request,
    user=CurrentAdmin,
) -> AssistantRequestDetailEnvelope:
    updated = create_assistant_approval_record(
        request.app.state.settings,
        action_request_id=action_request_id,
        decision=payload.decision,
        decision_note=payload.decision_note,
        backup_required=payload.backup_required,
        rollback_plan_required=payload.rollback_plan_required,
        external_access_approved=payload.external_access_approved,
        actor=user,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return AssistantRequestDetailEnvelope(request=updated)


@router.post("/requests/{request_id}/change-records", response_model=AssistantRequestDetailEnvelope)
def admin_assistant_create_change_record(
    request_id: int,
    payload: AssistantChangeRecordCreateRequest,
    request: Request,
    user=CurrentAdmin,
) -> AssistantRequestDetailEnvelope:
    updated = create_assistant_change_record(
        request.app.state.settings,
        request_id=request_id,
        linked_action_request_id=payload.linked_action_request_id,
        created_by_type=payload.created_by_type,
        change_summary=payload.change_summary,
        reversibility_level=payload.reversibility_level,
        backup_reference=payload.backup_reference,
        revert_recipe_draft=payload.revert_recipe_draft,
        verification_plan_draft=payload.verification_plan_draft,
        status_value=payload.status,
        actor=user,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return AssistantRequestDetailEnvelope(request=updated)
