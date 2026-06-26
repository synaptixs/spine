# URL Shortener Service

A small service that turns long URLs into short, shareable links and
redirects visitors from the short link back to the original.

## Create a short link

A user submits a long URL and gets back a short code they can share.

- `POST /links` accepts `{ "url": "https://..." }` and returns
  `{ "code": "abc123", "short_url": "https://sho.rt/abc123" }`.
- The same long URL submitted twice returns the same code (idempotent).
- An invalid or non-HTTP(S) URL is rejected with `400`.

## Follow a short link

A visitor opening a short link is redirected to the original URL.

- `GET /{code}` responds `301` with the original URL in `Location`.
- An unknown code responds `404`.

## Basic analytics

The owner of a link can see how many times it was followed.

- `GET /links/{code}/stats` returns `{ "code": "abc123", "clicks": 42 }`.
- Click counts are eventually consistent; a small delay is acceptable.

## Out of scope

- Custom/vanity codes.
- User accounts and authentication.
- Link expiry.
