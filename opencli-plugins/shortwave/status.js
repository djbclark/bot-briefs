import { cli } from '@jackwener/opencli/registry';
import { AuthRequiredError } from '@jackwener/opencli/errors';
import { SHORTWAVE_BASE } from './lib/common.js';
import { readTodos } from './lib/session.js';

export const statusCommand = cli({
  ...SHORTWAVE_BASE,
  name: 'status',
  access: 'read',
  description: 'Check Shortwave login state and number of Todos',
  example: 'opencli shortwave status -f json',
  args: [],
  columns: ['login', 'todos'],
  func: async (page) => {
    try {
      const todos = await readTodos(page);
      return [{ login: 'yes', todos: todos.length }];
    } catch (e) {
      if (e instanceof AuthRequiredError) return [{ login: 'no', todos: '' }];
      throw e;
    }
  },
});
