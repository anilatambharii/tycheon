# Runbook: a credential has leaked

Treat any of these as an incident ([incident response](../incident-response.md)): a customer API
key or session, a bring-your-own market-data credential, the Stripe key or webhook secret, the
operator or metrics token, the session secret, a cloud credential, the KMS key.

## Customer API key
1. Revoke it now: the owner calls `DELETE /v1/api-keys/<id>`, or an operator revokes it in the
   database (record it). Revocation is immediate: the next request with the key is refused.
2. Look at what it did: the audit log (`GET /v1/audit`) and usage events for that key's period.
3. Tell the customer; they create a new key. Keys are shown once and stored only as hashes, so there
   is nothing to "recover".

## Session secret
Rotating `TYCHEON_CP_SESSION_SECRET` signs everyone out (sessions are signed with it). Rotate it, redeploy,
and ask customers to sign in again.

## Operator token / metrics token
Set a new value in the secret and redeploy. The operator API does not exist without its token, and the
metrics endpoint answers 404 without its token.

## Stripe
Roll the key in the Stripe dashboard (restricted keys), update the Secret, redeploy, and check webhook
signatures still verify. Only test-mode keys are accepted by the code today.

## A customer's bring-your-own data credential
The stored value is encrypted under a per-credential key wrapped by KMS and bound to the customer
and credential. If the *vendor* credential leaked, the customer rotates it with the vendor and
re-adds it (`DELETE` then `POST /v1/credentials`). If the **KMS key** is suspected compromised, rotate
it, re-encrypt every credential (a script that opens each with the old key and seals it with the new),
and review CloudTrail for `Decrypt` calls.

## Cloud credentials
Follow the cloud provider's procedure; IRSA / Workload Identity means no long-lived keys should exist,
so a leaked static key is itself a finding.

Always: find how it leaked, add a control, and record the timeline.
