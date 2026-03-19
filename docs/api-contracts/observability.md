# Observability API Contract

The system uses **HTTP polling** for real-time updates — there are no WebSocket endpoints. The Progress API provides operation tracking with ETag caching and poll interval hints.

---

## GET /api/progress/{operation_id}

Track a specific operation's progress.

### Path Parameters

| Parameter      | Type   | Required | Description          |
|----------------|--------|----------|----------------------|
| `operation_id` | string | Yes      | The operation UUID   |

### Request Headers

| Header          | Description                           |
|-----------------|---------------------------------------|
| `If-None-Match` | ETag from previous response (caching) |

### Response Headers

| Header           | Description                                               |
|------------------|-----------------------------------------------------------|
| `ETag`           | Hash of current data (RFC 7232)                           |
| `X-Poll-Interval`| `1000` for active operations, `0` for terminal states     |
| `Cache-Control`  | `no-cache, must-revalidate`                               |

### Response Example (Active Operation)

```json
{
  "progress_id": "op-abc123",
  "status": "processing",
  "progress": 45,
  "message": "Crawling page 9 of 20...",
  "timestamp": "2026-03-18T09:15:00Z",
  "type": "crawl"
}
```

### Response Example (Code Extraction)

```json
{
  "progress_id": "op-def456",
  "status": "processing",
  "progress": 70,
  "message": "Extracting code blocks...",
  "timestamp": "2026-03-18T09:20:00Z",
  "type": "processing",
  "completedSummaries": 14,
  "totalSummaries": 20,
  "codeBlocksFound": 37
}
```

### Response Example (Completed)

```json
{
  "progress_id": "op-abc123",
  "status": "completed",
  "progress": 100,
  "message": "Crawl completed successfully",
  "timestamp": "2026-03-18T09:30:00Z",
  "type": "crawl"
}
```

### Operation Statuses

| Status       | Terminal | Description                    |
|--------------|----------|--------------------------------|
| `pending`    | No       | Operation queued               |
| `processing` | No       | Operation in progress          |
| `completed`  | Yes      | Finished successfully          |
| `failed`     | Yes      | Finished with error            |
| `error`      | Yes      | Unexpected error               |
| `cancelled`  | Yes      | Manually cancelled             |

**Stop polling** when status is terminal (`completed`, `failed`, `error`, `cancelled`).

---

## GET /api/progress/

List all active (non-terminal) operations.

### Response Example

```json
{
  "operations": [
    {
      "operation_id": "op-abc123",
      "operation_type": "crawl",
      "status": "processing",
      "progress": 45,
      "message": "Crawling page 9 of 20...",
      "started_at": "2026-03-18T09:00:00Z",
      "source_id": "src-xyz789",
      "url": "https://docs.example.com",
      "current_url": "https://docs.example.com/api/auth",
      "crawl_type": "full",
      "pages_crawled": 9,
      "total_pages": 20,
      "documents_created": 18,
      "code_blocks_found": 12
    }
  ],
  "count": 1,
  "timestamp": "2026-03-18T09:15:00Z"
}
```

### Operation Fields

| Field              | Type   | Present When      | Description                      |
|--------------------|--------|-------------------|----------------------------------|
| `operation_id`     | string | Always            | Unique operation identifier      |
| `operation_type`   | string | Always            | `crawl`, `upload`, `processing`  |
| `status`           | string | Always            | Current status                   |
| `progress`         | int    | Always            | 0-100 percentage                 |
| `message`          | string | Always            | Human-readable status message    |
| `started_at`       | string | Always            | ISO 8601 timestamp               |
| `source_id`        | string | Crawl/upload      | Related knowledge source         |
| `url`              | string | Crawl             | Target URL                       |
| `current_url`      | string | Crawl (active)    | Currently crawling URL           |
| `crawl_type`       | string | Crawl             | Crawl type                       |
| `pages_crawled`    | int    | Crawl             | Pages completed                  |
| `total_pages`      | int    | Crawl             | Total pages expected             |
| `documents_created`| int    | Crawl/upload      | Documents stored                 |
| `code_blocks_found`| int    | Processing        | Code snippets extracted          |

---

## Polling Strategy

The recommended polling pattern for Virtual Office:

1. **Start**: Poll at 1-second intervals (check `X-Poll-Interval` header)
2. **ETag**: Send `If-None-Match` header — server returns `304` if unchanged (~70% bandwidth reduction)
3. **Terminal**: Stop polling when status is terminal
4. **Tab visibility**: Pause polling when browser tab is hidden, resume on focus

### Frontend Implementation

Use TanStack Query with smart polling:

```typescript
const { data } = useQuery({
  queryKey: progressKeys.detail(operationId),
  queryFn: () => progressService.getProgress(operationId),
  refetchInterval: useSmartPolling(1000),  // visibility-aware
  enabled: !!operationId && !isTerminal(data?.status),
});
```

---

## Health Endpoints

### GET /health

Main application health check.

### GET /api/health

API-level health check.

### GET /internal/health

Internal health check (not exposed externally).
