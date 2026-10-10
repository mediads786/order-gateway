# ADR-0010: The AI proposer can only propose; people confirm

**Status:** Accepted

**Date:** 2026-10-08

## Context

Natural-language requests need translation into known operations. Model output can name an unknown workflow or contain invalid input. The recorded boundary is that the model never calls the ERP directly and people confirm proposals.

## Decision

Give the proposer only role-allowed registered workflows and their schemas. Validate its returned workflow and input against the registry before storing a valid proposal. Reject unknown, disallowed, or invalid outputs as invalid proposals. Require the requesting key to confirm a proposal through the normal governed workflow path. Audit stored proposals and confirmation or discard decisions.

## Consequences

Proposal generation cannot execute ERP operations, and confirmation retains workflow permissions and approvals. The default rule proposer needs no model API key; Anthropic is optional. Proposal text remains in the database. Confirmation records its outcome even when execution fails, and does not automatically retry a failed proposal. Live Anthropic behavior is not recorded.

## Alternatives considered

Direct language-model access to ERP calls is explicitly outside the project's scope.

## Evidence in the code

`app/proposals/proposers.py`, `app/proposals/routes.py`, `app/proposals/service.py`, `app/governance/routes.py`, `migrations/versions/0008_proposals.py`.
