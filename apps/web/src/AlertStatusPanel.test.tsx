import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AlertStatusPanel } from './AlertStatusPanel';
const now = '2026-11-05T12:00:00Z';
function report() {
  return { status: 'READ_ONLY_ALERT_STATUS', as_of: now, is_stale: false,
    email: { state: 'NOT_CONFIGURED', expires_unix: null as number | null, remaining_seconds: null as number | null },
    queue: { total: 2, pending: 1, accepted: 1, critical_total: 1, critical_pending: 1, critical_accepted: 0,
      test_total: 1, test_pending: 0, test_accepted: 1, oldest_pending_at: '2026-11-05T10:35:00Z', oldest_pending_age_seconds: 5100, timing_consistent: true },
    latest_critical: { code: 'EMERGENCY_ENTERED', created_at: '2026-11-05T10:35:00Z', accepted_at: null as string | null,
      attempts: 0, last_failure: 'NONE', delivery_delay_seconds: null as number | null, timing_consistent: true },
    orders_enabled: false, live_trading_enabled: false, worker_liveness_checked: false,
    inbox_confirmation_available: false, confirmation_scope: 'RECEIVER_ACK_ONLY' };
}
function respond(body: unknown) { return { ok: true, status: 200, json: async () => body }; }
function mock(body: unknown) { vi.stubGlobal('fetch', vi.fn().mockResolvedValue(respond(body))); }
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });
describe('read-only urgent alert visibility', () => {
  it('separates pending risk from accepted TEST and warns on missing config', async () => {
    mock(report()); render(<AlertStatusPanel />);
    expect(await screen.findByText('설정 없음')).toBeTruthy();
    expect(screen.getByText('위험 알림 1건')).toBeTruthy();
    expect(screen.getByText('위험 알림 0건')).toBeTruthy();
    expect(screen.getByText('가장 오래 대기 · 1시간 25분')).toBeTruthy();
    expect(screen.getByText('시험 알림 1건 · 전체 수락 1건')).toBeTruthy();
    expect(screen.queryByRole('button')).toBeNull();
    expect(fetch).toHaveBeenCalledWith('/api/v1/demo/alerts/status', expect.objectContaining({ cache: 'no-store' }));
  });
  it('shows expiry without asserting worker health and updates when time passes', async () => {
    vi.useFakeTimers(); vi.setSystemTime(new Date(now));
    const body = report(); body.email = { state: 'CONFIGURED_PRIVATE_RAM', expires_unix: Date.now()/1000 + 2, remaining_seconds: 2 };
    mock(body); render(<AlertStatusPanel />); await act(async () => {});
    expect(screen.getByText('등록됨 · 작업자 상태 미확인')).toBeTruthy();
    expect(screen.getByText(/받은편지함 도착을 보장하지 않습니다/)).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(screen.getByText('유효기한 지남 · 재확인 필요')).toBeTruthy();
  });
  it('shows historical delay, excludes raw failure and does not label it current risk', async () => {
    const body = report(); body.latest_critical.accepted_at = now;
    body.latest_critical.delivery_delay_seconds = 5100; body.latest_critical.last_failure = 'fixture-secret';
    mock(body); render(<AlertStatusPanel />);
    expect(await screen.findByText('발생부터 전송 수락까지 · 1시간 25분')).toBeTruthy();
    expect(screen.getByText(/현재 계정의 위험 상태를 나타내지 않습니다/)).toBeTruthy();
    expect(screen.queryByText('fixture-secret')).toBeNull();
  });
  it.each(['orders_enabled', 'live_trading_enabled', 'worker_liveness_checked', 'inbox_confirmation_available'])(
    'rejects unsafe or unsupported assertion %s', async key => {
      mock({ ...report(), [key]: true }); render(<AlertStatusPanel />);
      expect(await screen.findByText(/알림 상태를 확인할 수 없습니다/)).toBeTruthy();
      expect(screen.queryByText('설정 없음')).toBeNull();
    });
  it('rejects prototype names and inconsistent totals', async () => {
    const body = report(); body.latest_critical.code = 'toString'; body.queue.total = 50;
    mock(body); render(<AlertStatusPanel />);
    expect(await screen.findByText(/알림 상태를 확인할 수 없습니다/)).toBeTruthy();
  });
  it('clears the previous report on failed refresh instead of showing zero or stale success', async () => {
    vi.useFakeTimers(); vi.setSystemTime(new Date(now));
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(respond(report())).mockRejectedValue(new Error('offline')));
    render(<AlertStatusPanel />); await act(async () => {});
    expect(screen.getByText('설정 없음')).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
    expect(screen.getByText(/알림 상태를 확인할 수 없습니다/)).toBeTruthy();
    expect(screen.queryByText('설정 없음')).toBeNull();
    expect(screen.queryByText('위험 알림 0건')).toBeNull();
  });
  it('refreshes config status and stops polling on unmount', async () => {
    vi.useFakeTimers(); vi.setSystemTime(new Date(now));
    const next = report(); next.email.state = 'BLOCKED_EXPIRED_OR_INVALID';
    const fetcher = vi.fn().mockResolvedValueOnce(respond(report())).mockResolvedValue(respond(next));
    vi.stubGlobal('fetch', fetcher); const view = render(<AlertStatusPanel />); await act(async () => {});
    await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
    expect(screen.getByText('만료 또는 설정 확인 필요')).toBeTruthy();
    view.unmount(); await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it('times out even if transport ignores cancellation', async () => {
    vi.useFakeTimers(); vi.stubGlobal('fetch', vi.fn().mockReturnValue(new Promise(() => {})));
    render(<AlertStatusPanel />);
    await act(async () => { await vi.advanceTimersByTimeAsync(8000); });
    expect(screen.getByText(/알림 상태를 확인할 수 없습니다/)).toBeTruthy();
  });
  it('does not turn unavailable queue data into zero pending', async () => {
    mock({ ...report(), status: 'UNAVAILABLE', is_stale: true, queue: null, latest_critical: null });
    render(<AlertStatusPanel />);
    expect(await screen.findByText(/알림 상태를 확인할 수 없습니다/)).toBeTruthy();
    expect(screen.queryByText('위험 알림 0건')).toBeNull();
  });
});
