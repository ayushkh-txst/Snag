import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getActiveScan } from "../api/client";
import { StepHead } from "../components/Shell";
import { SnagMark } from "../components/SnagMark";
import { ErrorState, Loading, NotFound } from "../components/States";
import { Arrow, Coverage, DisputedPill, Pill, TierPill } from "../components/ui";
import { useProject } from "../hooks/useProject";
import {
  breakInput,
  checkerPlain,
  coverage,
  surfaceTitle,
  tally,
  type Break,
  type Example,
  type Rule,
} from "../data";

/** What was actually scanned. Collapsed by default — the findings come
 *  first — but one click away, because every number on this page is only
 *  meaningful against the exact prompt and tools that produced it. */
function ScannedInput({ ex }: { ex: Example }) {
  const [open, setOpen] = useState(false);
  const lines = ex.systemPrompt.split("\n");

  const toolsText = (() => {
    const raw = ex.tools?.trim();
    if (!raw || raw === "[]") return "";
    try {
      return JSON.stringify(JSON.parse(raw), null, 2);
    } catch {
      return raw; // not JSON we can pretty-print — show it exactly as sent
    }
  })();
  const toolCount = (() => {
    if (!toolsText) return 0;
    try {
      const parsed = JSON.parse(toolsText);
      return Array.isArray(parsed) ? parsed.length : 1;
    } catch {
      return 1;
    }
  })();

  return (
    <section className="panel scanned">
      <header className="panel__head">
        <span className="label">what was scanned</span>
        <div className="panel__aside">
          <span className="mono dimmer">
            {lines.length} lines
            {toolCount > 0 && ` · ${toolCount} tool${toolCount === 1 ? "" : "s"}`}
          </span>
          <button
            type="button"
            className="linky scanned__toggle"
            aria-expanded={open}
            aria-controls="scanned-body"
            onClick={() => setOpen((v) => !v)}
          >
            {open ? "Hide" : "See the system prompt"}
            <span className="scanned__chev" data-open={open || undefined} aria-hidden="true" />
          </button>
        </div>
      </header>

      {open && (
        <div className="scanned__body" id="scanned-body">
          <div className="scanned__pane">
            <div className="srcpane__head">
              <span className="label">system prompt</span>
              <span className="mono dimmer">{lines.length} lines</span>
            </div>
            <div className="srcpane__body">
              {lines.map((l, i) => (
                <div className="srcline" key={i}>
                  <span className="srcline__n mono">{String(i + 1).padStart(2, "0")}</span>
                  <span className="srcline__t mono">{l || " "}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="scanned__pane">
            <div className="srcpane__head">
              <span className="label">tool definitions</span>
              <span className="mono dimmer">{toolCount || "none"}</span>
            </div>
            <div className="srcpane__body">
              {toolsText ? (
                <pre className="scanned__tools mono">{toolsText}</pre>
              ) : (
                <p className="scanned__none dim">
                  No tools were given. Snag could test what the model says, but not what it does.
                </p>
              )}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

/** One finding row: a break, or a hold the judge thinks was a miss. */
function FindingRow({ ex, entry: b }: { ex: Example; entry: Break }) {
  const input = breakInput(b);
  const surface = ex.surfaces.find((s) => s.id === b.surfaceId);
  const missed = b.hits === 0 && (b.flaggedHits ?? 0) > 0;
  const rate = b.falsePositive
    ? "you marked this a false positive"
    : missed
      ? `the judge reads ${b.flaggedHits} of ${b.repeats} as breaking it`
      : `broke ${b.hits} of ${b.repeats}`;
  const body = (
    <>
      <div className="brk__top">
        <span className="brk__where mono">
          {input.where}
          {surface && ` · ${surfaceTitle(surface)}`}
        </span>
        <span className="brk__rate mono" data-quiet={missed || undefined}>{rate}</span>
      </div>
      <p className="brk__input">“{input.text}”</p>
      <div className="brk__foot">
        <span className="brk__tech">
          <span className="mono dimmer">{b.techniqueId}</span>
          <TierPill tier={b.verdictTier} />
          {(b.disputed || missed) && <DisputedPill />}
        </span>
        <span className="brk__cta">See the whole conversation <Arrow /></span>
      </div>
    </>
  );
  const link = (
    <Link className="brk" to={`/e/${ex.slug}/report/${b.id}`} data-fp={b.falsePositive || undefined}>
      {body}
    </Link>
  );
  if (!b.disputeQuote) return link;
  // Dimmed only when the whole finding is in doubt. A break with three solid
  // repeats and one disputed one still reads as a break; it just carries the
  // judge's quote underneath.
  return (
    <div className="disputed" data-quiet={b.disputed || missed || undefined}>
      {link}
      <p className="disputed__q">
        <span className="dimmer">the judge quoted · </span>
        <span className="source">“{b.disputeQuote}”</span>
      </p>
    </div>
  );
}

function BreakRow({ ex, breakId }: { ex: Example; breakId: string }) {
  return <FindingRow ex={ex} entry={ex.breaks.find((x) => x.id === breakId)!} />;
}

function BrokenRule({ ex, rule, i }: { ex: Example; rule: Rule; i: number }) {
  const rate = rule.attacks ? Math.round((rule.breaks / rule.attacks) * 100) : 0;
  const breaks = ex.breaks.filter((b) => b.ruleId === rule.id);
  return (
    <article className="broke">
      <header className="broke__head">
        <span className="broke__n mono">{String(i + 1).padStart(2, "0")}</span>
        <span className="markwrap">
          <h3 className="broke__text">{rule.text}</h3>
          <SnagMark verdict="snagged" delay={i * 70} />
        </span>
        <span className="broke__rate">
          <span className="broke__ratev mono">{rule.breaks}<span className="dimmer"> / {rule.attacks}</span></span>
          <span className="broke__ratep mono">{rate}% of tries</span>
        </span>
      </header>
      <p className="broke__how dim">{checkerPlain(rule)}</p>
      <div className="broke__list">
        {breaks.map((b) => (
          <BreakRow key={b.id} ex={ex} breakId={b.id} />
        ))}
      </div>
    </article>
  );
}

/** A report is readable while its scan is still running — the rows are real
 *  as far as they go. What was missing was any sign that more is coming, and
 *  any way back to the run: leaving the scanning screen stranded it, because
 *  nothing else on the site mentions a scan in flight. */
function LiveScanBanner({ slug }: { slug: string }) {
  const [active, setActive] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    const check = () =>
      getActiveScan(slug)
        .then((a) => {
          if (!cancelled) setActive(a.scanId);
        })
        .catch(() => {
          // the banner is an affordance, not the report — stay quiet
        });
    check();
    const timer = window.setInterval(check, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [slug]);

  if (active == null) return null;
  return (
    <div className="panel" data-state="running">
      <p>
        A scan is still running for this project — these findings are partial and
        will keep growing.
      </p>
      <Link className="btn" data-variant="ghost" to={`/e/${slug}/scanning`}>
        Watch it run <Arrow />
      </Link>
    </div>
  );
}

export function Report() {
  const { slug } = useParams();
  const { data: ex, loading, error, notFound } = useProject(slug);

  if (loading) return <Loading label="Loading report…" />;
  if (notFound) return <NotFound slug={slug} />;
  if (error) return <ErrorState error={error} />;
  if (!ex) return <Loading label="Loading report…" />;

  return (
    <>
      <LiveScanBanner slug={ex.slug} />
      <ReportBody ex={ex} />
    </>
  );
}

function ReportBody({ ex }: { ex: Example }) {
  const c = coverage(ex);
  const t = tally(ex);

  // A rule that broke is shown whichever tier caught it — the judged ones are
  // labelled where each break is listed, not filtered out of the page.
  const broken = ex.rules
    .filter((r) => r.breaks > 0)
    .sort((a, b) => b.breaks / b.attacks - a.breaks / a.attacks);
  const held = ex.rules.filter((r) => r.testable && r.breaks === 0);
  const eyes = ex.rules.filter((r) => !r.testable && r.breaks === 0);
  // The two ways the checker and the judge disagreed. Neither changes a number
  // above; both are here in full with the reasoning one click away.
  const disputed = ex.breaks.filter((b) => b.disputed);
  const flagged = ex.flagged ?? [];

  const bySurface = ex.surfaces
    .map((s) => ({
      s,
      hits: ex.breaks.filter((b) => b.surfaceId === s.id && !b.falsePositive).reduce((n, b) => n + b.hits, 0),
    }))
    .filter((x) => x.hits > 0)
    .sort((a, b) => b.hits - a.hits);
  const maxHits = Math.max(...bySurface.map((x) => x.hits), 1);

  const worstGap = ex.gaps.find(
    (g) => !(g.covered ?? (g.verdict.startsWith("No gap") || g.verdict.startsWith("Covered"))),
  );
  const latest = ex.history[0];

  return (
    <>
      <StepHead
        n="05"
        title={ex.headline}
        lede={ex.walkthrough.broke}
        aside={
          <div className="reportmeta mono">
            <div>{ex.model}</div>
            <div className="dimmer">{ex.scan.mode} · {ex.scan.repeats} tries each</div>
            <div className="dimmer">{ex.scan.calls.toLocaleString()} calls · ${ex.scan.cost.toFixed(2)}</div>
          </div>
        }
      />

      <Coverage total={c.total} testable={c.testable} eyes={c.eyes} />

      <div className="statrow rep__stats">
        <div className="stat" data-tone="snagged"><div className="stat__n">{t.snagged}</div><div className="stat__label">rules broke</div></div>
        <div className="stat" data-tone="held"><div className="stat__n">{t.held}</div><div className="stat__label">rules held</div></div>
        <div className="stat" data-tone="eyes"><div className="stat__n">{t.eyes}</div><div className="stat__label">need your eyes</div></div>
        <div className="stat"><div className="stat__n">{t.breaks}</div><div className="stat__label">breaks</div></div>
        <div className="stat"><div className="stat__n">{t.attacks}</div><div className="stat__label">attacks</div></div>
      </div>

      <ScannedInput ex={ex} />

      {bySurface.length > 0 && (
        <section className="whereband">
          <header className="panel__head">
            <span className="label">where the attacks got in</span>
            <div className="panel__aside">
              <Link to={`/e/${ex.slug}/surfaces`} className="linky">Change what's tested <Arrow /></Link>
            </div>
          </header>
          <ul className="wherelist">
            {bySurface.map(({ s, hits }) => (
              <li key={s.id}>
                <span className="where__name mono">{surfaceTitle(s)}</span>
                <span className="where__bar"><span style={{ width: `${(hits / maxHits) * 100}%` }} /></span>
                <span className="where__n mono">{hits}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {broken.length > 0 && (
        <section className="panel">
          <header className="panel__head">
            <span className="label">what broke, and what was sent</span>
            <div className="panel__aside"><span className="dimmer">worst first</span></div>
          </header>
          <div className="brokelist">
            {broken.map((r, i) => (
              <BrokenRule key={r.id} ex={ex} rule={r} i={i} />
            ))}
          </div>
        </section>
      )}

      {flagged.length > 0 && (
        <section className="panel">
          <header className="panel__head">
            <span className="label">the judge thinks these were missed</span>
            <div className="panel__aside"><span className="dimmer">not counted as breaks</span></div>
          </header>
          <p className="disputed__lede dim">
            Nothing broke here by the checker's reading — the reply said the thing a different
            way, and a text search only finds the words it was given. A stronger model reading
            the same replies calls these violations. They are deliberately <strong>not</strong> in
            any number above: a model's opinion is enough to be worth your eye, not enough to
            count as a break.
          </p>
          <div className="brokelist">
            {flagged.map((b) => (
              <FindingRow key={b.id} ex={ex} entry={b} />
            ))}
          </div>
        </section>
      )}

      {disputed.length > 0 && (
        <section className="panel">
          <header className="panel__head">
            <span className="label">disputed by the judge</span>
            <div className="panel__aside"><span className="dimmer">out of the rate, not deleted</span></div>
          </header>
          <p className="disputed__lede dim">
            A checker flagged these, and a stronger model reading the same replies thinks it
            misread them — a denial or a hypothetical counted as the real thing. They are out of
            every number above. Nothing else was changed. Have a look and decide.
          </p>
          <div className="brokelist">
            {disputed.map((b) => (
              <FindingRow key={b.id} ex={ex} entry={b} />
            ))}
          </div>
        </section>
      )}

      <div className="restgrid">
        <section className="panel">
          <header className="panel__head"><span className="label">held</span></header>
          <ul className="quietlist">
            {held.map((r) => (
              <li key={r.id}>
                <Pill verdict="held">held</Pill>
                <span>{r.text}</span>
                <span className="mono dimmer">{r.attacks} attacks</span>
              </li>
            ))}
            {held.length === 0 && <li className="dim">Nothing held.</li>}
          </ul>
        </section>

        <section className="panel">
          <header className="panel__head"><span className="label">need your eyes</span></header>
          <ul className="quietlist">
            {eyes.map((r) => (
              <li key={r.id} data-eyes>
                {/* Still no checker — but no longer unmeasured once the judge
                    has actually attacked it and quoted what it read. */}
                <Pill verdict="eyes">
                  {r.verdictTier === "judged" && r.attacks > 0 ? "judged · held" : "not measured"}
                </Pill>
                <span>
                  {r.text}
                  <em className="quietlist__why">{r.untestableReason}</em>
                </span>
              </li>
            ))}
            {eyes.length === 0 && <li className="dim">Every rule had a checker.</li>}
          </ul>
        </section>
      </div>

      <section className="cards">
        <Link className="rcard" to={`/e/${ex.slug}/gaps`}>
          <div className="rcard__top">
            <span className="label">gaps</span>
            <span className="rcard__n mono">{ex.gaps.length}</span>
          </div>
          <p className="rcard__lede">Things your prompt never mentions, so the model decided for itself.</p>
          {worstGap && (
            <p className="rcard__quote">
              <span className="dimmer">{worstGap.item} — </span>
              {worstGap.observed}
            </p>
          )}
          <span className="rcard__cta">Probed during this same scan <Arrow /></span>
        </Link>

        <Link className="rcard" to={`/e/${ex.slug}/fixes`}>
          <div className="rcard__top">
            <span className="label">fixes</span>
            <span className="rcard__n mono">{ex.fixes.length}</span>
          </div>
          <p className="rcard__lede">
            {ex.fixes.length
              ? "Exact edits to your prompt, each rerun against the attacks that worked."
              : "Nothing to suggest. Snag won't invent an edit it can't verify."}
          </p>
          {ex.fixes[0] && (
            <p className="rcard__quote">
              <span className="snagv">{ex.fixes[0].before}</span> → <span className="heldv">{ex.fixes[0].after}</span>
            </p>
          )}
          <span className="rcard__cta">{ex.fixes.length ? "See the edits" : "See why"} <Arrow /></span>
        </Link>

        <Link className="rcard" to={`/e/${ex.slug}/history`}>
          <div className="rcard__top">
            <span className="label">history</span>
            <span className="rcard__n mono">{ex.history.length}</span>
          </div>
          <p className="rcard__lede">Compare with earlier scans. New failures are called out.</p>
          <p className="rcard__quote">
            <span className="heldv">{latest.fixed} fixed</span> ·{" "}
            <span className="snagv">{latest.added} new</span> ·{" "}
            <span className="dimmer">{latest.unchanged} unchanged</span>
          </p>
          <span className="rcard__cta">Compare runs <Arrow /></span>
        </Link>
      </section>
    </>
  );
}
