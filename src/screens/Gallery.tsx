import { Link } from "react-router-dom";
import { Arrow, Coverage, Pill } from "../components/ui";
import { SnagMark } from "../components/SnagMark";
import { coverage, examples, tally } from "../data";

export function Gallery() {
  return (
    <div className="shell gal">
      <header className="gal__head">
        <p className="eyebrow">pre-run examples</p>
        <h1 className="gal__h">Six prompts, scanned once and kept.</h1>
        <p className="gal__lede">
          Each of these was written with a known hole, or written properly on purpose.
          The reports are real output stored as fixtures — no model call happens when you
          open one, and there is nothing to sign up for. Every screen of the product is
          reachable inside them.
        </p>
      </header>

      <div className="gal__list">
        {examples.map((ex) => {
          const c = coverage(ex);
          const t = tally(ex);
          return (
            <article className="galrow" key={ex.slug}>
              <div className="galrow__n">
                <span className="mono">{String(ex.n).padStart(2, "0")}</span>
              </div>
              <div className="galrow__body">
                <div className="galrow__top">
                  <h2 className="galrow__title">
                    <Link to={`/e/${ex.slug}/report`}>{ex.title}</Link>
                  </h2>
                  <Pill verdict={t.snagged > 0 ? "snagged" : "held"}>
                    {t.breaks > 0
                      ? `${t.breaks} breaks across ${t.snagged} rules`
                      : "no breaks"}
                  </Pill>
                </div>
                <span className="markwrap galrow__mark"><SnagMark verdict={t.snagged > 0 ? "snagged" : "held"} /></span>
                <p className="galrow__head">{ex.headline}</p>
                <p className="galrow__blurb dim">{ex.blurb}</p>

                <div className="galrow__meta">
                  <span className="mono dimmer">{ex.model}</span>
                  <span className="mono dimmer">
                    {ex.scan.mode} · {ex.scan.repeats}× · {ex.scan.calls.toLocaleString()} calls · ${ex.scan.cost.toFixed(2)}
                  </span>
                </div>

                <div className="galrow__cov">
                  <Coverage total={c.total} testable={c.testable} eyes={c.eyes} compact />
                </div>

                <nav className="galrow__links">
                  <Link to={`/e/${ex.slug}/rules`}>Rules</Link>
                  <Link to={`/e/${ex.slug}/surfaces`}>Surfaces</Link>
                  <Link to={`/e/${ex.slug}/scanning`}>Watch the scan</Link>
                  <Link to={`/e/${ex.slug}/report`}>Report</Link>
                  <Link to={`/e/${ex.slug}/gaps`}>Gaps</Link>
                  {ex.fixes.length > 0 && <Link to={`/e/${ex.slug}/fixes`}>Fixes</Link>}
                </nav>
              </div>
              <div className="galrow__walk">
                <p className="label">the walkthrough</p>
                <p className="galrow__walktext">{ex.walkthrough.broke}</p>
                <Link to={`/e/${ex.slug}/report`} className="galrow__cta">
                  Open the report <Arrow />
                </Link>
              </div>
            </article>
          );
        })}
      </div>
    </div>
  );
}
