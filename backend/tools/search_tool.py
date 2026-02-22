"""LangChain tool that performs live web searches via the SerpAPI Google Search wrapper.

:func:`search_web` delegates to
:class:`~langchain_community.utilities.SerpAPIWrapper`, which calls the
`SerpAPI <https://serpapi.com>`_ REST endpoint and returns an organic-result
summary string.  A valid ``SERPAPI_API_KEY`` environment variable must be set
at runtime; the wrapper reads it automatically.

Failures (network errors, missing API key, quota exceeded) are caught and
returned as a plain error string so the LLM can receive a ``ToolMessage``
with meaningful context rather than causing an unhandled exception in the
agentic loop.

Typical usage::

    from tools.search_tool import search_web

    # Bind to an LLM:
    llm_with_tools = llm.bind_tools([search_web])

    # Or invoke directly (requires SERPAPI_API_KEY in environment):
    print(search_web.invoke({"query": "Python 3.12 release date"}))
"""

from langchain.tools import tool
from langchain_community.utilities import SerpAPIWrapper


@tool
def search_web(query: str) -> str:
    """Search the web for up-to-date information on the given query.

    Use this tool when the user asks about recent events, current news,
    live data (prices, scores, weather), or any topic that may have changed
    since the model's training cut-off.

    Args:
        query: A natural-language or keyword search query, e.g.
            ``"Python 3.12 new features"`` or ``"current Bitcoin price"``.

    Returns:
        A summary string of the top organic search results, as provided
        by SerpAPI.  If the request fails for any reason (missing API key,
        network error, quota exceeded), returns a descriptive error string
        of the form ``"Search failed: <reason>"``.

    Example:
        >>> # Requires SERPAPI_API_KEY to be set in the environment.
        >>> result = search_web.invoke({"query": "Python 3.12 release date"})
        >>> isinstance(result, str)
        True
    """
    try:
        search = SerpAPIWrapper()
        return search.run(query)
    except Exception as e:
        # Return the error as a string so the LLM receives a ToolMessage
        # rather than an unhandled exception propagating up the stack.
        return f"Search failed: {e}"
