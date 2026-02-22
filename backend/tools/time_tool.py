"""LangChain tool that exposes the current system wall-clock time to the LLM.

The :func:`get_current_time` function is decorated with
:func:`~langchain.tools.tool`, which wraps it in a
:class:`~langchain_core.tools.StructuredTool` and makes its docstring
available to the LLM as the tool description.  The LLM uses that description
to decide when to call this tool.

Typical usage::

    from tools.time_tool import get_current_time

    # Bind to an LLM:
    llm_with_tools = llm.bind_tools([get_current_time])

    # Or invoke directly (e.g. in a test):
    print(get_current_time.invoke({}))
"""

import datetime

from langchain.tools import tool


@tool
def get_current_time() -> str:
    """Return the current local date and time as an ISO-format string.

    Useful when the user asks about the current time, date, day of the week,
    or any question that requires knowing the present moment.

    Returns:
        A string representation of :func:`datetime.datetime.now`, e.g.
        ``"2026-02-22 14:37:55.993716"``.

    Example:
        >>> result = get_current_time.invoke({})
        >>> isinstance(result, str)
        True
    """
    return str(datetime.datetime.now())
