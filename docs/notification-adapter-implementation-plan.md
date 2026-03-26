# Notification and Digest Delivery Adapter Implementation Plan

## Purpose

This document turns `ADR-0013 Notification and Digest Delivery Adapter` into a concrete implementation plan for `archon-leankit`.

The goal is to refactor the current notification path without breaking the active LeanKit Platform pilot:

- keep current Telegram webhook and digest APIs working
- keep current `channel-smoke.py` flows working
- preserve Observability emission and control-plane authority
- extract formatter, adapter, and channel seams incrementally

## Current State in `archon-leankit`

### Current seam 1: `Notifier` is mixing event emission and delivery concerns

Current implementation:

- `/Users/thanhcl/Development/TrueAI/archon-leankit/python/src/server/services/engine/notifier.py`

Today `Notifier` is responsible for:

- appending `TaskEvent` to the in-memory event log
- publishing task events to the WebSocket bridge
- publishing unified events to Observability
- classifying whether Telegram should send immediately, digest later, or skip
- formatting Telegram message text
- building Telegram inline approval payloads
- building the Telegram daily digest body
- sending Telegram messages directly
- sending Discord webhook messages directly

This is the main refactor driver. The current file is both event authority and delivery implementation.

### Current seam 2: `TelegramChannelService` is both ingress and outbound digest orchestration

Current implementation:

- `/Users/thanhcl/Development/TrueAI/archon-leankit/python/src/server/services/channels/telegram_service.py`

Today `TelegramChannelService` handles:

- inbound Telegram message webhook -> `ExternalRequestService`
- inbound Telegram callback -> `ApprovalRequestService`
- digest preview generation
- digest sending
- due-digest scheduling cycle
- replay collection from Observability
- channel health reporting

It currently instantiates `Notifier` only to reuse Telegram formatting and digest logic. That is a strong signal that formatter logic is living in the wrong place.

### Current API surface that must remain stable during refactor

- `/Users/thanhcl/Development/TrueAI/archon-leankit/python/src/server/api_routes/telegram_api.py`

Current routes:

- `POST /api/channels/telegram/webhook`
- `POST /api/channels/telegram/digest`
- `POST /api/channels/telegram/digest-preview`
- `POST /api/channels/telegram/digest-send`
- `POST /api/channels/telegram/due-digest`
- `POST /api/channels/telegram/due-digest-cycle`
- `GET /api/channels/telegram/health`
- `GET /api/channels/telegram/heartbeat`

These endpoints are already used by pilot tooling and should remain compatible through the initial phases.

## Design Goals

The refactor should achieve the following:

1. Separate event emission from outbound delivery transport.
2. Separate formatting from channel transport.
3. Preserve Telegram as the first supported channel without making it the architecture.
4. Make digest generation reusable by the primary scheduler, due-digest cycle, and future fallback jobs.
5. Preserve best-effort delivery semantics so a delivery failure does not masquerade as a control-plane failure.
6. Make it straightforward to add more channels later without adding more logic to `Notifier`.

## Target Architecture

The target module split inside `archon-leankit` is:

- `Notifier`
  - remains the task-event emission entry point
  - records task events
  - publishes to Observability
  - delegates outbound delivery to an adapter

- `NotificationFormatter`
  - builds canonical notification content
  - builds canonical digest content
  - owns text assembly and summary grouping
  - does not perform HTTP delivery

- `NotificationPolicy`
  - decides immediate vs digest vs skip
  - centralizes routing rules and event allowlists
  - keeps Telegram- and digest-related classification out of `Notifier`

- `NotificationAdapter`
  - fans out one event or digest to configured channels
  - collects per-channel delivery results
  - enforces best-effort semantics

- `TelegramChannel`
  - outbound transport only
  - sends formatted messages and inline markup to Telegram

- `TelegramChannelService`
  - remains the inbound webhook and callback surface
  - remains a health/readiness surface
  - calls formatter/adapter/channel services for digest-related behavior instead of reusing `Notifier`

## Proposed Module Layout

The initial module layout should be:

- `/Users/thanhcl/Development/TrueAI/archon-leankit/python/src/server/services/channels/notification_formatter.py`
- `/Users/thanhcl/Development/TrueAI/archon-leankit/python/src/server/services/channels/notification_policy.py`
- `/Users/thanhcl/Development/TrueAI/archon-leankit/python/src/server/services/channels/notification_adapter.py`
- `/Users/thanhcl/Development/TrueAI/archon-leankit/python/src/server/services/channels/notification_channel.py`
- `/Users/thanhcl/Development/TrueAI/archon-leankit/python/src/server/services/channels/telegram_channel.py`

