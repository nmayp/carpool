# Security policy

Please report security problems privately through the repository's **Security** tab ("Report a vulnerability"),
not in a public issue.

Things worth reporting:

- the MCP server or hooks executing something they shouldn't, or reading files outside what a tool call names;
- `carpool_lookup` reading files outside the path it was given;
- a way for task text or a model reply to make carpool run commands or write files.

carpool sends your task text to whichever model backend you choose. It stores no credentials of its own: API keys
are read from the environment variables each backend documents.

Supported versions: the latest commit on `main`.
