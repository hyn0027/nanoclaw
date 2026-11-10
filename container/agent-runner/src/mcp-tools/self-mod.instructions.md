## Installing packages & tools

To install packages that persist, use the self-modification tools:

**`install_packages`** — request system (apt) or global npm packages. Requires admin approval. Pin an exact version when you know it (`pkg@1.2.3` for npm, `pkg=<version>` for apt — from the user, or the project's docs or release page) so the admin approves exactly what gets installed; never guess a version. A bare name installs whatever is newest when the image is built. You cannot query the registries yourself (they are blocked at runtime).

Example flow:
```
install_packages({ apt: ["ffmpeg"], npm: ["@xenova/transformers"], reason: "Audio transcription" })
# → Admin gets an approval card → approves
```

**When to use this vs workspace `pnpm install`:**
- `pnpm install` if you only need it temporarily to do one task. Will not be available in subsequent truns.
- `install_packages` persists for all future turns. Use especially if the user specifically asks you to add a capability

### MCP servers (`add_mcp_server`)

Use **`add_mcp_server`** to add an MCP server to your configuration. Your network goes through a
gateway that denies every host no policy permits, and package registries (npm, PyPI, ...) are not
permitted. So a server that downloads its code when it starts — `npx -y <pkg>`, `pnpm dlx`,
`bunx`, `uvx`, `pipx run` — **will fail** (the download gets 403 and the server exits; you only see
`CONNECTION_CLOSED`). Never use those launchers. Pick one of two paths:

**1. Remote server (preferred when the service offers one).** No code runs in your container:

```
add_mcp_server({ name: "github", url: "https://api.githubcopilot.com/mcp/" })
```

**2. Local (stdio) server: install it first, then add it.** Two requests, two approvals:

```
install_packages({ npm: ["@modelcontextprotocol/server-memory@<version>"], reason: "MCP server: memory" })
# → admin approves → image is rebuilt (on the host) and your container restarts
# → find the command it installed:
#     cat "$(pnpm root -g)/@modelcontextprotocol/server-memory/package.json" | grep -A3 '"bin"'
add_mcp_server({ name: "memory", command: "mcp-server-memory" })
# → admin approves → container restarts with the server
```

The server then starts from your image without downloading anything. If it calls an external API,
that host must be permitted by the gateway's policy, which you cannot see or change: a `403` with a
policy error from that host means it is not permitted — tell the user which host was refused, do
not retry or work around it.

If a server fails to start, check how it was launched before guessing: a launcher that downloads
at start is the usual cause. Do not swap in a different server or config on your own after a
failure — tell the user what failed and propose the alternative, then request it.

Use HTTPS; plain HTTP is allowed only for `localhost` and
`host.docker.internal` (an MCP server running on the host machine). URLs with
credentials, fragments, or credential-looking query parameters are rejected;
authentication belongs in OneCLI.

Do not ask the user to give you credentials or tell them how to create credentials (OAuth, API keys, etc.) — NEVER fabricate credential setup instructions. Credentials are handled by the OneCLI gateway. Use `"onecli-managed"` as the placeholder value for any credential env vars or config fields. After the MCP server is installed and the container restarts, load `/onecli-gateway` for the full credential-handling flow (connect URLs, stubs, error recovery).
