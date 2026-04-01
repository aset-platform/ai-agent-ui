# API Reference (Auto-Generated)

!!! info
    This page is auto-generated from FastAPI route definitions on every `mkdocs build`.
    Do not edit manually.

## Core

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/docs/oauth2-redirect` | public | swagger_ui_redirect |
| `POST` | `/v1/audit/chat-sessions` | authenticated | save_chat_session |
| `GET` | `/v1/audit/chat-sessions` | authenticated | list_chat_sessions |
| `GET` | `/v1/audit/chat-sessions/{session_id}` | authenticated | get_chat_session_detail |
| `GET` | `/v1/subscription` | authenticated | get_subscription |
| `POST` | `/v1/subscription/cancel` | authenticated | cancel_subscription |
| `POST` | `/v1/subscription/checkout` | authenticated | checkout |
| `POST` | `/v1/subscription/cleanup` | superuser | cleanup_subscriptions |
| `POST` | `/v1/subscription/webhooks/razorpay` | public | razorpay_webhook |
| `POST` | `/v1/subscription/webhooks/stripe` | public | stripe_webhook |
| `GET` | `/v1/users` | superuser | list_users |
| `POST` | `/v1/users` | superuser | create_user |
| `POST` | `/v1/webhooks/razorpay` | public | razorpay_webhook |

## Auth

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/v1/auth/health` | public | auth_health |
| `POST` | `/v1/auth/login` | public | login |
| `POST` | `/v1/auth/login/form` | public | login_form |
| `POST` | `/v1/auth/logout` | authenticated | logout |
| `GET` | `/v1/auth/me` | authenticated | get_me |
| `PATCH` | `/v1/auth/me` | authenticated | patch_me |
| `POST` | `/v1/auth/oauth/callback` | public | oauth_callback |
| `GET` | `/v1/auth/oauth/providers` | public | list_oauth_providers |
| `GET` | `/v1/auth/oauth/{provider}/authorize` | public | oauth_authorize |
| `POST` | `/v1/auth/password-reset/confirm` | authenticated | password_reset_confirm |
| `POST` | `/v1/auth/password-reset/request` | authenticated | password_reset_request |
| `POST` | `/v1/auth/refresh` | public | refresh_token |
| `GET` | `/v1/auth/sessions` | authenticated | list_sessions |
| `POST` | `/v1/auth/sessions/revoke-all` | authenticated | revoke_all_sessions |
| `DELETE` | `/v1/auth/sessions/{session_id}` | authenticated | revoke_session |
| `POST` | `/v1/auth/upload-avatar` | authenticated | upload_avatar |

## Users

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/v1/users/me/portfolio` | authenticated | get_portfolio |
| `POST` | `/v1/users/me/portfolio` | authenticated | add_portfolio_holding |
| `PUT` | `/v1/users/me/portfolio/{transaction_id}` | authenticated | edit_portfolio_holding |
| `DELETE` | `/v1/users/me/portfolio/{transaction_id}` | authenticated | delete_portfolio_holding |
| `GET` | `/v1/users/me/preferences` | authenticated | get_preferences |
| `PUT` | `/v1/users/me/preferences` | authenticated | put_preferences |
| `GET` | `/v1/users/me/tickers` | authenticated | get_user_tickers |
| `POST` | `/v1/users/me/tickers` | authenticated | link_ticker |
| `DELETE` | `/v1/users/me/tickers/{ticker}` | authenticated | unlink_ticker |
| `GET` | `/v1/users/{user_id}` | superuser | get_user |
| `PATCH` | `/v1/users/{user_id}` | superuser | update_user |
| `DELETE` | `/v1/users/{user_id}` | superuser | delete_user |
| `POST` | `/v1/users/{user_id}/reset-password` | superuser | admin_reset_password |

## Dashboard

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/v1/dashboard/analysis/latest` | authenticated | get_analysis_latest |
| `GET` | `/v1/dashboard/chart/forecast-series` | authenticated | get_chart_forecast_series |
| `GET` | `/v1/dashboard/chart/indicators` | authenticated | get_chart_indicators |
| `GET` | `/v1/dashboard/chart/ohlcv` | authenticated | get_chart_ohlcv |
| `GET` | `/v1/dashboard/compare` | authenticated | get_compare |
| `GET` | `/v1/dashboard/forecasts/summary` | authenticated | get_forecasts_summary |
| `GET` | `/v1/dashboard/home` | authenticated | get_dashboard_home |
| `GET` | `/v1/dashboard/llm-usage` | authenticated | get_llm_usage |
| `GET` | `/v1/dashboard/portfolio/forecast` | authenticated | get_portfolio_forecast |
| `GET` | `/v1/dashboard/portfolio/performance` | authenticated | get_portfolio_performance |
| `POST` | `/v1/dashboard/refresh/{ticker}` | authenticated | start_refresh |
| `GET` | `/v1/dashboard/refresh/{ticker}/status` | authenticated | refresh_status |
| `GET` | `/v1/dashboard/registry` | authenticated | get_registry |
| `GET` | `/v1/dashboard/watchlist` | authenticated | get_watchlist |

## Insights

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/v1/insights/correlation` | authenticated | get_correlation |
| `GET` | `/v1/insights/dividends` | authenticated | get_dividends |
| `GET` | `/v1/insights/quarterly` | authenticated | get_quarterly |
| `GET` | `/v1/insights/risk` | authenticated | get_risk |
| `GET` | `/v1/insights/screener` | authenticated | get_screener |
| `GET` | `/v1/insights/sectors` | authenticated | get_sectors |
| `GET` | `/v1/insights/targets` | authenticated | get_targets |

## Admin

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/v1/admin/audit-log` | superuser | get_audit_log |

---

*64 endpoints total.*
