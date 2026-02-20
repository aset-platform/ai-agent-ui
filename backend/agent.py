from langchain_groq import ChatGroq
from langchain.tools import tool
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
import datetime


# Tool definitions
@tool
def get_current_time() -> str:
    """Returns the current system time"""
    return str(datetime.datetime.now())


@tool
def search_web(query: str) -> str:
    """Searches the web for the given query and returns a summary of results"""
    # Placeholder — swap in a real search API (e.g. Tavily, SerpAPI) here
    return f"Search results for '{query}': [Dummy search results here]"


tools = [get_current_time, search_web]

TOOL_MAP = {
    "get_current_time": get_current_time,
    "search_web": search_web,
}

# Initialize Claude Sonnet 4.6
llm = ChatGroq(model="openai/gpt-oss-120b", temperature=0)
llm_with_tools = llm.bind_tools(tools)


def run_agent(user_input: str, history: list = []) -> str:
    """
    Run the agentic loop with tool support.
    Keeps invoking the model until it stops requesting tool calls,
    feeding tool results back in each iteration.
    """
    # Convert plain history dicts to LangChain message objects
    lc_history = []
    for msg in history:
        role = msg.get("role")
        content = msg.get("content", "")
        if role == "user":
            lc_history.append(HumanMessage(content=content))
        elif role == "assistant":
            lc_history.append(AIMessage(content=content))

    messages = lc_history + [HumanMessage(content=user_input)]

    while True:
        response = llm_with_tools.invoke(messages)
        messages.append(response)

        print("RAW RESPONSE:", response)

        # No tool calls → Claude is done, return final text
        if not response.tool_calls:
            break

        # Execute every requested tool and feed results back
        for tc in response.tool_calls:
            tool_name = tc["name"]
            tool_args = tc.get("args", {})
            print(f"TOOL CALLED: {tool_name} | ARGS: {tool_args}")

            fn = TOOL_MAP.get(tool_name)
            if fn:
                result = fn.invoke(tool_args)
            else:
                result = f"Unknown tool: {tool_name}"

            messages.append(
                ToolMessage(content=str(result), tool_call_id=tc["id"])
            )

    return response.content or "No response"
