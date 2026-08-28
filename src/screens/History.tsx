import { useState } from "react";
import { useParams } from "react-router-dom";
import { NextBar, StepHead } from "../components/Shell";
import { byslug } from "../data";

export function History() {
  const { slug } = useParams();
  const ex = byslug(slug);
  const [sel, setSel] = useState(ex.history[0].id);
  const run = ex.history.find((h) => h.id === sel)!;
  const max = Math.max(...ex.history.map((h) => h.breaks), 1);

  return (
    <>
      <StepHead
        n="—"
        title="What changed since last time."
        lede="A total that went down can still hide a rule that just started failing, so rescans lead with the comparison."
      />

      <section className="hist">
        <div className="hist__chart">
          {[...ex.history].reverse().map((h) => (
            <button
              className="hbar"
              key={h.id}
              data-on={h.id === sel || undefined}
              onClick={() => setSel(h.id)}
            >
              <span className="hbar__col">
                <span className="hbar__fill" style={{ height: `${(h.breaks / max) * 100}%` }} />
              </span>
              <span className="hbar__n mono">{h.breaks}</span>
              <span className="hbar__d mono">{h.date}</span>
              <span className="hbar__l">{h.label}</span>
            </button>
          ))}
        </div>

        <aside className="hist__panel">
          <p className="label">{run.date}</p>
          <h2 className="hist__h">{run.label}</h2>
          <p className="mono dimmer hist__mode">{run.mode} · {run.calls.toLocaleString()} calls · ${run.cost.toFixed(2)}</p>

          <div className="delta">
            <div className="delta__row" data-k="fixed">
              <span className="delta__n mono">{run.fixed}</span>
              <span>fixed</span>
            </div>
            <div className="delta__row" data-k="added">
              <span className="delta__n mono">{run.added}</span>
              <span>new failure{run.added === 1 ? "" : "s"}</span>
            </div>
            <div className="delta__row" data-k="unchanged">
              <span className="delta__n mono">{run.unchanged}</span>
              <span>unchanged</span>
            </div>
          </div>

          {run.added > 0 ? (
            <div className="loud">
              <p className="loud__tag mono">called out loudly</p>
              <p>
                {run.added} thing{run.added === 1 ? "" : "s"} started failing that wasn't
                failing before. Tightening one rule regularly loosens another.
              </p>
            </div>
          ) : (
            <p className="dim hist__none">Nothing new started failing in this run.</p>
          )}
        </aside>
      </section>

      <div className="tblwrap card">
        <table className="tbl">
          <thead>
            <tr>
              <th>Scan</th><th>Mode</th>
              <th className="num">Breaks</th><th className="num">Fixed</th>
              <th className="num">New</th><th className="num">Unchanged</th>
              <th className="num">Calls</th><th className="num">Cost</th>
            </tr>
          </thead>
          <tbody>
            {ex.history.map((h) => (
              <tr key={h.id} data-on={h.id === sel || undefined}>
                <td>
                  <div>{h.label}</div>
                  <div className="mono dimmer">{h.date}</div>
                </td>
                <td className="mono dim">{h.mode}</td>
                <td className="num mono">{h.breaks}</td>
                <td className="num mono held">{h.fixed}</td>
                <td className="num mono snag">{h.added}</td>
                <td className="num mono dim">{h.unchanged}</td>
                <td className="num mono dim">{h.calls.toLocaleString()}</td>
                <td className="num mono dim">${h.cost.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <NextBar back={`/e/${ex.slug}/report`} backLabel="Report" />
    </>
  );
}
