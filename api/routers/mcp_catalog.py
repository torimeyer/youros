import asyncio
import logging
import subprocess
from typing import Optional

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter(tags=["mcp_catalog"])

CATALOG: list[dict] = [
    {"name": "Filesystem", "description": "Access local files and folders on your machine", "icon": "folder_open", "npm_package": "@modelcontextprotocol/server-filesystem", "requires_auth": False},
    {"name": "GitHub", "description": "Access repos, issues, and pull requests", "icon": "code", "npm_package": "@modelcontextprotocol/server-github", "requires_auth": True, "auth_hint": "Needs a GitHub access key. Create one at github.com/settings/tokens."},
    {"name": "Postgres", "description": "Query and manage your databases", "icon": "database", "npm_package": "@modelcontextprotocol/server-postgres", "requires_auth": False},
    {"name": "Brave Search", "description": "Search the web using Brave", "icon": "travel_explore", "npm_package": "@modelcontextprotocol/server-brave-search", "requires_auth": True, "auth_hint": "Needs a Brave Search API key (set BRAVE_API_KEY)"},
    {"name": "Puppeteer", "description": "Automate a web browser for scraping or testing", "icon": "web", "npm_package": "@modelcontextprotocol/server-puppeteer", "requires_auth": False},
    {"name": "Memory", "description": "Persistent notes and memory that last across sessions", "icon": "psychology", "npm_package": "@modelcontextprotocol/server-memory", "requires_auth": False},
    {"name": "Google Calendar", "description": "View and manage your calendar events", "icon": "calendar_month", "npm_package": "mcp-server-google-calendar", "requires_auth": True, "auth_hint": "Needs Google OAuth credentials (client ID and secret)"},
    {"name": "Slack", "description": "Send messages and read channels", "icon": "forum", "npm_package": "@modelcontextprotocol/server-slack", "requires_auth": True, "auth_hint": "Needs a Slack bot key from your Slack app settings."},
    {"name": "Figma", "description": "Access and inspect design files", "icon": "palette", "npm_package": "figma-mcp", "requires_auth": True, "auth_hint": "Needs a Figma access key from figma.com/developers."},
    {"name": "Google Drive", "description": "Access Google Docs, Sheets, and Drive files", "icon": "folder_shared", "npm_package": "@modelcontextprotocol/server-gdrive", "requires_auth": True, "auth_hint": "Needs Google OAuth credentials (client ID and secret)"},
    {"name": "Linear", "description": "Manage issues, projects, and teams in Linear", "icon": "track_changes", "npm_package": "@linear/mcp-server", "requires_auth": True, "auth_hint": "Needs a Linear API key from linear.app/settings/api."},
    {"name": "Notion", "description": "Read and write pages, databases, and blocks in Notion", "icon": "article", "npm_package": "@notionhq/mcp", "requires_auth": True, "auth_hint": "Needs a Notion integration token from notion.so/my-integrations."},
    {"name": "Jira", "description": "Search, create, and update Jira issues and projects", "icon": "bug_report", "npm_package": "mcp-server-jira", "requires_auth": True, "auth_hint": "Needs a Jira API token and your Atlassian account email."},
    {"name": "Sentry", "description": "Query errors, issues, and performance data from Sentry", "icon": "error_outline", "npm_package": "@sentry/mcp-server", "requires_auth": True, "auth_hint": "Needs a Sentry auth token from sentry.io/settings/account/api/auth-tokens."},
    {"name": "Stripe", "description": "Look up customers, payments, and subscriptions", "icon": "credit_card", "npm_package": "@stripe/mcp", "requires_auth": True, "auth_hint": "Needs a Stripe secret key (set STRIPE_SECRET_KEY)."},
    {"name": "Airtable", "description": "Read and write Airtable bases, tables, and records", "icon": "table_chart", "npm_package": "airtable-mcp-server", "requires_auth": True, "auth_hint": "Needs an Airtable personal access token from airtable.com/create/tokens."},
]

# Org-curated MCPs: empty stub, populated by enterprise config (E1) later.
ORG_CATALOG: list[dict] = []


def _run_mcp_list() -> list[str]:
    result = subprocess.run(
        ["claude", "mcp", "list"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        logger.warning("claude mcp list failed: %s", result.stderr)
        return []
    installed = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line:
            name = line.split()[0]
            installed.append(name)
    return installed


def _run_mcp_add(name: str) -> tuple[bool, str]:
    result = subprocess.run(
        ["claude", "mcp", "add", name],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode == 0:
        return True, f"{name} was added successfully."
    stderr = result.stderr.strip() or result.stdout.strip()
    logger.warning("claude mcp add %s failed: %s", name, stderr)
    return False, "Something went wrong while adding this tool. Please try again."


@router.get("/mcp/catalog")
async def get_catalog():
    return {"catalog": CATALOG + ORG_CATALOG}


@router.get("/mcp/installed")
async def get_installed():
    installed = await asyncio.to_thread(_run_mcp_list)
    return {"installed": installed}


@router.post("/mcp/install")
async def install_mcp(body: dict):
    name: Optional[str] = body.get("name")
    if not name:
        return {"ok": False, "message": "No tool name provided."}
    ok, message = await asyncio.to_thread(_run_mcp_add, name)
    return {"ok": ok, "message": message}
