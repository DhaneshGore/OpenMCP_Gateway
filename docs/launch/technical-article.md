# DRAFT: technical article

> Not for publication without maintainer approval.

## Building a reliable MCP gateway: what we learned about multiplexing, lifecycles and egress

An MCP gateway looks simple: accept a request, forward it to the right server,
return the answer. The interesting parts are what happens when things go
wrong. This article walks through the design decisions in OpenMCP Gateway and
why we made them.

### 1. Two protocol conversations, not one

The gateway is an MCP **server** toward clients and an MCP **client** toward
each upstream. We re-originate every upstream request instead of relaying
JSON-RPC frames. Client request ids never travel upstream and upstream ids
never travel downstream. That decision removes a whole class of bugs: one
client can't receive another client's response, because responses are
matched inside the gateway against futures it created itself.

It also lets the two sides use different protocol revisions. Clients may use
the handshake-based revisions or the newer 2026-07-28 revision. The official
SDK handles all of that on the client-facing side, and stdio upstreams are
driven over the handshake revision they negotiate.

### 2. Multiplexing stdio: ids, not order

A stdio MCP server is a pipe pair. With concurrent requests, responses can
arrive in any order, twice, late, malformed, or not at all. Our multiplexer
(about 300 lines) keeps a map from a per-connection, monotonically increasing
integer id to a future:

- a response resolves only the future registered under exactly its id;
- unknown, duplicate and late ids are counted and dropped;
- a timeout removes the entry and sends `notifications/cancelled` upstream,
  so a late answer can't land anywhere;
- if the process exits, every pending future fails at once;
- lines over a size cap are discarded up to the next newline without losing
  framing;
- stderr is drained continuously into a bounded ring buffer, so a chatty
  server can't fill a pipe and deadlock.

We test this against a deliberately misbehaving fake server that answers out
of order, sends garbage, duplicates responses, floods stderr and crashes on
command.

### 3. Lifecycles need memory

Restarting a crashed server immediately looks friendly, but in practice it
becomes a hot loop that eats CPU and logs. The manager records failures in a
sliding window, backs off exponentially, and stops trying after a hard cap
until an operator intervenes. Starts are single-flight: a burst of 50
requests to a stopped server launches one process, not 50. When an operator
stops a server, a client request doesn't silently start it again.

### 4. Egress checks belong at connect time

Validating a remote server's URL when it's configured isn't enough: DNS can
change between the check and the connection, and redirects add new
destinations. Our HTTP client uses a custom network backend that resolves the
hostname, requires **every** resolved address to be public (or explicitly
allowed), and then connects to the validated address itself, while TLS still
verifies the original hostname. Redirects open new connections, so they go
through the same check automatically.

### 5. Secure defaults, explicit exceptions

The gateway listens on localhost. It refuses to listen on a network
interface without authentication unless you set a flag whose name makes the
risk obvious. Registering subprocess servers through the admin API is off,
because it amounts to remote code execution. Servers get a minimal
environment, so the gateway's cloud credentials don't leak into every
child process.

### 6. What we didn't build (yet)

Distributed rate limiting, shared sessions across replicas, OAuth, and
sampling proxying are on the roadmap. We document what is and isn't
implemented rather than implying more.

*Code, ADRs and tests: https://github.com/DhaneshGore/mcp-bridge*
