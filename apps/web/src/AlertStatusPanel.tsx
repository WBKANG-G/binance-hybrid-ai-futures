import { useEffect, useState } from 'react';

type EmailState = 'CONFIGURED_PRIVATE_RAM' | 'NOT_CONFIGURED' | 'BLOCKED_EXPIRED_OR_INVALID' | 'UNKNOWN';
interface Queue {
  total: number; pending: number; accepted: number;
  critical_total: number; critical_pending: number; critical_accepted: number;
  test_total: number; test_pending: number; test_accepted: number;
  oldest_pending_at: string | null; oldest_pending_age_seconds: number | null;
  timing_consistent: boolean;
}
interface Recent {
  code: string; created_at: string; accepted_at: string | null;
  attempts: number; last_failure: string; delivery_delay_seconds: number | null;
  timing_consistent: boolean;
}
interface AlertReport {
  status: 'READ_ONLY_ALERT_STATUS' | 'UNAVAILABLE'; as_of: string; is_stale: boolean;
  email: { state: EmailState; expires_unix: number | null; remaining_seconds: number | null };
  queue: Queue | null; latest_critical: Recent | null;
  orders_enabled: false; live_trading_enabled: false;
  worker_liveness_checked: false; inbox_confirmation_available: false;
  confirmation_scope: 'RECEIVER_ACK_ONLY';
}
const emailStates = ['CONFIGURED_PRIVATE_RAM', 'NOT_CONFIGURED', 'BLOCKED_EXPIRED_OR_INVALID', 'UNKNOWN'];
const criticalCodes: Record<string, string> = {
  EMERGENCY_ENTERED: '비상 상태 전환', STOP_PROTECTION_FAILURE: '손절 보호 확인 실패',
  EXIT_OUTCOME_UNKNOWN: '종료 결과 확인 필요', RISK_LOCK_ENTERED: '위험 잠금 전환',
  PRIVATE_SESSION_FAILED: '비공개 연결 확인 실패',
};
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('invalid status');
  return value as Record<string, unknown>;
}
function count(value: unknown): boolean { return Number.isSafeInteger(value) && Number(value) >= 0; }
function date(value: unknown): boolean { return typeof value === 'string' && Number.isFinite(Date.parse(value)); }
function nullableDate(value: unknown): boolean { return value === null || date(value); }
function nullableCount(value: unknown): boolean { return value === null || count(value); }
function parse(value: unknown): AlertReport {
  const r = object(value); const email = object(r.email);
  if (!['READ_ONLY_ALERT_STATUS', 'UNAVAILABLE'].includes(String(r.status)) || !date(r.as_of) ||
      typeof r.is_stale !== 'boolean' || r.orders_enabled !== false || r.live_trading_enabled !== false ||
      r.worker_liveness_checked !== false || r.inbox_confirmation_available !== false ||
      r.confirmation_scope !== 'RECEIVER_ACK_ONLY' || !emailStates.includes(String(email.state))) throw new Error('unsafe status');
  if (email.state === 'CONFIGURED_PRIVATE_RAM') {
    if (!count(email.expires_unix) || Number(email.expires_unix) <= 0 ||
        !count(email.remaining_seconds) || Number(email.remaining_seconds) <= 0 || Number(email.remaining_seconds) > 43200) throw new Error('invalid expiry');
  } else if (email.expires_unix !== null || email.remaining_seconds !== null) throw new Error('invented expiry');
  if (r.status === 'UNAVAILABLE') {
    if (r.is_stale !== true || r.queue !== null || r.latest_critical !== null) throw new Error('invented availability');
    return r as unknown as AlertReport;
  }
  const q = object(r.queue);
  if (r.is_stale !== false || !['total','pending','accepted','critical_total','critical_pending','critical_accepted','test_total','test_pending','test_accepted'].every(k => count(q[k])) ||
      q.total !== Number(q.pending) + Number(q.accepted) || q.total !== Number(q.critical_total) + Number(q.test_total) ||
      q.critical_total !== Number(q.critical_pending) + Number(q.critical_accepted) ||
      q.test_total !== Number(q.test_pending) + Number(q.test_accepted) ||
      q.pending !== Number(q.critical_pending) + Number(q.test_pending) ||
      q.accepted !== Number(q.critical_accepted) + Number(q.test_accepted) ||
      !nullableDate(q.oldest_pending_at) || !nullableCount(q.oldest_pending_age_seconds) || typeof q.timing_consistent !== 'boolean') throw new Error('invalid queue');
  if (r.latest_critical !== null) {
    const recent = object(r.latest_critical);
    if (!Object.hasOwn(criticalCodes, String(recent.code)) || !date(recent.created_at) || !nullableDate(recent.accepted_at) ||
        !count(recent.attempts) || typeof recent.last_failure !== 'string' ||
        !nullableCount(recent.delivery_delay_seconds) || typeof recent.timing_consistent !== 'boolean') throw new Error('invalid critical alert');
  }
  return r as unknown as AlertReport;
}
function duration(seconds: number | null): string {
  if (seconds === null) return '확인 필요';
  if (seconds >= 86400) return `${Math.floor(seconds / 86400)}일 ${Math.floor(seconds / 3600) % 24}시간`;
  if (seconds >= 3600) return `${Math.floor(seconds / 3600)}시간 ${Math.floor(seconds / 60) % 60}분`;
  if (seconds >= 60) return `${Math.floor(seconds / 60)}분 ${seconds % 60}초`;
  return `${seconds}초`;
}
function kst(value: string | number): string {
  return `${new Date(value).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' })} KST`;
}

