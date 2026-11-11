# dodo-link

Private HTTPS redirect for Dodosend thread links. Chat apps often block
`dodosend://` custom-scheme links, so this page accepts a normal https URL
and hands off to the Mac app.

Live: https://dodo-link.pages.dev/

Source: https://github.com/djbclark/bot-briefs/tree/main/dodo-link

## Usage

- `https://dodo-link.pages.dev/t/<threadId>`
- `https://dodo-link.pages.dev/t/<threadId>?account=you@example.com`

`threadId` must be a 12–16 character hexadecimal Gmail thread id. The
`account` query is optional; if present it is passed through to Dodosend
and used in the Gmail fallback link.

## Deploy

From this directory, set `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`
in the environment. Do not commit those values. The account id is not a
secret, but keep it out of the repo and set it as an environment variable.

```bash
npx wrangler@4 pages deploy site --project-name dodo-link
```

Cloudflare Pages project name: `dodo-link`.
