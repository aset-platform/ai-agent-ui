"""Authentication and user management package for ai-agent-ui.

This package provides:

- :mod:`auth.create_tables` — one-time Iceberg table initialisation
- :mod:`auth.repository` — :class:`~auth.repository.UserRepository`
  for CRUD operations against the ``users`` and ``audit_log`` Iceberg tables
- :mod:`auth.models` — Pydantic request / response models (added in Phase 2)
- :mod:`auth.service` — :class:`~auth.service.AuthService`
  for JWT + bcrypt (added in Phase 2)
- :mod:`auth.dependencies` — FastAPI dependency functions
  (added in Phase 2)
- :mod:`auth.api` — FastAPI router with all auth + user
  endpoints (added in Phase 2)
"""
