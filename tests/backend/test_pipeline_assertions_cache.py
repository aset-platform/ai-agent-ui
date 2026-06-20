import inspect

import backend.routes as routes


def test_pipeline_assertions_uses_cache():
    src = inspect.getsource(routes)
    assert "cache:admin:pipeline-assertions" in src
