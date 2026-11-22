# Remote access

`second-brain-ics` normally runs over **stdio**, started by an MCP client on the same machine.
Claude's **custom connectors** (claude.ai, the desktop and mobile apps, Claude Code) work
differently: they call an MCP server over HTTPS **from Anthropic's cloud**, not from your device.
HTTP mode lets this server be one of those connectors.

```
Claude (Anthropic's cloud)
        │  HTTPS, OAuth bearer token
        ▼
your tunnel or reverse proxy (public URL, TLS)
        │
        ▼
second-brain-ics serve --transport http   (127.0.0.1:8777)
        │  HTTPS, outbound only
        ▼
the calendar service's feed (a share link)
```

The server stays bound to loopback. Something else publishes it: a Cloudflare Tunnel, Tailscale
Funnel, or a reverse proxy with TLS. The feeds' addresses never leave the machine.

This is the same design as
[second-brain-joplin's](https://github.com/second-brain-tools/second-brain-joplin/blob/main/docs/remote-access.md),
whose guide goes further into publishing and keeping a server running. What differs is below.

## Running in HTTP mode

```bash
second-brain-ics serve --transport http --public-url https://ics.example.com --env-file serve.env
```

| Flag | Variable | Default | |
|---|---|---|---|
| `--transport {stdio,http}` | `SBICS_TRANSPORT` | `stdio` | |
| `--host` | `SBICS_HOST` | `127.0.0.1` | Loopback; the tunnel reaches it locally |
| `--port` | `SBICS_PORT` | `8777` | Beside second-brain-joplin's 8775 and second-brain-imap's 8776 |
| `--path` | `SBICS_HTTP_PATH` | `/mcp` | The MCP endpoint |
| `--public-url` | `SBICS_PUBLIC_URL` | none | The public origin (`https://host`, no path); sign-in needs it |
| `--env-file PATH` | | none | `KEY=value` lines, loaded first; variables already set win |
| `--no-auth` | | off | No sign-in, for a local test only |

- **Stateless, plain JSON.** Each request stands alone, so a restart never breaks a client's
  session. The feeds' copies live as long as the process, so every request reads the same ones.
- **Host check.** A request whose `Host` header isn't a loopback name or the public URL's host is
  answered `421`.
- **`/healthz`** answers `{"server": true, "calendar": true}` with `200` when every feed's latest
  fetch worked, or `"calendar": false` with `503` when one failed or a setting can't be used. It
  is never behind sign-in, so it carries nothing else. An answer is reused for 30 seconds, and a
  feed is fetched at most once per `SBICS_MIN_FETCH_SECONDS` however often it is asked.

### What it refuses to start with

- HTTP mode without sign-in configured, unless `--no-auth` is given; sign-in only partly
  configured; `--no-auth` while sign-in is configured.
- `--no-auth` on anything but a loopback address, or together with a public URL.
- Sign-in without a public URL, a public URL that isn't `https` (off loopback) or that has a
  path, query or fragment, or a signing key under 32 characters.
- Any setting that can't be used ([configuration.md](configuration.md)).

## Sign-in

GitHub is the identity: the server sends you to GitHub, GitHub says who you are, and the server
lets in only the numeric GitHub user ids you listed.

1. **Create a GitHub OAuth App** (GitHub → Settings → Developer settings → OAuth Apps):
   homepage `https://ics.example.com`, callback `https://ics.example.com/auth/callback`.
   One app per server: don't reuse another connector's.
2. **Find your id:** `curl -s https://api.github.com/users/<login> | grep '"id"'`.
3. **Make a signing key:** `openssl rand -base64 32`.
4. Put them in the env file:

   ```
   SBICS_PUBLIC_URL=https://ics.example.com
   SBICS_GITHUB_CLIENT_ID=...
   SBICS_GITHUB_CLIENT_SECRET=...
   SBICS_JWT_SIGNING_KEY=...
   SBICS_ALLOWED_GITHUB_IDS=12345
   ```

An account that isn't listed is refused before any MCP message is read, and a second check runs
before every tool.

### Turning access off

Any one of these: revoke the OAuth App's grant in your GitHub settings; change
`SBICS_JWT_SIGNING_KEY` and restart; stop the tunnel. To stop the server reading a calendar,
delete its share link at the calendar service: the server then reports the error the feed
answers with.

## Publishing it

Put the server behind HTTPS at the public URL. With a Cloudflare Tunnel that is one hostname
routed to `http://127.0.0.1:8777`. Claude's connector traffic comes from Anthropic's published
outbound addresses (<https://platform.claude.com/docs/en/api/ip-addresses>), so the hostname can
be closed to everyone else, except the pages your own browser opens while signing in:
`/authorize`, `/auth/callback` and `/consent`.

Then in Claude: Customize → Connectors → add a custom connector with the URL
`https://ics.example.com/mcp`, and sign in.

## Limits and logs

- Each caller gets `SBICS_RATE_LIMIT_PER_MINUTE` tool calls a minute (60), with bursts of half.
- The log has a start-up line naming the feeds (names only), one line per tool call:
  `tool=calendar_events caller=<github id> client=Claude outcome=ok ms=40`, and a warning naming
  the feed and the kind of failure when a fetch fails.
  Never arguments (a search says what you are looking for), results, event text or an address.
  The HTTP library's own loggers, which print each request's address, are held at warning level.

## Before you connect it

Anyone can send you an invitation, and its title and description reach the model. The server
cleans that text and labels it as information, but an invitation can still ask, in plain words,
for something to be done. This server can't change anything, so a hostile event can't act through
it; a chat that also has tools which write or send things gives it somewhere to act. Prefer
approval prompts on those tools in chats that read your calendar.
