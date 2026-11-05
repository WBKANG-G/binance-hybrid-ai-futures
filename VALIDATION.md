# Validation

-26 tests passed in0.06s using synthetic fixtures. Recent/advancing vs stopped/old/malformed/future heartbeat, shared config mismatch/expiry, unsafe flags, inconsistent/malformed/unavailable API response, fixed GET/no proxy/no redirect, body bound, LIVE refusal and sanitized failures covered.
- Ruff passed using the deployed application's existing pyproject rules. Python modules compile. No dependency changes.
- Main and deployed application source unchanged. No Docker restart, database mutation, SMTP connection, new alert or exchange request performed by this cloud work.
- Windows execution is pending; no actual worker liveness or SMTP authentication success is claimed by cloud tests. The helper is executed through STDIN in the existing alerts container and is not installed into the image.
- Evidence records are an allowlist transcription of user screenshots; no screenshot review tokens or addresses/passwords are included. The last deployed readiness report had22 signed GETs,1 pooled connection,21 reused requests and1349ms elapsed.
