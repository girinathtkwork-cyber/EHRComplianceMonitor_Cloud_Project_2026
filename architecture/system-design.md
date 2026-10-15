# System design document index

The maintained architecture documentation lives in `docs/`:

- [High-Level Design](../docs/HLD.md) describes the implementation-aligned local and AWS demo architecture, trust boundaries, data flows, security controls, retention, observability, and production-readiness gaps.
- [Low-Level Design](../docs/LLD.md) documents module responsibilities, configuration, API contracts, data structures, queue/job lifecycle, FHIR importer behavior, failure handling, and limits.
- [README](../README.md) is the entry point for setup, demo use, tests, and repository navigation.
- [AWS deployment report](../docs/AWS_DEPLOYMENT_REPORT_20261016.md) records dated account-side deployment facts and validation evidence.

Update these documents together when architecture or runtime behavior changes. Distinguish repository behavior from AWS account configuration, and date deployment evidence. The system is a synthetic-data research demonstration; the design documents do not claim production readiness or regulatory certification.
