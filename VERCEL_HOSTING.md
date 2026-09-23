# Hosting this on Vercel

Every step to put this app on Vercel and to update it afterwards, plus the
things that went wrong the first time so they do not have to go wrong again.

The live instance at <https://dissect-sepia.vercel.app> was deployed exactly
this way, and every number quoted below was measured on it.

- [What you get](#what-you-get)
- [Before you start](#before-you-start)
- [First deployment](#first-deployment)
- [Updating a deployment](#updating-a-deployment)
- [Rolling back](#rolling-back)
- [Environment variables](#environment-variables)
- [Verifying a deployment](#verifying-a-deployment)
- [Storage and housekeeping](#storage-and-housekeeping)
- [Things that will bite you](#things-that-will-bite-you)
- [Which files control the deployment](#which-files-control-the-deployment)

---

## What you get

Vercel runs the serverless tier. `server/runtime.py` detects it and turns off
what cannot work, and the UI shows the reason rather than a dead control.

| Works | Does not |
|---|---|
| Fast parsing: text, headings, ruled tables, figures, display equations | Deep parsing (Docling layout and TableFormer, roughly 2 GB) |
| BM25, dense vectors with the bundled ONNX encoder, hybrid fusion | OCR, CLIP visual search, cross encoder reranking |
| The reader, page rendering, All Views, notes, references, exports | Local LLM servers (Ollama, LM Studio) |
| Streamed answers from any of the 19 hosted providers | Background work after a response is sent |

A function may run for 300 seconds, which is enough for the fast parser on a
large paper: ColPali, 26 pages and 20 figures, takes about 18 seconds including
indexing.

Deep parsing is not a matter of paying Vercel more. The layout models do not
fit in a serverless bundle at all. Use the Docker image, a container on Render,
or a VM if you need them.

---

## Before you start

**A Vercel account.** The free Hobby plan is enough.

**A Postgres database.** This is not optional. Each serverless instance gets
its own empty `/tmp`, so without a shared database a paper uploaded through one
instance is invisible to the next and gone within minutes. Measured before one
was attached: of twelve concurrent requests, eight saw an uploaded paper and
four saw an empty library, and a few minutes later none of them did.

Neon's free tier works and does not expire. Create a project and copy the
**pooled** connection string, the one whose host contains `-pooler`. Serverless
functions open a connection per invocation and the pooler is what stops that
exhausting the database.

**Node 20 or newer**, for the CLI and the Next.js build.

**A Vercel token with full access to the scope you are deploying into.** Create
it at <https://vercel.com/account/settings/tokens>. A restricted token is not
enough and fails in a way that reads as a broken credential:

    $ vercel link
    Loading teams...
    Error: The token provided via VERCEL_TOKEN environment variable is not valid.

The token is valid. The CLI lists teams before anything else, a restricted
token may not, and the API is blunter about it:

    {"error":{"code":"forbidden","message":"You don't have permission to create the project."}}

---

## First deployment

### 1. Install the CLI

```bash
npm i -g vercel@latest
vercel --version
```

### 2. Link the directory to a project

```bash
export VERCEL_TOKEN=...                 # the full access token
vercel link --yes --project dissect --scope <your-team-slug>
```

This writes `.vercel/project.json`, which holds the project and org ids and is
gitignored. It also writes `.env.local`, which is ignored too.

Run `vercel teams ls` if you do not know your scope slug.

### 3. Point it at the database

```bash
echo 'postgresql://USER:PASSWORD@HOST-pooler.REGION.aws.neon.tech/neondb?sslmode=require' \
  | vercel env add DATABASE_URL production --scope <your-team-slug>
```

Repeat with `preview` and `development` if you want those environments to work
as well. `sslmode=require` is added automatically if you leave it out, because
Neon and Supabase both need TLS and neither always says so in the URL they
hand you.

Do not set any model provider keys. The app is designed to run without them:
retrieval, parsing and the whole browsing surface need none, and a visitor who
wants written answers pastes their own key in Settings, which is kept in their
browser and sent only to the provider it belongs to. A deployment with no keys
cannot spend your quota.

### 4. Deploy

```bash
vercel deploy --prod --yes --scope <your-team-slug>
```

The build takes about 30 to 50 seconds. Expect this line, which is not a
problem:

    Bundle size (355.67 MB) exceeds the standard size; optimizing dependencies.

The CLI prints the deployment URL and the alias it was promoted to.

### 5. Create the schema

The first request does it. The tables are created on demand, so:

```bash
curl -s https://<your-app>.vercel.app/api/health | python -m json.tool
```

Then go to [Verifying a deployment](#verifying-a-deployment), which is worth
doing properly the first time.

---

## Updating a deployment

The same command. It redeploys to the same project and the same alias:

```bash
vercel deploy --prod --yes --scope <your-team-slug>
```

Nothing in the database is touched, so papers, notes and threads survive. New
tables and columns are added on the next request: `create_all` runs every
`CREATE TABLE IF NOT EXISTS`, then the `ADDED_COLUMNS` migrations, each inside
a savepoint so one that has already been applied cannot take the rest with it.

To deploy on every push instead, connect the repository once:

```bash
vercel git connect --scope <your-team-slug>
```

After that a push to `main` deploys to production and a push to any other
branch gets a preview URL. The live instance is deployed from the CLI, so that
path is the tested one here.

---

## Rolling back

```bash
vercel ls dissect --scope <your-team-slug>          # find the previous URL
vercel rollback <deployment-url> --scope <your-team-slug>
```

A rollback replaces the code and not the data. If the version you are rolling
back to predates a schema change, the old code simply ignores the new columns,
which is why every migration here adds rather than renames.

---

## Environment variables

| Variable | Needed | What it does |
|---|---|---|
| `DATABASE_URL` | **yes** | Postgres. Without it nothing survives the request that created it. |
| `APP_PASSWORD` | no | Requires a password on every request. Set this if the instance should not be public. |
| `PUBLIC_BASE_URL` | no | The instance's own URL, used when it needs to build an absolute link. |
| `MAX_JOB_SECONDS` | no | Defaults to 275, just under the function's 300. Raise only with the plan to match. |
| `PARSE_MODE` | no | `fast` is the only mode available here and is chosen automatically. |
| Provider keys | no | Deliberately unset. See step 3. |

```bash
vercel env ls --scope <your-team-slug>
vercel env rm NAME production --yes --scope <your-team-slug>
```

Changing a variable does not redeploy. Deploy again for it to take effect.

---

## Verifying a deployment

A build that succeeds proves the code compiled and nothing else. These are the
checks worth running, in order, because each one fails differently.

**Capabilities.** Confirms the platform was detected and the encoder is in the
bundle:

```bash
curl -s https://<your-app>.vercel.app/api/health | python -c "
import sys, json
d = json.load(sys.stdin)['runtime']
print(d['platform'], d['tier'])
for c in d['capabilities']:
    print(('  yes ' if c['available'] else '  no  ') + c['id'])
"
```

`onnx_models` must say yes. If it does not, `includeFiles` in `vercel.json` did
not pick up `Semantic Models/`, and retrieval will silently fall back to
keywords only.

**Ingest a paper.** The first invocation installs runtime dependencies into
`/tmp` and takes a few seconds longer, so warm it first:

```bash
curl -s -o /dev/null https://<your-app>.vercel.app/api/health
curl -s -X POST https://<your-app>.vercel.app/api/papers/fetch \
  -H 'Content-Type: application/json' \
  -d '{"source":"1706.03762","dense":true}'
```

Expect 219 elements and 4 tables for that paper. Four is the number it has. A
much larger number means table detection has regressed into body prose.

**The check that actually matters.** Whether the library is visible from every
instance, which is the thing a database is there to fix:

```bash
for i in $(seq 1 12); do
  curl -s https://<your-app>.vercel.app/api/papers \
    | python -c "import sys,json; print(len(json.load(sys.stdin)['papers']), end=' ')" &
done; wait; echo
```

Every number must be the same. A mixture means `DATABASE_URL` is not set, or
not set on the environment you deployed.

**Blobs, defeating the CDN.** Page renders are cached at the edge, so a plain
repeat request can return 200 from the cache while the function behind it is
failing. Add a cache buster:

```bash
curl -s -o /dev/null -w '%{http_code} %{size_download}\n' \
  "https://<your-app>.vercel.app/api/papers/1/page/4?cb=$RANDOM"
```

**Logs, when something returns 500.** The traceback is in the runtime logs and
nowhere else:

```bash
vercel logs https://<your-app>.vercel.app --scope <your-team-slug>
```

---

## Storage and housekeeping

With no persistent disk, PDFs, figure crops and cached page renders go into the
database and the filesystem is only a read cache. That means a quota to watch.

```bash
curl -s https://<your-app>.vercel.app/api/health | python -c "
import sys, json
s = json.load(sys.stdin)['storage']
print(f\"{s['blob_bytes']/1e6:.1f} MB, in database: {s['blobs_in_database']}\")
"
```

Papers differ by more than an order of magnitude. Measured: "Attention Is All
You Need" costs 2.8 MB, ColPali costs 31 MB because of its twenty figures and
page count. Neon's free tier is 512 MB.

Removing a paper through the UI or `DELETE /api/papers/{id}` frees its storage.
It cannot simply delete the paper's blobs, because storage is content addressed
and shared, so it sweeps afterwards for blobs that no surviving paper refers
to, and the response says how much it freed:

    {"ok":true,"blobs_removed":9,"bytes_freed":2798174}

A failed ingest or a re-parse can strand bytes without deleting a paper. The
same sweep runs on its own, against whatever `DATABASE_URL` points at:

```bash
DATABASE_URL='postgresql://...' EPHEMERAL_DISK=1 BLOB_DIR=/tmp/sweep \
  python -m server.ops.cli gc
```

`EPHEMERAL_DISK=1` makes your local machine take the no-disk path so it sweeps
the database rather than your own files, and `BLOB_DIR` sends the local half of
the sweep somewhere harmless. Without both, it will look at your local library
instead.

---

## Things that will bite you

**`.vercelignore` replaces `.gitignore`, it does not add to it.** The moment
that file exists the CLI stops consulting `.gitignore` entirely. Everything
secret is therefore repeated in it, including `.env`, which in a working
checkout holds real provider keys. Leaving a rule out of that file is how a
local `.env` reaches a public deployment.

**A restricted token reads as an invalid one.** See
[Before you start](#before-you-start).

**No `DATABASE_URL` means no persistence at all**, and it fails silently: the
upload returns 200, the paper is parsed correctly, and it is gone. Nothing
errors, because nothing went wrong on the request that stored it.

**The encoder has to be pinned into the function.** `Semantic Models/` holds a
23 MB quantised ONNX export and an 88 MB PyTorch cache of the same model. Only
the first is uploaded and only the first is named in `includeFiles`. Shipping
both wastes the bundle budget; shipping neither loses dense retrieval.

**`memory` in `vercel.json` is ignored** on Active CPU billing and warns on
every deploy. It was removed for that reason.

**Cold starts can drop a concurrent request.** The first invocation installs
runtime dependencies into `/tmp`. During that window, requests were observed
failing after 21 seconds with no response at all. Warm the instance with a
`GET /api/health` before timing anything or running a test suite.

**A PDF whose text contains NUL bytes** used to fail only on Postgres, because
SQLite stores them and Postgres refuses them. ColPali's text layer carries
174576 of them. They are now stripped at the one point every query passes
through, on both dialects, so the same paper stores the same way everywhere.
This is fixed, and is here because it is the shape of bug to expect when a
deployment behaves differently from a laptop.

---

## Which files control the deployment

| File | What it decides |
|---|---|
| `vercel.json` | Framework, the 300 second limit, the `/api/*` rewrite, and which files are pinned into the function |
| `.vercelignore` | Everything that is **not** uploaded. Read it before adding secrets to the working directory |
| `api/index.py` | The ASGI entrypoint. Normalises the request path, which the rewrite can present in more than one form |
| `requirements.txt` | Must stay inside the function bundle limit. Anything heavier belongs in `requirements-server.txt`, which this target never installs |
| `server/runtime.py` | Detects the platform and decides which capabilities are available, and what reason to show for the ones that are not |
| `next.config.mjs` | In production `NEXT_PUBLIC_API_BASE` is empty, because Vercel serves `/api` same origin |