The existing files should stay in place as façades during migration:

- `/Users/thanhcl/Development/TrueAI/archon-leankit/python/src/server/services/engine/notifier.py`
- `/Users/thanhcl/Development/TrueAI/archon-leankit/python/src/server/services/channels/telegram_service.py`

## Phase Plan

### Phase 1: Extract canonical formatter

**Goal**

Move Telegram text formatting and digest text building out of `Notifier`.

**Primary write set**

- `/Users/thanhcl/Development/TrueAI/archon-leankit/python/src/server/services/engine/notifier.py`
- `/Users/thanhcl/Development/TrueAI/archon-leankit/python/src/server/services/channels/notification_formatter.py`
- `/Users/thanhcl/Development/TrueAI/archon-leankit/python/tests/server/services/engine/test_notifier.py`
- add formatter tests

**Move out of `Notifier`**

- Telegram text formatting logic
- digest text building logic
- shared content formatting helpers

**Keep in `Notifier`**

- event creation
- event log append
- Observability publish
- delegation to outbound delivery

**Acceptance**

- `Notifier` no longer owns digest text construction.
- `TelegramChannelService.build_digest()` no longer instantiates `Notifier` to build digest text.
- Existing digest preview/send endpoints still behave the same.

### Phase 2: Extract outbound Telegram channel transport

**Goal**

Move Telegram HTTP transport out of `Notifier` and reuse the same outbound path from digest sending.

**Primary write set**

- `/Users/thanhcl/Development/TrueAI/archon-leankit/python/src/server/services/engine/notifier.py`
- `/Users/thanhcl/Development/TrueAI/archon-leankit/python/src/server/services/channels/telegram_service.py`
- `/Users/thanhcl/Development/TrueAI/archon-leankit/python/src/server/services/channels/notification_channel.py`
- `/Users/thanhcl/Development/TrueAI/archon-leankit/python/src/server/services/channels/telegram_channel.py`
- `/Users/thanhcl/Development/TrueAI/archon-leankit/python/tests/server/services/channels/test_telegram_service.py`

**Responsibilities**

- `TelegramChannel` sends one outbound Telegram message payload.
- `Notifier` delegates outbound Telegram sends instead of posting directly.
- `TelegramChannelService.send_digest()` uses the same channel transport rather than duplicating Telegram HTTP calls.

**Acceptance**

- Telegram transport is implemented in exactly one place.
- `Notifier` and `TelegramChannelService` both reuse that place.
- Existing webhook and digest routes remain stable.

**Status**

- Implemented on 2026-03-23 through `notification_channel.py` + `telegram_channel.py`, with `Notifier` and `TelegramChannelService` both delegating outbound sends to the shared Telegram transport.

### Phase 3: Extract routing and delivery policy

**Goal**

Move immediate vs digest vs skip classification out of `Notifier`.

**Primary write set**

- `/Users/thanhcl/Development/TrueAI/archon-leankit/python/src/server/services/engine/notifier.py`
- `/Users/thanhcl/Development/TrueAI/archon-leankit/python/src/server/services/channels/notification_policy.py`
- policy-focused tests

**Policy concerns to centralize**

- `telegram_only_critical`
- `telegram_notify_events`
- `telegram_digest_events`
- critical-event override
- digest eligibility

**Acceptance**

- `Notifier` does not contain Telegram routing rules.
- Digest/event routing is centrally testable without exercising network code.
- Future channels can reuse the same routing decision model.

**Status**

- Implemented on 2026-03-23 through `notification_policy.py`, with both `Notifier` and `TelegramChannelService` delegating Telegram immediate/digest/skip classification to the shared policy seam.

### Phase 4: Introduce `NotificationAdapter`

**Goal**

Provide one adapter boundary that fans out to channels and reports delivery results.

**Primary write set**

- `/Users/thanhcl/Development/TrueAI/archon-leankit/python/src/server/services/channels/notification_adapter.py`
- `/Users/thanhcl/Development/TrueAI/archon-leankit/python/src/server/services/engine/notifier.py`
- `/Users/thanhcl/Development/TrueAI/archon-leankit/python/src/server/services/channels/telegram_service.py`
- adapter tests

**Adapter responsibilities**

- receive canonical event/digest payloads
- ask policy what to do
- invoke enabled outbound channels
- return per-channel delivery results
- preserve best-effort behavior

**Acceptance**

- `Notifier` emits and delegates rather than orchestrating channel specifics.
- channel delivery results are structured and inspectable.
- Telegram remains the only enabled outbound channel initially, but the seam exists for others.

