# alluploadgro

A small CLI in a Docker container for posting **Buy Now offers on Allegro
Sandbox**, priced in PLN. Start a container per command. Login tokens persist
in a named Docker volume. No server, exposed ports, or database.

## First-time setup

1. Create and activate a seller account on
   [Allegro Sandbox](https://allegro.pl.allegrosandbox.pl). Follow Allegro's
   [account and application setup guide](https://developer.allegro.pl/tutorials/pierwsze-kroki-MRwYEoOq0im).
2. Register an application at
   [Sandbox Developer Apps](https://apps.developer.allegro.pl.allegrosandbox.pl).
   Choose **Device flow** (an application without a browser/redirect URL).
   Enable offer read/write permissions and permissions for the catalog,
   categories, shipping rates, and after-sales policies used by these commands.
3. On your Sandbox seller account, configure your shipping address and a
   shipping price list. Use one shipping list, or name the intended default
   `default`. For a business account, also configure return and complaint
   policies with one choice each, or name the defaults `default`.
   Allegro can use these defaults when creating an offer. See
   [the product-offer guide](https://developer.allegro.pl/tutorials/jak-jednym-requestem-wystawic-oferte-powiazana-z-produktem-D7Kj9gw4xFA).

Then, from this directory:

```bash
cp .env.example .env
chmod 600 .env
# Edit .env and fill in your Sandbox application's client ID and secret.
docker compose build
docker compose run --rm allegro login
```

Open the printed link in your normal browser and authorize the **Sandbox seller
account**. No callback server is needed. The CLI waits for authorization and
saves tokens. Later commands refresh tokens automatically.

## Sell a catalog product

```bash
docker compose run --rm allegro search "your product name"
# Barcodes are detected automatically (EAN/UPC/GTIN):
docker compose run --rm allegro search 195950051186

# Sell by barcode without copying a product UUID (replace PRICE):
docker compose run --rm allegro sell --ean 195950051186 --price PRICE --draft

# Use an ID returned by the Sandbox catalog search:
docker compose run --rm allegro sell --product PRODUCT_ID --price 49.99 --quantity 1
```

The catalog supplies product information. Your account supplies listing defaults.
The response is a short summary containing the offer ID and publication status. This command
requests immediate publication and exits successfully only if the resulting
offer is `ACTIVE`.

Search shows at most 10 products per page, with names, IDs, barcodes and key
variant details. Use `--limit 25` to display more, `--category CATEGORY_ID` to
filter by category, and `--page CURSOR` to retrieve the next page when offered.
Name searches go directly to Allegro; if a long name finds nothing, try a shorter
model name, such as `iPhone 16e`. The Sandbox catalog is separate from production.
The exact example `195950051186` / `Apple iPhone 16e SMARTFON` was verified in Sandbox.

Every command accepts `--json` for the complete API response, including filters,
image URLs, all parameters and validation details:

```bash
docker compose run --rm allegro search 195950051186 --json
docker compose run --rm allegro status OFFER_ID --json
```

`--ean` accepts 8, 12, 13 or 14 digits and passes an explicit GTIN reference to
Allegro. Search first to verify the correct product variant. Multiple matching
variants may require selecting a specific product ID instead.

Optional customization:

```bash
docker compose run --rm allegro settings
docker compose run --rm allegro sell \
  --product PRODUCT_ID --price 49.99 --quantity 2 \
  --title "My sample product title" \
  --description "A plain text description of the item." \
  --image https://your-site.example/product.jpg \
  --shipping-rate SHIPPING_RATE_ID
```

Descriptions passed as flags are plain text and escaped. Repeat `--image` for
multiple publicly accessible HTTPS images. Use a JSON file for structured HTML
descriptions, condition parameters, custom location, after-sales policy IDs or
product safety information. The short command relies on Allegro's default
condition, which is generally **new**; set the correct category-specific
condition in JSON when selling a used item.

Inspect without creating anything:

```bash
docker compose run --rm allegro sell --product PRODUCT_ID --price 49.99 --dry-run
```

Dry-run checks local structure and prints the exact payload. It does not validate
the product ID or category rules against Allegro, and does not need credentials.

Create a draft and publish it later:

```bash
docker compose run --rm allegro sell --product PRODUCT_ID --price 49.99 --draft
docker compose run --rm allegro status OFFER_ID
docker compose run --rm allegro publish OFFER_ID
```

## Full JSON and products outside the catalog

The `examples` directory is mounted read-only at `/offers` in the container.
Edit `examples/catalog-offer.json`, replacing its product ID, then run:

```bash
docker compose run --rm allegro sell --file /offers/catalog-offer.json --dry-run
docker compose run --rm allegro sell --file /offers/catalog-offer.json
```

For a new product, use `examples/custom-offer.json` as a **structural template**.
Replace every placeholder and image URL. Required parameters differ by category;
the template cannot be submitted as-is. Discover the current requirements:

```bash
docker compose run --rm allegro categories
docker compose run --rm allegro categories --parent CATEGORY_ID
docker compose run --rm allegro parameters LEAF_CATEGORY_ID
docker compose run --rm allegro product PRODUCT_ID
```

Select a leaf category. Parameters with `options.describesProduct: true` go in
`productSet[].product.parameters`; offer parameters go in top-level `parameters`.
Fill required values using the API's dictionary IDs or value fields. Add any
required manufacturer and safety information in `productSet[]`, following
[Allegro's product-offer documentation](https://developer.allegro.pl/tutorials/jak-jednym-requestem-wystawic-oferte-powiazana-z-produktem-D7Kj9gw4xFA).

```bash
docker compose run --rm allegro sell --file /offers/custom-offer.json --draft
docker compose run --rm allegro publish OFFER_ID
```

With `--file`, define price, stock and all customization in the file. `--draft`
and `--dry-run` are the only payload override flags; `--json` controls output. Without `--draft`, `sell` sets the
publication status to `ACTIVE`, even if the file says `INACTIVE`.

To use a file outside `examples`, add a read-only mount:

```bash
docker compose run --rm -v "$PWD/my-offer.json:/input/offer.json:ro" \
  allegro sell --file /input/offer.json
```

## Errors and recovery

- `401` or a refresh failure: verify the Sandbox app credentials and run `login`.
- `403`: check seller activation and the app's permissions, then authorize again.
- `422`: read the printed field errors; fix shipping/policy defaults, category
  parameters or safety fields. Use the JSON path for missing custom fields.
- An asynchronous request prints progress and waits up to 15 seconds by default,
  respecting `Retry-After`. Use `--wait-seconds 120` on `sell`, `publish` or
  `operation` to wait longer, or `--wait-seconds 0` to return immediately after
  acceptance. HTTP requests have their own 30-second network timeout, so the
  overall command can take longer than this processing wait.
- If processing remains pending, the CLI reports **accepted, still processing**,
  shows the current offer state if available, and prints recovery commands.
  Exit code **2** means pending, rather than a rejected listing (exit **1**).
  The snapshot may already show `ACTIVE`, but only the operation confirms
  completion and any operation-specific validation errors. `--json` returns a
  pending envelope with `processing`, `operation`, and `offer` fields.
  Resume processing without creating another offer:

  ```bash
  docker compose run --rm allegro operation '/sale/product-offers/OFFER_ID/operations/OPERATION_ID'
  ```

- After a timeout, network interruption or Ctrl-C during `sell`, check your
  offers on the Sandbox website before repeating the command. Allegro may
  already have created the offer. Use `status` or `operation` when its ID/path is
  known. Creation is never automatically retried after a network error.
- If the created offer is still inactive, correct it on Sandbox and use
  `publish OFFER_ID`; repeating `sell` creates another offer.

Run only one authenticated command at a time per volume. A local lock prevents
concurrent token refreshes. Browser login instructions and diagnostics go to stderr;
compact results go to stdout. Use `--json` for machine-readable API responses.
Dry-run always prints JSON. Exit 0 means completion, exit 1 means a
validation/API/publication failure, and exit 2 means accepted but still processing
(argparse also uses exit 2 for invalid command syntax).
`status` and `operation` inspect state; their successful exit means retrieval
succeeded, not necessarily that the offer is active.

```bash
docker compose run --rm allegro --help
docker compose run --rm allegro logout
```

Logout deletes the local token file; it does not revoke the app's authorization
on Allegro. `docker compose down` preserves credentials. Removing the named
volume (`docker compose down -v`) deletes them. All API/OAuth destinations are
fixed to Sandbox; this image has no production-mode switch.

## Without Compose

```bash
docker build -t alluploadgro:local .
docker run --rm --env-file .env -v allegro-data:/data alluploadgro:local login
docker run --rm --env-file .env -v allegro-data:/data \
  alluploadgro:local sell --product PRODUCT_ID --price 49.99
```

## Development and verification

Python 3.12+ on Linux:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
```

Run the same mock-based tests inside the built image:

```bash
docker run --rm --entrypoint python -v "$PWD/tests:/app/tests:ro" \
  alluploadgro:local -m unittest discover -s tests -v
```

Tests cover OAuth polling, token refresh/rotation, credential locking, trusted
operation URLs, asynchronous completion, CLI validation and offline payload
creation. Live login/publication requires your Sandbox credentials and activated
seller account; it is not part of the mock suite.

## License

[MIT](LICENSE)
