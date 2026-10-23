# Why does a healthy NVIDIA URL fail provider save? {#nvidia-url-denied}

Save does not test whether the URL is reachable. It rejects `integrate.api.nvidia.com` because that host is not on the operator allowlist.

- Save compares the hostname with `X_INSIGHT_PROVIDER_ALLOWED_HOSTS` and nothing else.[](source:require)
- An empty list denies every host, including a public API.[](source:hosts)
- The path is the POST target, and the list does not read it.[](source:endpoint)
- DNS and private-address checks run on Test, not on this save error.[](source:dns)

@answer scope="Save · exact host match"
@meta locale=en

@source require title="Exact-host save gate" path=backend/src/x_insight/reasoning/provider_config.py lines=288:314
@source hosts title="Allowlist from the process environment" path=backend/src/x_insight/reasoning/provider_config.py lines=283:285
@source save-call title="Save calls the gate before insert" path=backend/src/x_insight/reasoning/provider_config.py lines=396:397
@source dns title="DNS check used only after save" path=backend/src/x_insight/reasoning/provider_config.py lines=338:355
@source later-text title="Later diagnostic is a different sentence" path=backend/src/x_insight/reasoning/provider_config.py lines=510:513
@source endpoint title="Saved value is the full POST URL" path=backend/src/x_insight/reasoning/provider.py lines=149:158
@source post title="POST uses the saved URL unchanged" path=backend/src/x_insight/reasoning/provider.py lines=437:452
@source denial-test title="A normal HTTPS URL is denied when unlisted" path=backend/tests/http/test_api_settings.py lines=344:356
@source page title="The page shows the API message" path=web/src/features/admin/provider-settings/ProviderSettingsPage.tsx lines=21:27
@source install title="Restart the API after setting the list" path=docs/installation.md lines=290:296
@source make-dev title="make dev does not load .env into the API" path=docs/installation.md lines=128:130
@source readme title="Empty allowlist denies every destination" path=README.md lines=188:188
@source compose title="Production Compose passes the list to the API" path=compose.prod.yaml lines=63:64
@source worker title="Production Compose also passes the list to the worker" path=compose.prod.yaml lines=92:93

## What does save check? {#save-check}

The denial is this function, called before any settings row is inserted.[](source:save-call)

@workflow id=save-gate title="Save destination gate"
| lane submit | Submit |
| lane match | Match host |
| lane result | Result |
| form | Provider page | frontend | submit |
| put | PUT api-settings | backend | submit | source:save-call |
| host | Host on allowlist? | security | match | source:require |
| save | Save revision | database | result |
| deny | 422 denied | security | result | source:page |
| form > put | |
| put > host | |
| host > save | listed |
| host > deny | missing | emphasis |

@steps id=save-steps
| form | Provider page | The form sends the URL as typed. It does not hold the allowlist. | save-gate | form |
| put | PUT api-settings | Save calls the destination check before inserting a revision. | save-gate | put |
| host | Host on allowlist? | The match is the hostname only, and case does not matter. An empty list matches nothing. | save-gate | host |
| deny | 422 denied | The page shows `Provider base URL is not an allowed destination.` No row is written. | save-gate | deny |

@code id=gate-code source=require language=python

## Which part of this URL fails? {#url-parts}

Your URL passes scheme and userinfo. Only the host is missing from the list.

| Part | Value | Save gate |
| --- | --- | --- |
| Submitted URL | `https://integrate.api.nvidia.com/v1/chat/completions` | Split, then host-matched |
| Scheme | `https` | Allowed |
| Userinfo | none | Allowed |
| Host | `integrate.api.nvidia.com` | Must be listed {bad:Missing} |
| Path | `/v1/chat/completions` | Not read by the list |

The same 422 is returned for `https://example.com/v1` when the list is only `127.0.0.1`.[](source:denial-test)

## What string must the list contain? {#allowlist-entry}

| Entry | Matches this host? |
| --- | --- |
| `integrate.api.nvidia.com` | Yes {good:Use this} |
| `Integrate.API.NVIDIA.com` | Yes |
| `https://integrate.api.nvidia.com/v1` | No {bad:Has a scheme} |
| `nvidia.com` | No |
| `*.nvidia.com` | No |
| `integrate.api.nvidia.com:443` | No |

@callout level=warning title="Exact host only"
| The list stores hostnames only.
| A scheme, path, port, or parent domain does not match.
| `nvidia.com` does not allow `integrate.api.nvidia.com`.

## Where does the running API read that list? {#list-source}

Save reads the API process environment on each request.[](source:hosts) A file edit is invisible until that process starts again with the new value.

| Place | Role on this error |
| --- | --- |
| Provider page | Sends the URL. It does not hold the list.[](source:page) |
| API process environment | The only list save reads. |
| Example allowlist | Empty, so a copy denies every host.[](source:readme) {bad:Denies all} |
| Worker environment | Not used on save. Needed later for generation.[](source:worker) |

@callout level=warning title="Restart after the edit"
| `make dev` does not load `.env` into the API.[](source:make-dev)
| Export the file in that shell, then restart the API.[](source:install)
| Code reload does not pick up a new list.
| This checkout has no `.env` file. The API you are saving against may be another process.

## What should you change? {#fix}

Keep the full POST URL in the form. The app posts to that string unchanged.[](source:post)

- [ ] Add `integrate.api.nvidia.com` to `X_INSIGHT_PROVIDER_ALLOWED_HOSTS`, with no scheme, path, or port
- [ ] Export that value into the shell that starts the API, then restart the API
- [ ] Give the worker the same list before generation, then restart the worker
- [ ] Save `https://integrate.api.nvidia.com/v1/chat/completions` as the endpoint
- [ ] If Test says `Resolved address is not an allowlisted IP.`, treat that as the later DNS check[](source:later-text)

@callout level=note title="This checkout"
| No `.env` is present here, and the example list is empty.
| Those files do not prove what your running API has loaded.
