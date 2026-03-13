"""Verify the Dash WSGI server object used by gunicorn."""

from flask import Flask


def test_server_is_flask_wsgi_app():
    """``dashboard.app:server`` must be a Flask WSGI app for gunicorn."""
    from dashboard.app import server

    assert isinstance(server, Flask)


def test_server_has_dash_routes():
    """The WSGI app should contain at least the Dash root route."""
    from dashboard.app import server

    rules = [r.rule for r in server.url_map.iter_rules()]
    assert "/" in rules
