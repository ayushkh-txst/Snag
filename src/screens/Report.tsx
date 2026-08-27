import { Link, useParams } from "react-router-dom";
import { StepHead } from "../components/Shell";
import { SnagMark } from "../components/SnagMark";
import { Arrow, CheckerConfig, Coverage, Pill } from "../components/ui";
import { CATEGORY_LABEL, byslug, coverage, tally, verdictOf } from "../data";

export function Report() {
  const { slug } = useParams();
  const ex = byslug(slug);
  const c = coverage(ex);
  const t = tally(ex);

  const sorted = [...ex.rules].sort((a, b) => {
    const rate = (r: typeof a) => (r.attacks ? r.breaks / r.attacks : -1);
    return rate(b) - rate(a);
  });

  return (
    <>
      <StepHead
        n="05"
        title={ex.headline}
        lede={ex.walkthrough.broke}
        aside={
          <div className="reportmeta mono">
            <div>{ex.model}</div>
            <div className="dimmer">{ex.scan.mode} · {ex.scan.repeats} repeats</div>
            <div className="dimmer">{ex.scan.calls.toLocaleString()} calls · ${ex.scan.cost.toFixed(2)} · {ex.scan.duration}</div>
          </div>
        }
      />

      <section className="repcov">
        <Coverage total={c.total} testable={c.testable} eyes={c.eyes} />
        <p className="repcov__note dim">
          This line comes first because the number below it means nothing without it.
          {c.eyes > 0
            ? c.eyes === 1
              ? " One rule cannot be checked by code. It is listed at the bottom with sample outputs so you can judge it yourself."
              : ` ${c.eyes} of your rules cannot be checked by code. They are listed at the bottom with sample outputs so you can judge them yourself.`
            : " Every rule in this prompt has a checker."}
        </p>
      </section>

      <div className="statrow rep__stats">
        <div className="stat" data-tone="snagged"><div className="stat__n">{t.snagged}</div><div className="stat__label">rules that broke</div></div>
        <div className="stat" data-tone="held"><div className="stat__n">{t.held}</div><div className="stat__label">rules that held</div></div>
        <div className="stat" data-tone="eyes"><div className="stat__n">{t.eyes}</div><div className="stat__label">need your eyes</div></div>
        <div className="stat"><div className="stat__n">{t.breaks}</div><div className="stat__label">breaks total</div></div>
        <div className="stat"><div className="stat__n">{t.attacks}</div><div className="stat__label">attacks run</div></div>
      </div>

      <section className="repnav">
        <Link to={`/e/${ex.slug}/gaps`}>
          <span className="repnav__n mono">{ex.gaps.filter((g) => !g.verdict.startsWith("No gap")).length}</span>
          <span className="repnav__l">gaps your prompt never addresses</span>
          <Arrow />
        </Link>
        {ex.fixes.length > 0 && (
          <Link to={`/e/${ex.slug}/fixes`}>
            <span className="repnav__n mono">{ex.fixes.length}</span>
            <span className="repnav__l">suggested edits, with a verifying rescan</span>
            <Arrow />
          </Link>
        )}
        <Link to={`/e/${ex.slug}/history`}>
          <span className="repnav__n mono">{ex.history.length}</span>
          <span className="repnav__l">scans on record — compare them</span>
          <Arrow />
        </Link>
      </section>

      <section className="rulesreport">
        <header className="panel__head">
          <span className="label">rule by rule</span>
          <div className="panel__aside">
            <span className="dimmer">sorted by break rate</span>
          </div>
        </header>

        {sorted.map((r, i) => {
          const v = verdictOf(r);
          const rate = r.attacks ? Math.round((r.breaks / r.attacks) * 100) : 0;
          const rBreaks = ex.breaks.filter((b) => b.ruleId === r.id);
          return (
            <article className="rrow" key={r.id} data-verdict={v}>
              <div className="rrow__l">
                <div className="rrow__top">
                  <span className="rrow__n mono">{String(i + 1).padStart(2, "0")}</span>
                  <span className="chip">{CATEGORY_LABEL[r.category]}</span>
                  <span className="chip" data-dir={r.direction}>
                    {r.direction === "positive" ? "must refuse" : "must not break"}
                  </span>
                </div>
                <span className="markwrap">
                  <p className="rrow__text">{r.text}</p>
                  <SnagMark verdict={v} />
                </span>
                {r.testable ? (
                  <div className="rrow__checker">
                    <CheckerConfig type={r.checkerType} config={r.checkerConfig} />
                  </div>
                ) : (
                  <div className="untest">
                    <Pill verdict="eyes" />
                    <p>{r.untestableReason}</p>
                  </div>
                )}
              </div>

              <div className="rrow__r">
                {r.testable ? (
                  <>
                    <div className="ratebox" data-verdict={v}>
                      <div className="ratebox__n mono">
                        {r.breaks}<span className="dimmer"> / {r.attacks}</span>
                      </div>
                      <div className="ratebox__pct mono">{rate}% broke</div>
                      <div className="ratebox__bar">
                        <span style={{ width: `${Math.max(rate, r.breaks ? 2 : 0)}%` }} />
                      </div>
                    </div>
                    {rBreaks.length > 0 && (
                      <ul className="breaklinks">
                        {rBreaks.map((b) => (
                          <li key={b.id}>
                            <Link to={`/e/${ex.slug}/report/${b.id}`} data-fp={b.falsePositive || undefined}>
                              <span className="mono">{b.techniqueId}</span>
                              <span className="breaklinks__hits mono">
                                {b.hits}/{b.repeats}
                              </span>
                              {b.falsePositive && <span className="fpflag mono">marked false positive</span>}
                            </Link>
                          </li>
                        ))}
                      </ul>
                    )}
                    {r.breaks === 0 && (
                      <p className="rrow__held mono">held against every technique tried</p>
                    )}
                  </>
                ) : (
                  <div className="ratebox" data-verdict="eyes">
                    <div className="ratebox__n mono">—</div>
                    <div className="ratebox__pct mono">not measured</div>
                  </div>
                )}
              </div>
            </article>
          );
        })}
      </section>

      <section className="whyband">
        <p className="eyebrow">why it broke</p>
        <p className="whyband__t">{ex.walkthrough.why}</p>
      </section>
    </>
  );
}
