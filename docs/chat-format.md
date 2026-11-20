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

## Sections: Roman numerals, numbered items
Every line item must be unambiguously referable. When a post has more than one section (for example findings
plus decisions), label each section with a Roman numeral and restart the item numbers inside it, so the user can
reply "I.2 yes" or "II.1a":

```markdown
**I. Findings**

***1.*** Backup ran clean.

***2.*** Two certificates expire this month.
> ***a.*** Web front end, 9 days.
>
> ***b.*** Mail relay, 21 days.

**II. Decisions needed**

***1.*** Renew both certificates now? (yes / later)
```

## Lettering, a third level, and citations
- Sub-item letters skip `i` (it reads like a Roman numeral), so they run `a` to `h`, then `j` onward. After
  `z`, continue with `aa`, `ab`, ...
- A third level goes in a nested quote under its sub-item, labeled with bold-italic lowercase Roman numerals:
  `> > ***ii.*** text`.
- To cite an item, give the post's Ref id, then the path: `CHIEF-tt I.3b.ii` (section I, item 3, sub-item b,
  third-level ii). When replying to the latest post, the Ref id can be dropped: `I.3b.ii`.

```markdown
***3.*** Renewals this month.
> ***a.*** Hosting.
> > ***i.*** Plan A, $12.
> >
> > ***ii.*** Plan B, $20.
>
> ***b.*** Domain.
```

A single-section post skips the Roman numerals. The post still ends with one `Ref:` line
([phase-15-rules.md](phase-15-rules.md) rule 1).

## Why
Markdown does not treat `a.` as a list marker, so consecutive `a. ...` / `b. ...` lines merge into one
paragraph. The quote block indents the sub-items, and the lone `>` line keeps each one on its own line.
Bold-italic numbers stay readable whether or not the chat app renders ordered lists.

## Dashboard files keep the older shape
Report files that the dashboard parses (`report-to-items`, `brief import`) keep the original shape:
`**Most urgent:** ...`, section headers `**1. Title**`, item lines `1.1 text`. Only the chat post uses the
format above. If a bot writes one file for both, write the dashboard shape to the file and convert for chat.