**Status**

- Implemented on 2026-03-23 through `notification_adapter.py`, with `Notifier` delegating Telegram and Discord fan-out to a shared adapter and recording structured per-channel delivery results while preserving best-effort semantics.

### Phase 5: Canonical digest pipeline

**Goal**

Create one canonical digest generation path reused by all scheduler modes.

**Primary write set**

- `/Users/thanhcl/Development/TrueAI/archon-leankit/python/src/server/services/channels/notification_formatter.py`
- `/Users/thanhcl/Development/TrueAI/archon-leankit/python/src/server/services/channels/notification_adapter.py`
- `/Users/thanhcl/Development/TrueAI/archon-leankit/python/src/server/services/channels/telegram_service.py`

**Required behavior**

- digest preview uses the canonical formatter
- direct digest send uses the canonical formatter plus adapter/channel
- due-digest cycle uses the same formatter and adapter path
- dedupe keys such as `telegram-digest:<date>` remain preserved

**Acceptance**

- there is one digest content builder
- there is one outbound digest delivery path
- scheduler-backed and caller-supplied digest flows share the same core code

**Status**

- Implemented on 2026-03-23 by extending `notification_adapter.py` with canonical Telegram digest preview/send methods and refactoring `telegram_service.py` plus `Notifier.build_telegram_daily_digest()` to reuse that shared digest path.

### Phase 6: Fallback scheduler seam and degradation semantics

**Goal**

Support future scheduled fallback jobs without elevating them into execution authority.

**Primary write set**

- notification adapter + policy + docs
- possibly channel health and ops docs if behavior changes

**Required behavior**

- fallback schedulers may invoke digest delivery or summary delivery
- fallback schedulers must not decide task execution or approval authority
- delivery failures remain best-effort by default
- control-plane success remains distinct from channel delivery success

**Acceptance**

- code clearly distinguishes event creation from notification delivery
- delivery errors do not look like task failures
- future schedulers can reuse the same path without special Telegram-only code

**Status**

- Implemented on 2026-03-23 through `notification_scheduler_service.py`, extended Telegram digest request/response contracts, and `scheduler_origin` plus `delivery_required` handling in `telegram_service.py`, so fallback summary jobs now reuse the canonical digest path while keeping `authority_scope=summary-only` and explicit `delivery_ok` vs job-status semantics.

## Compatibility Constraints

The refactor must preserve the following during the initial rollout:

- Telegram routes in `/api/channels/telegram/*`
- inbound webhook behavior for messages and approval callbacks
- `channel-smoke.py` pilot flows
- due-digest cycle behavior
- health and heartbeat endpoints
- current Observability event emission from `Notifier`

The refactor should be facade-first. Public routes and service entry points should stay stable while logic is moved behind them.

## Testing Strategy

### Existing tests that should remain green

- `/Users/thanhcl/Development/TrueAI/archon-leankit/python/tests/server/services/engine/test_notifier.py`
- `/Users/thanhcl/Development/TrueAI/archon-leankit/python/tests/server/services/channels/test_telegram_service.py`

### New tests to add

- formatter tests
  - canonical text assembly
  - digest grouping and truncation

- policy tests
  - immediate vs digest vs skip
  - critical override behavior

- adapter tests
  - per-channel result aggregation
  - best-effort delivery semantics

- transport tests
  - Telegram outbound payload shape
  - inline approval buttons

- compatibility tests
  - `digest-preview`
  - `digest-send`
  - `due-digest-cycle`

## Rollout Strategy

1. Extract formatter without changing route behavior.
2. Extract Telegram transport behind a small outbound channel abstraction.
3. Move policy classification out of `Notifier`.
4. Introduce adapter fanout while leaving Telegram as the only configured channel.
5. Convert digest flows to the shared formatter + adapter path.
6. Only after the seam is stable, consider adding new outbound channels.

This keeps the pilot live while the architecture becomes cleaner.

## Non-Goals

This implementation plan does not include:

- adding new approval authority outside Archon
- making GitHub Actions or any external scheduler part of the execution plane
- replacing the Telegram API surface
- redesigning inbound `ExternalRequestService` or `ApprovalRequestService`
- replacing Observability as the event authority

## Follow-Up Work After Core Refactor

After the adapter seam is stable, the next useful extensions are:

- wrap Discord behind the same outbound channel interface
- add richer delivery metrics and channel-level observability
- add alternative outbound channels without touching `Notifier`
- broaden fallback policy across provider, runner, model, and channel decisions
