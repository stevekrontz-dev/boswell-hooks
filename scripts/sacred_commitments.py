"""Sacred Commitments & Tool Registry loader for the boswell-hooks plugin.
Emits markers on stdout (injected into SessionStart context) for Claude Code to
pick up and call boswell_search via MCP. Hooks cannot call MCP directly, so the
markers are the handoff."""
import sys


def load_sacred():
    print("\n<!-- LOAD_SACRED_COMMITMENTS -->")
    print("Claude Code: Call boswell_search with query 'sacred_manifest' to load the active commitments manifest.")
    print("<!-- END_LOAD_MARKER -->\n")


def load_tool_registry():
    print("\n<!-- LOAD_TOOL_REGISTRY -->")
    print("Claude Code: Call boswell_search with query 'tool_registry' to load available tools. Match against project context and surface relevant ones.")
    print("<!-- END_TOOL_REGISTRY_MARKER -->\n")


def check_priorities():
    print("\n<!-- CHECK_EXPIRING_PRIORITIES -->")
    print("Claude Code: Call boswell_search for commits with priority_until dates expiring within 24 hours.")
    print("<!-- END_PRIORITY_MARKER -->\n")


if __name__ == "__main__":
    cmd = sys.argv[1].lower() if len(sys.argv) > 1 else ""
    if cmd in ("load", "load_silent"):
        load_sacred()
        load_tool_registry()
    elif cmd == "check_priorities":
        check_priorities()
    elif cmd == "load_tools":
        load_tool_registry()
    else:
        print("Usage: sacred_commitments.py <load|load_silent|check_priorities|load_tools>")
        sys.exit(1)
