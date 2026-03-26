# Approval Requests API

## Purpose

First-class control-plane authority for mobile/operator approval flows.

## Endpoints

### `GET /api/approval-requests`

Query params:

| Name | Type | Required | Description |
| --- | --- | --- | --- |
| `project_id` | string | No | Filter by project |
| `status` | string | No | `pending`, `approved`, `rejected`, `cancelled` |
| `external_request_id` | string | No | Filter by originating external request |
| `limit` | integer | No | Max rows, default `50` |

### `GET /api/approval-requests/{approval_id}`

Returns one approval request.

### `POST /api/approval-requests`

Creates a pending approval request.

### `POST /api/approval-requests/{approval_id}/decision`

Records a decision.

Example body:

```json
{
  "decision": "approve",
  "decided_by": "owner-mobile",
  "decision_comment": "Approved from phone"
}
```

### `POST /api/approval-requests/batch-decision`

Records the same decision across multiple pending approvals.

Example body:

```json
{
  "approval_ids": ["apr-001", "apr-002"],
  "decision": "approve",
  "decided_by": "owner-mobile",
  "decision_comment": "Approved from phone",
  "bundle_label": "release-bundle",
  "minimum_required": 2,
  "stop_on_error": false
}
```

## Notes

- approval decisions are persisted as audit trail, not ephemeral callbacks
- approval events emit unified events for Observability consumers
- external channels may create approval requests directly or through
  `external_requests` materialization
- batch decisions de-duplicate repeated ids and report failed ids separately
- `minimum_required` enables threshold-based approval bundles
- `bundle_label` gives mobile and voice channels a stable bundle identifier
