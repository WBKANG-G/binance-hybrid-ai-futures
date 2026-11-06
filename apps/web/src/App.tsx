import { useEffect, useState } from 'react';
import './style.css';
import { AlertStatusPanel } from './AlertStatusPanel';

interface Health {
  phase: 4;
  environment: 'BACKTEST' | 'PAPER';
  live_trading_enabled: false;
  orders_enabled: false;
  demo_trading_enabled: false;
  status: 'development_only';
  database: 'ready' | 'unavailable';
  as_of: string;
  is_stale: false;
}

function parseHealth(value: unknown): Health {
  if (!value || typeof value !== 'object') throw new Error('invalid response');
  const data = value as Record<string, unknown>;
  if (data.phase !== 4 || !['BACKTEST', 'PAPER'].includes(String(data.environment)) ||
      data.live_trading_enabled !== false || data.orders_enabled !== false || data.demo_trading_enabled !== false ||
      data.status !== 'development_only' || !['ready', 'unavailable'].includes(String(data.database)) ||
      typeof data.as_of !== 'string' || !Number.isFinite(Date.parse(data.as_of)) || data.is_stale !== false) {
    throw new Error('invalid or unsafe development status');
  }
  return data as unknown as Health;
}

export function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8000);
    void fetch('/api/v1/system/ready', { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok && response.status !== 503) throw new Error('API unavailable');
        return parseHealth(await response.json());
      })
      .then((result) => { if (!controller.signal.aborted) setHealth(result); })
      .catch(() => { setError(true); })
      .finally(() => clearTimeout(timeout));
    return () => { clearTimeout(timeout); controller.abort(); };
  }, []);

  return <main>
    <header><span className="brand">HYBRID / FUTURES</span><span className="phase">PHASE 4</span></header>
    <section className="intro">
      <p className="eyebrow">개발 환경 · 실거래 차단</p>
      <h1>안전한 시작을 위한<br />프로젝트 기반</h1>
      <p>Binance Hybrid AI Futures Trading System</p>
      <div className="notice">주문 실행 비활성</div>
    </section>
    {error && <p role="alert">상태를 확인할 수 없습니다. API 연결 또는 응답 형식을 확인하세요.</p>}
    {!health && !error && <p role="status">개발 환경 상태를 확인하고 있습니다…</p>}
    {health && <section className="status-grid" aria-label="개발 환경 상태">
      <article><h2>실행 환경</h2><strong>{health.environment}</strong><p>PAPER 재현은 별도 CLI로 실행합니다.</p></article>
      <article><h2>PostgreSQL</h2><strong>{health.database === 'ready' ? '연결 확인' : '연결 불가'}</strong>
        <p>확인 시각 · {new Date(health.as_of).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' })} KST</p></article>
      <article><h2>실거래</h2><strong>비활성</strong><p>설정 토글로 활성화할 수 없습니다.</p></article>
    </section>}
    {health?.database === 'ready' && <AlertStatusPanel />}
    <section className="scope"><h2>이번 단계의 범위</h2>
      <p>DEMO 키 분리 · Algo SL/TP · 계좌 대조 · 장애 복구</p>
      <p>이 화면은 조회 전용입니다. 실제 DEMO 검증 결과는 작업별 기록에서 확인합니다.</p>
    </section>
    <footer>불변 이벤트 · Decimal 금액 · 미확정 위험값 유지</footer>
  </main>;
}
