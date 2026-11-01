# Saner.AI web-app protocol notes

Recorded 2026-09-24 from the `app.saner.ai` frontend (Next.js bundle) and read-only requests made with the author's own session. The field lists below are structural. No credentials, account identifiers or personal content are recorded here.

## Hosts

| Host | Role |
|---|---|
| `server.saner.ai` | Supabase auth (`/auth/v1/...`) |
| `api.saner.ai/api/v1/admin` | REST resources (the frontend's `backendUrl`) |
| `api.saner.ai/api/v2/admin` | a few v2 routes (`/tasks`) |
| `aisrv.saner.ai/api/v1` | AI: chat streams and search |

## Auth

1. The session is a Supabase SSR cookie on `app.saner.ai`: `sb-server-auth-token`, split into `.0`, `.1`, … chunks. Its value is URL-encoded JSON (newer `@supabase/ssr` versions prefix `base64-`) with the fields `access_token, token_type, expires_in, expires_at, refresh_token, user`.
2. Every API call sends `Authorization: Bearer <access_token>`, `X-Request-ID: <uuid v4>` and JSON content headers. Browser `fetch` with `credentials: 'include'` fails CORS; cookies are not the API credential.
3. Access tokens last about one hour, and the web app refreshes them itself.

## REST endpoints used

| Method & path | Notes |
|---|---|
| `GET /ai-profiles/me` | `plan`, timezone, instructions. The response also contains `ciphered_*_API_KEY` fields: never output them |
| `GET /user-plan-rate-limits/me` | `message_limit_balance`, `message_limit_reset_time`, `ai_request_limit_balance`, … |
| `GET /conversations?page=0&limit=0` | all conversations; `limit=0` means "no limit" across this API |
| `GET /conversations/search?query=` | conversations with matching `messages` |
| `GET /conversations/:id` | `id, title, is_proactive, system_sid_mapping, created_at` |
| `GET /conversations/:id/messages?limit=0` | `{ data, page, total, limit, pages }`; message fields `id, conversation_id, sender, receiver, content, metadata, blocks, created_at` |
| `POST /conversations` | `{ id, title, is_proactive }`; the client generates the UUID |
| `PUT /conversations/:id` | `{ title?, system_sid_mapping? }` |
| `POST /messages` | insert one message (see the chat flow below) |
| `GET /tasks?checked=&limit=0&order_by=manual&order_dir=asc&priority=all[&has_due_date=true]` | `{ data: [task] }`; task fields `id, content, priority, date_time, reminder_at, checked, parent_task_id, recurrence_rule, note_id, …` |
| `GET /notes/explorer-view` | all notes, lightweight: `id, type, source, title, folder_id, preview_content, created_at, updated_at` |
| `GET /notes/:id` | full note; `content` is ProseMirror/TipTap JSON (`{type:"doc",content:[…]}`); connector notes (Gmail, calendar) may carry HTML in `preview_content` |

Also present in the bundle but not used yet: task upsert, batch and delete (`/tasks/upsert`, `/tasks/batch`, …); note create, rename, append-content and tags; daily notes (`/notes/dates/:date`); folders; connectors; `/messages/:id/change-model` and `/renew`; `/conversations/:id/notes` (generate a note from a conversation).

## Search (aisrv)

| Path | Result |
|---|---|
| `GET /search/smart?query=` | `{ data: [{ id, title, highlight_title, highlight_content, folder_name, folder_breadcrumb, type, source, relevance, … }], trace_id }` |
| `GET /search/semantic?query=&content_type=note` | same shape plus `content`; the highlights are objects |
| `GET /search/similar?note_id=` | similar notes |

The client ignores queries of 2 characters or fewer.

## Chat flow (client-orchestrated)

The browser persists both sides of every exchange.

1. **New conversation only:** `POST /conversations {id: uuid, title: <prompt>, is_proactive: false}`.
2. `POST /messages {id: uuid, conversation_id, sender: "user", receiver: <bot>, content: <prompt>, metadata: {reason_enabled, attachments: [], selected_notes: [], selected_tasks: []}, created_at, updated_at}`.
3. `POST aisrv` stream, `Accept: text/event-stream`:
   1. The endpoint is `/chat/skai/stream`, `/chat/skai_reasoning/stream` (reasoning on), `/chat/online/stream` (web), or `/chat/external_chat/stream` (external models such as `gpt-5.4`, `claude-opus-4.6`, `gemini-3.1-pro`, `grok-4`).
   2. For skai and online, the body is `{input: {chat_history, question, current_time, is_external_notes, [tasks], [notes], selected_suggestion_blocks, attachments, sid_mapping}}`.
   3. For external models, it is `{input: {…same…, model: <bot>, message_id: <user message id>}}` without `attachments`.
   4. `current_time` is `dayjs().format()`: local ISO-8601 with a UTC offset.
   5. `chat_history` comes from the web app's `buildChatHistory`: a leading assistant message on its own, then only complete user→assistant pairs, each `{sender, receiver, content, selected_notes, selected_tasks, selected_suggestion_blocks, attachments}`. Assistant entries add `references: [{id, title}]` and `reasoning`.
4. Stream events:
   1. `event: data` frames carry `{tag, content}` with these tags:
      1. `response.text`: an answer delta.
      2. `response.full_text`: the whole answer.
      3. `conversation.title`: the generated title.
      4. `recall.references`: the notes used.
      5. `recall.web_sources`: the web pages used.
      6. `system.message`: `{status, content}`.
      7. `system.sid_mapping`: an object merged into the conversation mapping.
      8. `thinking.status`, `thinking.text` and `thinking.section.recall.status`.
   2. `event: error` frames carry `{errors: [{message}]}`.
   3. The client inserts `\n\n` before each fenced `skai_block_*` in answer chunks.
5. `PUT /conversations/:id {title, system_sid_mapping}` runs when the streamed title differs or the mapping is non-empty.
6. `POST /messages {conversation_id, sender: <bot>, receiver: "user", content: <answer>, metadata: {reason_enabled, reason, recall_references, web_sources, follow_up_blocks, errors, error, web_url, ftsearchResults, system_messages, searchTerm}, created_at, updated_at}`. The server assigns this message's id.

### Skai UI blocks inside answers

Answers can embed fenced blocks: `skai_block_option_selector` (a plain numbered list), `skai_block_references`, `skai_block_note_editor`, `skai_block_tasks_editor`, `skai_block_task_editor`, `skai_block_instruction_editor`, `skai_block_fetch`, `skai_block_apply` and `skai_block_system_action`. The editors are proposals the user applies in the UI.
