# alluploadgro

Publish **Buy Now offers on Allegro Sandbox** from the command line.

A small Dockerized CLI (Python 3.12, one dependency) that handles Allegro's
OAuth device flow, refreshes tokens for you, searches the Sandbox catalog by
name or barcode, and creates offers from a few flags or a full product-offer
JSON file. Login tokens persist in a Docker volume. No server, no exposed ports,
no database, no build step.

> **Sandbox only.** Every API and OAuth destination is pinned to
> `*.allegrosandbox.pl` and there is no production switch. Nothing in this tool
> can touch a live Allegro account.

```bash
allegro login
allegro search "iPhone 16e"
allegro sell --ean 195950051186 --price 49.99 --draft
```

- Prebuilt image: `ghcr.io/majorlupa/alluploadgro:latest` (`linux/amd64`, `linux/arm64`)
- License: [MIT](LICENSE)

## Contents

- [Quick start](#quick-start)
- [Sell a catalog product](#sell-a-catalog-product)
- [Create a draft and publish it later](#create-a-draft-and-publish-it-later)
- [Full JSON and products outside the catalog](#full-json-and-products-outside-the-catalog)
- [All commands](#all-commands)
- [Errors and recovery](#errors-and-recovery)
- [Build from source](#build-from-source)
- [Development](#development)

## Quick start

### 1. Register a Sandbox app (one-time, on Allegro's side)

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

You now have a **client ID** and **client secret** for the app.

### 2. Store your credentials

```bash
mkdir -p ~/.config/alluploadgro ~/allegro-offers

cat > ~/.config/alluploadgro/env <<'EOF'
ALLEGRO_CLIENT_ID=your-client-id
ALLEGRO_CLIENT_SECRET=your-client-secret
EOF

chmod 600 ~/.config/alluploadgro/env
```

The credentials file is passed to the container with `--env-file`; nothing is
baked into the image. `~/allegro-offers` is a scratch directory for offer JSON
files, mounted read-only at `/offers` in the steps below.

### 3. Define a shortcut

So you do not have to repeat the `docker run` flags:

```bash
allegro() {
  docker run --rm -i \
    --env-file ~/.config/alluploadgro/env \
    -v alluploadgro-data:/data \
    -v "$HOME/allegro-offers:/offers:ro" \
    ghcr.io/majorlupa/alluploadgro:latest "$@"
}
```

Add it to `~/.bashrc` or `~/.zshrc` to keep it across shells. Everywhere this
README writes `allegro <args>`, the portable equivalent is the same command with
the alias expanded:

```bash
docker run --rm -i \
  --env-file ~/.config/alluploadgro/env \
  -v alluploadgro-data:/data \
  -v "$HOME/allegro-offers:/offers:ro" \
  ghcr.io/majorlupa/alluploadgro:latest <args>
```

Use that full form on Windows/PowerShell, in CI, or in a Makefile. If you would
rather not repeat it, define the equivalent PowerShell function:

```powershell
function allegro { docker run --rm -i --env-file "$HOME\.config\alluploadgro\env" `
  -v alluploadgro-data:/data -v "$HOME\allegro-offers:/offers:ro" `
  ghcr.io/majorlupa/alluploadgro:latest @args }
```

### 4. Log in

```bash
allegro login
```

Open the printed link in your normal browser and authorize the **Sandbox seller
account**. No callback server is needed. The CLI waits for authorization and
saves the tokens into the `alluploadgro-data` volume. Later commands refresh
those tokens automatically.

Then try a search:

```bash
allegro search "iPhone 16e"
```

Skipping the setup already? The image is public, so `docker pull
ghcr.io/majorlupa/alluploadgro:latest` works without authenticating to GitHub.

## Sell a catalog product

```bash
allegro search "your product name"

# Barcodes are detected automatically (EAN/UPC/GTIN):
allegro search 195950051186

# Sell by barcode without copying a product UUID (replace PRICE):
allegro sell --ean 195950051186 --price PRICE --draft

# Use an ID returned by the Sandbox catalog search:
allegro sell --product PRODUCT_ID --price 49.99 --quantity 1
```

The catalog supplies product information. Your account supplies listing
defaults. The response is a short summary containing the offer ID and
publication status. This command requests immediate publication and exits
successfully only if the resulting offer is `ACTIVE`.

Search shows at most 10 products per page, with names, IDs, barcodes and key
variant details. Use `--limit 25` to display more, `--category CATEGORY_ID` to
filter by category, and `--page CURSOR` to retrieve the next page when offered.
Name searches go directly to Allegro; if a long name finds nothing, try a shorter
model name, such as `iPhone 16e`. The Sandbox catalog is separate from
production. The exact example `195950051186` / `Apple iPhone 16e SMARTFON` was
verified in Sandbox.

Every command accepts `--json` for the complete API response, including filters,
image URLs, all parameters and validation details:

```bash
allegro search 195950051186 --json
allegro status OFFER_ID --json
```

`--ean` accepts 8, 12, 13 or 14 digits and passes an explicit GTIN reference to
Allegro. Search first to verify the correct product variant. Multiple matching
variants may require selecting a specific product ID instead.

Optional customization:

```bash
allegro settings

allegro sell \
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
allegro sell --product PRODUCT_ID --price 49.99 --dry-run
```

Dry-run checks local structure and prints the exact payload. It does not validate
the product ID or category rules against Allegro, and does not need credentials
or network access.

## Create a draft and publish it later

```bash
allegro sell --product PRODUCT_ID --price 49.99 --draft
allegro status OFFER_ID
allegro publish OFFER_ID
```

## Full JSON and products outside the catalog

The container reads offer files from `/offers`, which the shortcut mounts
read-only from `~/allegro-offers`. Copy the templates out of this repository (or
write your own) and drop them there:

```bash
curl -fsSL -o ~/allegro-offers/catalog-offer.json \
  https://raw.githubusercontent.com/majorlupa/alluploadgro/main/examples/catalog-offer.json
```

Replace the product ID, then:

```bash
allegro sell --file /offers/catalog-offer.json --dry-run
allegro sell --file /offers/catalog-offer.json
```

For a new product, use
[`examples/custom-offer.json`](examples/custom-offer.json) as a **structural
template**. Replace every placeholder and image URL. Required parameters differ
by category; the template cannot be submitted as-is. Discover the current
requirements:

```bash
allegro categories
allegro categories --parent CATEGORY_ID
allegro parameters LEAF_CATEGORY_ID
allegro product PRODUCT_ID
```

Select a leaf category. Parameters with `options.describesProduct: true` go in
`productSet[].product.parameters`; offer parameters go in top-level `parameters`.
Fill required values using the API's dictionary IDs or value fields. Add any
required manufacturer and safety information in `productSet[]`, following
[Allegro's product-offer documentation](https://developer.allegro.pl/tutorials/jak-jednym-requestem-wystawic-oferte-powiazana-z-produktem-D7Kj9gw4xFA).

```bash
allegro sell --file /offers/custom-offer.json --draft
allegro publish OFFER_ID
```

With `--file`, define price, stock and all customization in the file. `--draft`
and `--dry-run` are the only payload override flags; `--json` controls output.
Without `--draft`, `sell` sets the publication status to `ACTIVE`, even if the
file says `INACTIVE`.

To read a file from somewhere else, mount it explicitly:

```bash
docker run --rm -i \
  --env-file ~/.config/alluploadgro/env \
  -v alluploadgro-data:/data \
  -v "$PWD/my-offer.json:/input/offer.json:ro" \
  ghcr.io/majorlupa/alluploadgro:latest sell --file /input/offer.json
```

## All commands

```bash
allegro --help
```

| Command | Purpose |
| --- | --- |
| `login` | Authorize via a browser link and save tokens |
| `logout` | Remove locally saved tokens |
| `search PHRASE` | Search the Sandbox product catalog (name or GTIN) |
| `product ID` | Show catalog product details |
| `categories [--parent ID]` | List categories; pick a leaf for custom products |
| `parameters CATEGORY_ID` | Show category parameters and required values |
| `settings` | List shipping rates, return policies and complaint policies |
| `sell` | Create and publish an offer (or a draft) |
| `status ID` | Show an offer's current status |
| `publish ID` | Activate an existing draft |
| `operation PATH` | Resume polling an asynchronous operation |

`sell` sources an offer from exactly one of `--product`, `--ean` or `--file`,
and accepts `--price`, `--quantity`, `--title`, `--description`, `--image`,
`--shipping-rate`, `--draft` and `--dry-run`. Every command accepts `--json`.
`sell`, `publish` and `operation` accept `--wait-seconds` (default `15`).

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
  allegro operation '/sale/product-offers/OFFER_ID/operations/OPERATION_ID'
  ```

- After a timeout, network interruption or Ctrl-C during `sell`, check your
  offers on the Sandbox website before repeating the command. Allegro may
  already have created the offer. Use `status` or `operation` when its ID/path is
  known. Creation is never automatically retried after a network error.
- If the created offer is still inactive, correct it on Sandbox and use
  `publish OFFER_ID`; repeating `sell` creates another offer.

Run only one authenticated command at a time per volume. A local lock prevents
concurrent token refreshes. Browser login instructions and diagnostics go to
stderr; compact results go to stdout. Use `--json` for machine-readable API
responses. Dry-run always prints JSON. Exit 0 means completion, exit 1 means a
validation/API/publication failure, and exit 2 means accepted but still processing
(argparse also uses exit 2 for invalid command syntax).
`status` and `operation` inspect state; their successful exit means retrieval
succeeded, not necessarily that the offer is active.

Logout deletes the local token file; it does not revoke the app's authorization
on Allegro. Deleting the volume (`docker volume rm alluploadgro-data`) removes
the credentials entirely. All API/OAuth destinations are fixed to Sandbox; this
image has no production-mode switch.

## Build from source

Clone the repository and use Compose to build the image locally instead of
pulling it:

```bash
git clone https://github.com/majorlupa/alluploadgro.git
cd alluploadgro
cp .env.example .env
chmod 600 .env
# Edit .env and fill in your Sandbox application's client ID and secret.
docker compose build          # or: docker compose pull, to use the published image
docker compose run --rm allegro login
```

The `compose.yaml` service is named `allegro`, so from that directory every
command in this README also works as
`docker compose run --rm allegro <args>`, with `examples/` mounted read-only at
`/offers`.

Note that the Compose volume (`alluploadgro_allegro-data`) is separate from the
`alluploadgro-data` volume used by the `docker run` shortcut above, so the two
approaches do not share a login. Pick one and stay with it, or run `login` once
per volume.

## Development

Python 3.12+ on Linux:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
```

Run the same mock-based tests inside the built image:

```bash
docker build -t alluploadgro:local .
docker run --rm --entrypoint python -v "$PWD/tests:/app/tests:ro" \
  alluploadgro:local -m unittest discover -s tests -v
```

Tests cover OAuth polling, token refresh/rotation, credential locking, trusted
operation URLs, asynchronous completion, CLI validation and offline payload
creation. Live login/publication requires your Sandbox credentials and activated
seller account; it is not part of the mock suite.

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs the suite on every
push and pull request, and publishes multi-architecture images to GHCR on
`main` and on `v*` tags.

## License

[MIT](LICENSE)
