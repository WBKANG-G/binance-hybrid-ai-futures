import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { App } from './App';

const health = { phase: 4, environment: 'PAPER', status: 'development_only', live_trading_enabled: false,
  orders_enabled: false, demo_trading_enabled: false, database: 'ready', as_of: '2026-10-19T03:00:00Z', is_stale: false };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('development status', () => {
  it('rejects implicit DEMO activation', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200,
      json: async () => ({ ...health, demo_trading_enabled: true }) }));
    render(<App />);
    expect(await screen.findByRole('alert')).toBeTruthy();
  });
  it('shows confirmed mode and disabled orders without invented balances', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => health }));
    render(<App />);
    expect(await screen.findByText('PAPER')).toBeTruthy();
    expect(screen.getByText('주문 실행 비활성')).toBeTruthy();
    expect(screen.getByText('연결 확인')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /주문|활성화/ })).toBeNull();
  });
  it('reports connection failure without showing a healthy state', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
    render(<App />);
    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(screen.queryByText('연결 확인')).toBeNull();
  });
  it('rejects an unsafe server response', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200,
      json: async () => ({ ...health, live_trading_enabled: true }) }));
    render(<App />);
    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(screen.queryByText('PAPER')).toBeNull();
  });
  it('reports database failure separately from an API failure', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 503,
      json: async () => ({ ...health, database: 'unavailable' }) }));
    render(<App />);
    expect(await screen.findByText('연결 불가')).toBeTruthy();
    expect(screen.getByText('PAPER')).toBeTruthy();
  });
  it('rejects incomplete payloads', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ phase: 4 }) }));
    render(<App />);
    expect(await screen.findByRole('alert')).toBeTruthy();
  });
});
