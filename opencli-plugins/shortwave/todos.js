import { cli } from '@jackwener/opencli/registry';
import { SHORTWAVE_BASE, positiveInt } from './lib/common.js';
import { readTodos } from './lib/session.js';

export const todosCommand = cli({
  ...SHORTWAVE_BASE,
  name: 'todos',
  access: 'read',
  description: 'List Shortwave Inbox Todos (read-only)',
  example: 'opencli shortwave todos -f json',
  args: [{ name: 'limit', type: 'int', default: 500, help: 'Max todos to return' }],
  columns: ['title', 'date', 'count', 'link'],
  func: async (page, kwargs) => {
    const limit = positiveInt(kwargs.limit ?? 500, 'limit', { max: 5000 });
    return (await readTodos(page)).slice(0, limit);
  },
});
