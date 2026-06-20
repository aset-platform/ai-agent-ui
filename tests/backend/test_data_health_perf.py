import inspect

import backend.routes as routes


def test_data_health_source_no_global_invalidate():
    src = inspect.getsource(routes)
    assert "invalidate_metadata()\n" not in src
