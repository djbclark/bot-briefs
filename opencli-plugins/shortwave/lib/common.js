/**
 * Shared wiring. Command files must still call `cli(...)` themselves:
 * OpenCLI's plugin discovery only imports top-level files containing `cli(`.
 */
import { Strategy } from '@jackwener/opencli/registry';
import { ArgumentError } from '@jackwener/opencli/errors';

export const SHORTWAVE_DOMAIN = 'app.shortwave.com';
export const SHORTWAVE_BASE = {
  site: 'shortwave',
  domain: SHORTWAVE_DOMAIN,
  strategy: Strategy.COOKIE,
  browser: true,
  navigateBefore: false, // lib/session.js loads the app only when the tab is elsewhere
  siteSession: 'persistent',
};

export function positiveInt(value, name, { max } = {}) {
  const n = Number(value);
  if (!Number.isInteger(n) || n < 1) throw new ArgumentError(`--${name} must be a positive integer`);
  if (max && n > max) throw new ArgumentError(`--${name} must be at most ${max}`);
  return n;
}
