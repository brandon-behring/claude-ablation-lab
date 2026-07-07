## Idempotency in HTTP APIs

An operation is **idempotent** when performing it once or N times leaves the server in the same state. GET, PUT, and DELETE are idempotent by contract (PUT replaces with the same representation; DELETE of a deleted resource stays deleted); POST is not — each POST may create another resource or charge another payment.

Idempotency matters because **retries are unavoidable**: clients time out without knowing whether the request was applied, and safe recovery means re-sending. Re-sending an idempotent request is harmless; re-sending a POST double-applies it.

The standard fix for non-idempotent operations is an **idempotency key**: the client generates a unique token per logical operation and sends it in a header; the server stores the result under that key and replays the stored response on duplicates instead of re-executing. Payment APIs (e.g. Stripe) make this mandatory. Note the scope: idempotency deduplicates *effects*, not responses — a replayed request may still see a different response if the resource has since changed by other means.
