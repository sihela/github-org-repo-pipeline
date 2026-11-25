# github-org-repo-pipeline
A resilient, low-memory data pipeline that scrapes repository metadata from the GitHub REST API, validates it with a configurable standalone engine, and cleans it based on the validator's machine-readable report.  Built to run comfortably on modest hardware (8 GB RAM, dual-core CPU) by streaming data to disk instead of holding it in memory.
