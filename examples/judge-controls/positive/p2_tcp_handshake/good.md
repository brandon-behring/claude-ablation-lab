## The TCP three-way handshake

TCP establishes a connection with three messages that synchronize state on both ends.

### The sequence

1. **SYN** — the client sends a segment with the SYN flag and an initial sequence number (ISN) `x`.
2. **SYN-ACK** — the server responds with its own ISN `y`, acknowledging `x + 1`.
3. **ACK** — the client acknowledges `y + 1`; both sides now agree on both sequence spaces and the connection is ESTABLISHED.

### Why three messages, not two

Each direction of a TCP connection carries an independent byte stream, so *each side's* ISN must be both proposed and acknowledged. Two messages can acknowledge only one direction; the third message closes the loop on the server's ISN. Two-way agreement over an unreliable network also protects against a delayed, duplicate SYN from an old connection creating a half-open ghost session.

### An operational failure mode

**SYN floods**: an attacker sends many SYNs and never completes step 3, filling the server's half-open connection table. Mitigations include SYN cookies — encoding the connection state in the SYN-ACK's sequence number so the server stores nothing until the final ACK arrives.
