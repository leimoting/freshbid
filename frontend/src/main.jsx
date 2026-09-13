import React, { useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

const initialForm = {
  product_name: "Croissant",
  inventory_on_hand: 36,
  recent_sales_units: 5,
  recent_footfall: 12,
  original_price_cents: 2400,
  current_price_cents: 2400,
  approved_price_cents: [2400, 2160, 1920],
  hours_until_close: 2,
};

const flow = ["recommended", "approved", "applied", "verified"];

function money(cents) {
  return `HK$${(Number(cents || 0) / 100).toFixed(2)}`;
}

function number(value, digits = 1) {
  return Number(value || 0).toFixed(digits);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "The request failed.");
  return data;
}

function App() {
  const [form, setForm] = useState(initialForm);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [outcome, setOutcome] = useState({ units_sold: 11, ending_inventory: 25 });

  const recommendation = result?.recommendation;
  const snapshot = result?.snapshot;
  const recommendedForecast = useMemo(() => {
    if (!result) return null;
    return result.forecast.scenarios.find(
      (scenario) => scenario.price_cents === recommendation.recommended_price_cents,
    );
  }, [result, recommendation]);

  function update(field, value) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  async function createRecommendation(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const data = await api("/api/recommendations", {
        method: "POST",
        body: JSON.stringify(form),
      });
      setResult(data);
      setOutcome({
        units_sold: Math.round(data.recommendation.next_hour_expected_sales),
        ending_inventory: Math.max(
          0,
          data.snapshot.inventory_on_hand -
            Math.round(data.recommendation.next_hour_expected_sales),
        ),
      });
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
  }

  async function runAction(action) {
    setBusy(true);
    setError("");
    try {
      const id = encodeURIComponent(recommendation.recommendation_id);
      const data = await api(`/api/recommendations/${id}/${action}`, {
        method: "POST",
        body: JSON.stringify({ actor: "demo-manager" }),
      });
      setResult(data);
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
  }

  async function submitOutcome(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const id = encodeURIComponent(recommendation.recommendation_id);
      const data = await api(`/api/recommendations/${id}/outcomes`, {
        method: "POST",
        body: JSON.stringify({ ...outcome, source: "simulated" }),
      });
      setResult(data);
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="#top" aria-label="FreshBid home">
          <span className="brand-mark">F</span>
          <span>FreshBid</span>
        </a>
        <div className="top-status"><span className="live-dot" /> Demo environment</div>
      </header>

      <main id="top">
        <section className="hero">
          <div>
            <p className="eyebrow">MERCHANT PRICING CONSOLE</p>
            <h1>Sell more fresh food.<br /><em>Waste less.</em></h1>
            <p className="hero-copy">
              Hourly markdown recommendations that balance revenue and leftover inventory—always
              within merchant-approved rules.
            </p>
          </div>
          <div className="hero-badge">
            <span>Decision cycle</span>
            <strong>60 min</strong>
            <small>Merchant approval required</small>
          </div>
        </section>

        <section className="workspace">
          <aside className="panel input-panel">
            <div className="panel-heading">
              <span className="step-number">01</span>
              <div><h2>Store snapshot</h2><p>Enter the latest operating data.</p></div>
            </div>
            <form onSubmit={createRecommendation} className="input-form">
              <label>Product<input value={form.product_name} onChange={(e) => update("product_name", e.target.value)} /></label>
              <div className="field-row">
                <label>Inventory<input type="number" min="0" value={form.inventory_on_hand} onChange={(e) => update("inventory_on_hand", Number(e.target.value))} /></label>
                <label>Sales / 30 min<input type="number" min="0" value={form.recent_sales_units} onChange={(e) => update("recent_sales_units", Number(e.target.value))} /></label>
              </div>
              <div className="field-row">
                <label>Footfall / 30 min<input type="number" min="0" value={form.recent_footfall ?? ""} onChange={(e) => update("recent_footfall", e.target.value === "" ? null : Number(e.target.value))} /></label>
                <label>Hours to close<input type="number" min="1" max="8" value={form.hours_until_close} onChange={(e) => update("hours_until_close", Number(e.target.value))} /></label>
              </div>
              <label>Approved price tiers<div className="tier-list">{form.approved_price_cents.map((price) => <span key={price}>{money(price)}</span>)}</div></label>
              <button className="primary-button" disabled={busy}>{busy ? "Calculating…" : "Generate recommendation"}<span>→</span></button>
            </form>
          </aside>

          <section className="results">
            {!result ? (
              <div className="empty-state">
                <div className="empty-visual"><span /><span /><span /></div>
                <h2>Ready to optimize</h2>
                <p>Generate a recommendation to see the forecast, DP decision, and safety checks.</p>
              </div>
            ) : (
              <>
                <div className="panel decision-card">
                  <div className="decision-topline">
                    <div><p className="eyebrow">RECOMMENDATION</p><h2>{snapshot.product_name}</h2></div>
                    <span className={`status-pill status-${result.status}`}>{result.status}</span>
                  </div>
                  <div className="price-decision">
                    <div><span>Current price</span><strong className="old-price">{money(snapshot.current_price_cents)}</strong></div>
                    <span className="price-arrow">→</span>
                    <div><span>Recommended</span><strong>{money(recommendation.recommended_price_cents)}</strong></div>
                    <div className="discount-chip">{Math.round((1 - recommendation.recommended_price_cents / snapshot.current_price_cents) * 100)}% markdown</div>
                  </div>
                  <div className="metric-grid">
                    <Metric label="Expected sales · next hour" value={`${number(recommendation.next_hour_expected_sales)} units`} />
                    <Metric label="Expected revenue · next hour" value={money(recommendation.next_hour_expected_revenue_cents)} />
                    <Metric label="Expected closing inventory" value={`${number(recommendation.expected_inventory_at_close)} units`} />
                    <Metric label="Sell-out probability" value={`${number(recommendation.sellout_probability_by_close * 100)}%`} />
                  </div>
                </div>

                <div className="two-column">
                  <div className="panel compact-panel">
                    <div className="panel-heading small"><span className="step-number">02</span><div><h2>DP price comparison</h2><p>Revenue minus leftover and change penalties.</p></div></div>
                    <ActionBars items={recommendation.candidate_action_values} selected={recommendation.recommended_price_cents} />
                  </div>
                  <div className="panel compact-panel">
                    <div className="panel-heading small"><span className="step-number">03</span><div><h2>Safety guardrails</h2><p>Deterministic checks—no LLM override.</p></div></div>
                    <div className="safety-summary"><span className={result.safety.passed ? "check" : "cross"}>{result.safety.passed ? "✓" : "!"}</span><strong>{result.safety.passed ? "All rules passed" : "Action blocked"}</strong></div>
                    <ul className="rule-list"><li>Merchant-approved tier</li><li>No price increase after markdown</li><li>Daily change limit</li><li>Minimum price floor</li></ul>
                  </div>
                </div>

                <div className="panel forecast-panel">
                  <div className="panel-heading small"><span className="step-number">04</span><div><h2>Demand forecast</h2><p>Recommended-price scenario in 30-minute intervals.</p></div></div>
                  <div className="forecast-table">
                    <div className="table-row table-head"><span>Time</span><span>P10</span><span>Expected / P50</span><span>P90</span></div>
                    {recommendedForecast?.intervals.map((interval) => (
                      <div className="table-row" key={interval.start_at}>
                        <span>{new Date(interval.start_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
                        <span>{number(interval.p10_units)}</span><strong>{number(interval.expected_units)}</strong><span>{number(interval.p90_units)}</span>
                      </div>
                    ))}
                  </div>
                  <p className="model-note">Illustrative simulated forecast · price elasticity 1.00 · not calibrated from merchant data</p>
                </div>

                <div className="panel workflow-panel">
                  <div className="panel-heading small"><span className="step-number">05</span><div><h2>Merchant decision</h2><p>Each operational state is timestamped and retained.</p></div></div>
                  <div className="flow-track">
                    {flow.map((stage, index) => {
                      const activeIndex = flow.indexOf(result.status);
                      const completed = index <= activeIndex;
                      return <React.Fragment key={stage}><div className={`flow-step ${completed ? "complete" : ""}`}><span>{completed ? "✓" : index + 1}</span><small>{stage}</small></div>{index < flow.length - 1 && <div className={`flow-line ${index < activeIndex ? "complete" : ""}`} />}</React.Fragment>;
                    })}
                  </div>
                  <WorkflowActions status={result.status} safe={result.safety.passed} busy={busy} action={runAction} />
                  {result.status === "verified" && (
                    <form className="outcome-form" onSubmit={submitOutcome}>
                      <div><strong>Record next-hour outcome</strong><p>Demo values are explicitly saved as simulated.</p></div>
                      <label>Units sold<input type="number" min="0" value={outcome.units_sold} onChange={(e) => setOutcome({ ...outcome, units_sold: Number(e.target.value) })} /></label>
                      <label>Ending inventory<input type="number" min="0" value={outcome.ending_inventory} onChange={(e) => setOutcome({ ...outcome, ending_inventory: Number(e.target.value) })} /></label>
                      <button disabled={busy} className="secondary-button">Save feedback</button>
                    </form>
                  )}
                  {result.outcomes.length > 0 && <div className="saved-message">✓ Feedback saved · {result.outcomes.at(-1).units_sold} units sold · {result.outcomes.at(-1).ending_inventory} remaining</div>}
                </div>
              </>
            )}
          </section>
        </section>
        {error && <div className="error-toast" role="alert">{error}</div>}
      </main>
      <footer><span>FreshBid MVP · Decision support, not autonomous pricing</span><span>Model v0.1.0</span></footer>
    </div>
  );
}

function Metric({ label, value }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong></div>;
}

function ActionBars({ items, selected }) {
  const values = items.map((item) => item.expected_total_objective_cents);
  const min = Math.min(...values);
  const max = Math.max(...values);
  return <div className="bars">{items.map((item) => {
    const width = max === min ? 100 : 58 + ((item.expected_total_objective_cents - min) / (max - min)) * 42;
    return <div className="bar-row" key={item.price_cents}><div className="bar-label"><span>{money(item.price_cents)}</span><small>{money(item.expected_total_objective_cents)}</small></div><div className="bar-track"><div className={item.price_cents === selected ? "bar-fill selected" : "bar-fill"} style={{ width: `${width}%` }} /></div></div>;
  })}</div>;
}

function WorkflowActions({ status, safe, busy, action }) {
  if (status === "recommended") return <div className="action-row"><button disabled={busy || !safe} className="primary-button action" onClick={() => action("approve")}>Approve recommendation</button><button disabled={busy} className="text-button" onClick={() => action("reject")}>Reject</button></div>;
  if (status === "approved") return <button disabled={busy} className="primary-button action" onClick={() => action("apply")}>Confirm price applied</button>;
  if (status === "applied") return <button disabled={busy} className="primary-button action" onClick={() => action("verify")}>Verify shelf price</button>;
  if (status === "rejected") return <p className="terminal-note">This recommendation was rejected. Generate a new one to continue.</p>;
  return <p className="terminal-note">Price verified. Record the observed result when the hour ends.</p>;
}

createRoot(document.getElementById("root")).render(<App />);
