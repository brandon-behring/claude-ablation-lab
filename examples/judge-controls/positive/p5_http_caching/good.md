## HTTP caching

HTTP caching lets clients and intermediaries reuse responses instead of refetching them, governed by explicit headers.

### Freshness: Cache-Control

`Cache-Control: max-age=3600` marks a response reusable for an hour without contacting the origin. `no-cache` allows storing but requires revalidation before reuse; `no-store` forbids storing entirely; `private` restricts reuse to the requesting user's cache (not shared proxies/CDNs); `public` permits shared caches even for authenticated responses.

### Validation: ETags and conditional requests

When a cached response goes stale, the client revalidates instead of refetching: it sends `If-None-Match: "<etag>"` with the stored entity tag. If the resource is unchanged, the origin answers **304 Not Modified** with no body — the cache keeps serving its copy — otherwise it sends the new representation. `Last-Modified`/`If-Modified-Since` is the coarser, timestamp-based fallback.

### What is cacheable

By default, caching applies to **safe** methods — effectively GET (and HEAD). POST responses are cacheable only with explicit freshness headers, which is rare in practice; PUT and DELETE responses are not cached at all. A correct mental model: caches store *representations of resources retrieved safely*, keyed by method + URL (+ `Vary` headers).
