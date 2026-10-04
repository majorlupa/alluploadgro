# Allegro Sandbox CLI

Build the simplest practical Docker CLI for listing products on Allegro Sandbox.
Python 3.12+ and requests are the only runtime requirements. There is no web
server or database. Commands run as disposable containers; a named volume holds
OAuth tokens and an advisory lock. Only one authenticated command may use that
volume at a time, preventing refresh-token races.

Commands: login (OAuth device flow), search (catalog phrase or GTIN), product,
categories, parameters, settings (shipping and after-sales IDs), sell, status,
publish, and logout. sell accepts either product ID plus PLN price and positive
quantity, or a complete Allegro product-offer JSON file. Optional title,
description, image URLs and shipping-rate ID customize the short form. A draft
switch submits INACTIVE; otherwise sell requests ACTIVE. A dry-run prints the
payload without credentials or network access. Custom product creation uses the
JSON path and category/parameter discovery rather than a universal form.

All API and OAuth URLs are fixed to Sandbox. TLS verification stays enabled.
Redirects never automatically receive credentials. OAuth tokens are written
atomically with mode 0600, tied to client ID, and refreshed before expiry or once
after a 401. No automatic replay of uncertain POST results is permitted.
API failures include Allegro field errors. Asynchronous operations follow trusted
Location headers and Retry-After, with a finite deadline. Timeouts report recovery
instructions and the operation path; successful publication is never inferred
from 202 alone. Standard output is JSON; diagnostics and browser instructions go
to standard error. Validation and API failures exit nonzero.

Deliver Dockerfile, Compose service, env template, catalog and custom-product
examples, setup/readme, and mock-based tests covering validation, OAuth polling,
refresh, token persistence, asynchronous publication, redirects and errors.
Verify the real Docker build and offline CLI. Live publication requires the
user's registered Sandbox application and seller account; no credentials are
available in this workspace.
