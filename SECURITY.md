# Security scope

This is a local reference implementation with synthetic data. The example bearer token is public and is not a secret. The API and receiver bind to `127.0.0.1` by default. Outbound webhook configuration accepts only local HTTP URLs.

Do not use this project with real customer data or expose it to the internet. A production adaptation needs managed secrets, TLS, access controls, privacy retention rules, rate limits, stronger identity checks, a durable job queue, receiver authentication, monitoring, and deployment review.

If you find a security issue in this demonstration, open a GitHub issue without sensitive data. Do not post real credentials or personal information.
