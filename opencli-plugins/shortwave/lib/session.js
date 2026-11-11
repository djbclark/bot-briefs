/**
 * Shortwave read-only data access.
 *
 * Shortwave has no public API on the Pro plan. The web app keeps a local replica
 * in IndexedDB (`shortwave-db-acct-<uuid>`), so we read that from the adapter tab
 * on app.shortwave.com. Nothing is written, clicked, or sent.
 *
 * Todos: rows in `bundle_overrides` (title = displayName, else first rtfDescription
 * text) that have at least one visible thread in `threads` with
 * `bundleOverrideId === todo.id`. Link: https://app.shortwave.com/t/<clusterId>
 * where clusterId is from the newest message of the newest thread.
 */
import { AuthRequiredError, CommandExecutionError } from '@jackwener/opencli/errors';
import { SHORTWAVE_DOMAIN } from './common.js';

export const SHORTWAVE_APP_URL = 'https://app.shortwave.com/';
const LOAD_WAIT_S = 25;

// Runs in the page. Returns only todo metadata (no bodies, senders, snippets).
const READ_JS = `(async () => {
  const href = location.href;
  if (location.origin !== 'https://app.shortwave.com') return { state: 'elsewhere', href };
  if (/\\/(login|signin|sign-in|onboarding)/i.test(location.pathname)) return { state: 'login', href };
  const dbs = await indexedDB.databases();
  const info = dbs.find((d) => d.name && d.name.startsWith('shortwave-db-acct-'));
  if (!info) return { state: 'nodb', href };
  const db = await new Promise((res, rej) => { const q = indexedDB.open(info.name); q.onsuccess = () => res(q.result); q.onerror = () => rej(q.error); });
  try {
    const names = [...db.objectStoreNames];
    for (const s of ['bundle_overrides', 'threads']) if (!names.includes(s)) return { state: 'schema', missing: s };
    const all = (s) => new Promise((res, rej) => { const q = db.transaction(s).objectStore(s).getAll(); q.onsuccess = () => res(q.result); q.onerror = () => rej(q.error); });
    const [bundles, threads] = await Promise.all([all('bundle_overrides'), all('threads')]);
    const summaries = names.includes('thread_summaries') ? await all('thread_summaries') : [];
    const messages = names.includes('messages') ? await all('messages') : [];
    const sumBy = new Map(summaries.map((s) => [s.threadId, s]));
    const cluster = new Map(); // threadId -> [ts, clusterId]
    const clusterOfMsg = new Map();
    for (const m of messages) {
      if (typeof m.clusterId !== 'string' || !/^m-[0-9a-f-]{36}$/.test(m.clusterId)) continue;
      clusterOfMsg.set(m.id, m.clusterId);
      const ts = Number(m.timestamp) || 0, cur = cluster.get(m.threadId);
      if (!cur || ts >= cur[0]) cluster.set(m.threadId, [ts, m.clusterId]);
    }
    const byBundle = new Map();
    for (const t of threads) {
      if (!t.bundleOverrideId || t.inboxStatus !== 'visible') continue;
      if (!byBundle.has(t.bundleOverrideId)) byBundle.set(t.bundleOverrideId, []);
      byBundle.get(t.bundleOverrideId).push(t);
    }
    const firstText = (n, d = 0) => {
      if (!n || d > 12) return '';
      if (typeof n === 'string') return '';
      if (Array.isArray(n)) { for (const x of n) { const r = firstText(x, d + 1); if (r) return r; } return ''; }
      if (typeof n === 'object') {
        if (n.text && typeof n.text === 'object' && typeof n.text.contents === 'string' && n.text.contents.trim()) return n.text.contents.trim();
        if (typeof n.text === 'string' && n.text.trim()) return n.text.trim();
        for (const k of ['blocks', 'primary', 'node', 'nodes', 'items']) { const r = firstText(n[k], d + 1); if (r) return r; }
      }
      return '';
    };
    const todos = [];
    for (const b of bundles) {
      const ts = byBundle.get(b.id);
      if (!ts) continue;
      ts.sort((a, z) => (z.latestMessageAt || 0) - (a.latestMessageAt || 0));
      const links = [];
      let messagesN = 0, haveCounts = true;
      for (const t of ts) {
        const s = sumBy.get(t.id);
        let c = cluster.get(t.id)?.[1];
        if (!c && s?.latestMessage?.messageId) c = clusterOfMsg.get(s.latestMessage.messageId);
        links.push(c ? 'https://app.shortwave.com/t/' + c : null);
        if (s && Number.isFinite(s.messageCount)) messagesN += s.messageCount; else haveCounts = false;
      }
      const title = (typeof b.displayName === 'string' && b.displayName.trim()) || firstText(b.rtfDescription) || '';
      todos.push({
        title: title.replace(/\\s+/g, ' '),
        id: b.id,
        link: links[0] || null,
        thread_links: links.filter(Boolean),
        date: ts[0].latestMessageAt ? new Date(ts[0].latestMessageAt).toISOString() : null,
        count: ts.length,
        messages: haveCounts ? messagesN : null,
        _sort: Math.max(...ts.map((t) => t.sortTimestamp || 0)),
      });
    }
    todos.sort((a, z) => z._sort - a._sort);
    todos.forEach((t) => delete t._sort);
    return { state: 'ok', todos };
  } finally { db.close(); }
})()`;

async function readOnce(page) {
  return page.evaluate(READ_JS);
}

/** Read todos from the app's IndexedDB, loading the app in the adapter tab if needed. */
export async function readTodos(page, { waitS = LOAD_WAIT_S } = {}) {
  let r = await readOnce(page).catch(() => ({ state: 'elsewhere' }));
  if (r.state === 'elsewhere' || r.state === 'nodb') {
    await page.goto(SHORTWAVE_APP_URL);
    for (let i = 0; i < waitS; i++) {
      r = await readOnce(page).catch(() => ({ state: 'elsewhere' }));
      if (r.state === 'ok' || r.state === 'login') break;
      await page.sleep(1);
    }
  }
  if (r.state === 'ok') return r.todos;
  if (r.state === 'login' || r.state === 'nodb') {
    throw new AuthRequiredError(SHORTWAVE_DOMAIN, 'Not signed in to Shortwave in the box Chrome (no account database on app.shortwave.com)');
  }
  if (r.state === 'schema') throw new CommandExecutionError(`Shortwave IndexedDB schema changed (missing store ${r.missing})`);
  throw new CommandExecutionError(`Could not load ${SHORTWAVE_APP_URL} in the adapter tab`);
}
