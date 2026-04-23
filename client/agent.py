"""
Uniiq Agentic Client
=====================
Connects to the MCP server via stdio, feeds GPT-4o a student profile,
and lets it autonomously call evaluate_chances / get_action_items
across each target university before synthesising a final recommendation.

Usage:
    python client/agent.py

The MCP server is launched as a subprocess; no separate server process needed.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from openai import OpenAI
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# ── Demo student profile ──────────────────────────────────────────────────────
STUDENT_PROFILE = {
    "name": "Alex Johnson",
    "gpa": 3.72,
    "sat_score": 1390,
    "act_score": 31,
    "ap_classes": 4,
    "extracurriculars": [
        "Varsity Math Team (captain)",
        "Robotics Club",
        "Hospital volunteer (120 hrs)",
    ],
    "essays_written": 1,
    "intended_major": "Computer Science",
    "target_universities": ["MIT", "Stanford", "Harvard", "UCLA", "UC Berkeley"],
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _mcp_tools_to_openai(tools) -> list[dict]:
    """Convert MCP tool descriptors to the OpenAI function-calling schema."""
    result = []
    for tool in tools:
        schema = tool.inputSchema if hasattr(tool, "inputSchema") else {}
        result.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": schema or {"type": "object", "properties": {}},
                },
            }
        )
    return result


async def _handle_tool_call(session: ClientSession, name: str, args: dict) -> str:
    """Dispatch a tool call to the MCP server and return the text result."""
    result = await session.call_tool(name, args)
    if result.content:
        return result.content[0].text
    return ""


# ── Main agentic loop ─────────────────────────────────────────────────────────

async def run_agent():
    repo_root = str(Path(__file__).parent.parent)

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_server.server"],
        env={**os.environ, "PYTHONPATH": repo_root},
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools_response = await session.list_tools()
            openai_tools = _mcp_tools_to_openai(tools_response.tools)

            client = OpenAI()

            system_prompt = (
                "You are a university admissions strategist. "
                "You have access to a live admissions database via tools. "
                "For each university the student is interested in, call "
                "evaluate_chances AND get_action_items. "
                "After collecting all data, output a single, well-structured "
                "strategic recommendation in this format:\n\n"
                "## Admissions Strategy Report\n"
                "### University Assessments (table)\n"
                "### Priority Action Items (top 5 across all schools)\n"
                "### Recommended School List (safety / target / reach)\n"
                "### 30-60-90 Day Plan\n"
            )

            messages = [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": (
                        f"Analyse this student profile and produce a strategic admissions report.\n\n"
                        f"Student Profile:\n```json\n{json.dumps(STUDENT_PROFILE, indent=2)}\n```"
                    ),
                },
            ]

            print("=" * 70)
            print("UNIIQ ADMISSIONS AGENT — starting agentic loop")
            print("=" * 70)

            # ── Agentic loop ──────────────────────────────────────────────────
            while True:
                response = client.chat.completions.create(
                    model="gpt-4o",
                    max_tokens=4096,
                    tools=openai_tools,
                    messages=messages,
                )

                msg = response.choices[0].message
                finish_reason = response.choices[0].finish_reason

                # Print any text content
                if msg.content and msg.content.strip():
                    print("\n[Agent]", msg.content)

                if finish_reason == "stop":
                    break

                if finish_reason == "tool_calls" and msg.tool_calls:
                    # Append assistant message with tool_calls
                    messages.append(msg)

                    for tc in msg.tool_calls:
                        args = json.loads(tc.function.arguments)
                        print(f"\n[Tool call] {tc.function.name}({json.dumps(args, indent=2)})")
                        result_text = await _handle_tool_call(session, tc.function.name, args)
                        print(f"[Tool result] {result_text[:400]}{'...' if len(result_text) > 400 else ''}")

                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": result_text,
                            }
                        )
                else:
                    break

            print("\n" + "=" * 70)
            print("AGENT COMPLETE")
            print("=" * 70)


def main():
    if not os.getenv("OPENAI_API_KEY"):
        sys.exit("Error: OPENAI_API_KEY environment variable not set.")
    asyncio.run(run_agent())


if __name__ == "__main__":
    main()
