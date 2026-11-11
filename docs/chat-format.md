# Chat post format

How bots format list posts to the user in chat.

## Shape
- Major items: a **bold-italic number at the left margin**, then the text: `***1.*** text`.
- Sub-items: in a Markdown quote block directly under their item, each starting `> ***a.*** text`, with a lone
  `>` line between sub-items.

```markdown
***1.*** Dentist moved to Thursday 10:00. [calendar](https://example.com/event)
> ***a.*** Conflicts with the team sync; suggest moving the sync.
>
> ***b.*** Bring the new insurance card.

***2.*** Two invoices due this week.
> ***a.*** Hosting, $12, due Wed.
>
> ***b.*** Domain renewal, $15, due Fri.
```

## Why
Markdown does not treat `a.` as a list marker, so consecutive `a. ...` / `b. ...` lines merge into one
paragraph. The quote block indents the sub-items, and the lone `>` line keeps each one on its own line.
Bold-italic numbers stay readable whether or not the chat app renders ordered lists.

## Dashboard files keep the older shape
Report files that the dashboard parses (`report-to-items`, `brief import`) keep the original shape:
`**Most urgent:** ...`, section headers `**1. Title**`, item lines `1.1 text`. Only the chat post uses the
format above. If a bot writes one file for both, write the dashboard shape to the file and convert for chat.