export function AlertStatusPanel() {
  const [report, setReport] = useState<AlertReport | null>(null);
  const [failed, setFailed] = useState(false);
  const [clock, setClock] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setClock(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    let stopped = false; let current: AbortController | null = null;
    const refresh = async () => {
      current?.abort(); const request = new AbortController(); current = request;
      const timeout = setTimeout(() => {
        request.abort();
        if (!stopped && current === request) { setFailed(true); setReport(null); }
      }, 8000);
      try {
        const response = await fetch('/api/v1/demo/alerts/status', { signal: request.signal, cache: 'no-store' });
        if (!response.ok && response.status !== 503) throw new Error('unavailable');
        const result = parse(await response.json());
        if (!response.ok && result.status !== 'UNAVAILABLE') throw new Error('invalid status code');
        if (!stopped && current === request && !request.signal.aborted) { setReport(result); setFailed(false); }
      } catch {
        if (!stopped && current === request) { setFailed(true); setReport(null); }
      } finally { clearTimeout(timeout); }
    };
    void refresh();
    const timer = setInterval(() => { void refresh(); }, 30000);
    return () => { stopped = true; clearInterval(timer); current?.abort(); };
  }, []);

  const q = report?.queue; const recent = report?.latest_critical;
  const configured = report?.email.state === 'CONFIGURED_PRIVATE_RAM';
  const expired = configured && Number(report?.email.expires_unix) * 1000 <= clock;
  const emailLabel = configured ? (expired ? '유효기한 지남 · 재확인 필요' : '등록됨 · 작업자 상태 미확인') :
    report?.email.state === 'NOT_CONFIGURED' ? '설정 없음' :
    report?.email.state === 'BLOCKED_EXPIRED_OR_INVALID' ? '만료 또는 설정 확인 필요' : '확인 불가';
  return <section className="alert-section" aria-label="위험 알림 상태">
    <h2>위험 알림 전달 상태</h2>
    {!report && !failed && <p role="status">알림 상태를 확인하고 있습니다…</p>}
    {(failed || report?.status === 'UNAVAILABLE') && <p role="status" className="alert-warning">알림 상태를 확인할 수 없습니다. 전달 가능 상태로 간주하지 마세요.</p>}
    {report?.status === 'READ_ONLY_ALERT_STATUS' && q && <>
      <div className="status-grid alert-grid">
        <article className={!configured || expired ? 'alert-warning' : ''}>
          <h3>네이버 메일 설정</h3><strong>{emailLabel}</strong>
          {configured && report.email.expires_unix !== null && <p>유효기한 · {kst(report.email.expires_unix * 1000)}</p>}
          <p>설정 등록은 현재 발송 작업자의 정상 실행이나 메일 도착 확인과 다릅니다.</p>
        </article>
        <article className={q.pending ? 'alert-warning' : ''}>
          <h3>전송 대기</h3><strong>위험 알림 {q.critical_pending}건</strong>
          <p>시험 알림 {q.test_pending}건 · 전체 대기 {q.pending}건</p>
          {q.pending > 0 && <p>가장 오래 대기 · {duration(q.oldest_pending_age_seconds)}</p>}
          {!q.timing_consistent && <p>시간 기록 확인 필요</p>}
        </article>
        <article><h3>전송 수락 기록</h3><strong>위험 알림 {q.critical_accepted}건</strong>
          <p>시험 알림 {q.test_accepted}건 · 전체 수락 {q.accepted}건</p>
          <p>수신처의 수락 기록이며 받은편지함 도착을 보장하지 않습니다.</p></article>
      </div>
      {recent ? <article className="recent-alert">
        <h3>최근 위험 알림 · {criticalCodes[recent.code]}</h3>
        <p>발생 · {kst(recent.created_at)}</p>
        <p>{recent.accepted_at ? `전송 수락 · ${kst(recent.accepted_at)}` : '전송 수락 기록 없음'}</p>
        {recent.accepted_at && <p>발생부터 전송 수락까지 · {duration(recent.delivery_delay_seconds)}</p>}
        {!recent.timing_consistent && <p>시간 기록 확인 필요</p>}
        {recent.last_failure !== 'NONE' && <p>전송 실패 또는 결과 확인이 필요한 기록이 있습니다.</p>}
        <p>과거 알림 기록이며 현재 계정의 위험 상태를 나타내지 않습니다.</p>
      </article> : <p>위험 알림 이력이 없습니다. 시험 알림은 별도로 집계합니다.</p>}
      <p className="alert-asof">확인 시각 · {kst(report.as_of)} · 30초마다 갱신</p>
    </>}
  </section>;
}
