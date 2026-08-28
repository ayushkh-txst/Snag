import { useEffect, useRef, useState } from "react";
import { Link, NavLink, useLocation, useParams } from "react-router-dom";
import { examples } from "../data";

const WORDMARK = (
  <svg className="wordmark" width="72" height="22" viewBox="0 0 72 22" aria-hidden="true">
    <path
      d="M2 15.5h11c3.4 0 3.4-9 6.8-9s3.4 9 6.8 9h9"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
    />
  </svg>
);

function ThemeToggle() {
  const [theme, setTheme] = useState<"light" | "dark">(() =>
    (document.documentElement.dataset.theme as "light" | "dark") ?? "light",
  );
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("snag-theme", theme); } catch { /* private mode */ }
  }, [theme]);

  return (
    <button
      className="themetoggle"
      onClick={() => setTheme(theme === "light" ? "dark" : "light")}
      aria-label={`Switch to ${theme === "light" ? "dark" : "light"} theme`}
      title={`Switch to ${theme === "light" ? "dark" : "light"} theme`}
    >
      <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
        <circle cx="7" cy="7" r="5.2" fill="none" stroke="currentColor" strokeWidth="1.3" />
        <path d="M7 1.8a5.2 5.2 0 0 0 0 10.4z" fill="currentColor" />
      </svg>
    </button>
  );
}

export function TopBar() {
  const loc = useLocation();
  const inApp = loc.pathname.startsWith("/e/");
  const { slug } = useParams();
  // 01-17: `byslug` silently falls back to the retail fixture for any slug
  // it doesn't recognise — fine for cosmetic display here, but ONLY the raw
  // route `slug` (never `ex.slug`) may ever be used to build a nav link
  // below, or every real (non-fixture) project would be redirected into the
  // wrong project on the first click.
  const knownExample = inApp ? examples.find((e) => e.slug === slug) : undefined;

  return (
    <header className="topbar">
      <div className="topbar__in">
        <Link to="/" className="brand">
          {WORDMARK}
          <span className="brand__name">Snag</span>
        </Link>

        {inApp && (
          <div className="topbar__project">
            <span className="label">{knownExample ? `example ${knownExample.n}` : "your project"}</span>
            <span className="topbar__projname">{knownExample ? knownExample.title : slug}</span>
          </div>
        )}

        <nav className="topbar__nav">
          <NavLink to="/examples" className="topbar__link">Examples</NavLink>
          <a className="topbar__link" href="#what-is-stored">Privacy</a>
          <ThemeToggle />
          <Link to="/paste" className="btn" data-variant="solid">
            Paste a prompt
          </Link>
        </nav>
      </div>
    </header>
  );
}

const STEPS = [
  { key: "rules", n: "01", label: "Rules" },
  { key: "questions", n: "02", label: "Questions" },
  { key: "surfaces", n: "03", label: "Surfaces" },
  { key: "config", n: "04", label: "Scan" },
  { key: "report", n: "05", label: "Report" },
];

const AFTER = [
  { key: "gaps", label: "Gaps" },
  { key: "fixes", label: "Fixes" },
  { key: "history", label: "History" },
];

/** The pipeline spine. The numbering is real: these steps happen in this order. */
export function Spine() {
  const { slug } = useParams();
  const loc = useLocation();
  const railRef = useRef<HTMLElement>(null);
  // 01-17: use the raw route `slug` for every nav link below — `byslug`'s
  // silent fallback-to-retail would otherwise redirect a real (non-fixture)
  // project into the wrong project's pages the moment this rail renders.
  const currentSlug = slug ?? "";
  const current = loc.pathname.split("/")[3] ?? "rules";
  const idx = STEPS.findIndex((s) => s.key === current);

  // On narrow screens the spine is a horizontal rail — keep the current step visible.
  useEffect(() => {
    const rail = railRef.current;
    if (!rail || rail.scrollWidth <= rail.clientWidth) return;
    const here = rail.querySelector<HTMLElement>('[data-state="here"]');
    here?.scrollIntoView({ inline: "center", block: "nearest", behavior: "smooth" });
  }, [current]);

  return (
    <nav className="spine" aria-label="Scan pipeline" ref={railRef}>
      <ol className="spine__steps">
        {STEPS.map((s, i) => (
          <li key={s.key}>
            <NavLink
              to={`/e/${currentSlug}/${s.key}`}
              className="spine__step"
              data-state={i < idx ? "done" : i === idx ? "here" : "ahead"}
            >
              <span className="spine__n mono">{s.n}</span>
              <span className="spine__label">{s.label}</span>
            </NavLink>
          </li>
        ))}
      </ol>
      <div className="spine__rest">
        {AFTER.map((s) => (
          <NavLink
            key={s.key}
            to={`/e/${currentSlug}/${s.key}`}
            className="spine__step"
            data-state={current === s.key ? "here" : "ahead"}
          >
            <span className="spine__n mono">·</span>
            <span className="spine__label">{s.label}</span>
          </NavLink>
        ))}
      </div>
      <div className="spine__switch">
        <span className="label">Example</span>
        <div className="spine__switchlist">
          {examples.map((e) => (
            <NavLink
              key={e.slug}
              to={`/e/${e.slug}/${STEPS[Math.max(idx, 0)]?.key ?? "rules"}`}
              className="spine__ex"
              data-on={e.slug === currentSlug || undefined}
              title={e.title}
            >
              {e.n}
            </NavLink>
          ))}
        </div>
      </div>
    </nav>
  );
}

export function StepHead({
  n,
  title,
  lede,
  aside,
}: {
  n: string;
  title: string;
  lede: string;
  aside?: React.ReactNode;
}) {
  return (
    <header className="stephead">
      <div className="stephead__main">
        <div className="stephead__n mono">step {n}</div>
        <h1 className="stephead__title">{title}</h1>
        <p className="stephead__lede">{lede}</p>
      </div>
      {aside && <div className="stephead__aside">{aside}</div>}
    </header>
  );
}

export function NextBar({
  back,
  backLabel,
  next,
  nextLabel,
  note,
  onNext,
  nextDisabled,
  nextBusy,
}: {
  back?: string;
  backLabel?: string;
  next?: string;
  nextLabel?: string;
  note?: string;
  /**
   * 01-17: an async action to run instead of a plain nav link (ScanConfig's
   * "Start the scan" has to call `startScan` and navigate with the
   * returned scan id, not just link to a fixed path). Takes precedence
   * over `next` when both are given.
   */
  onNext?: () => void;
  nextDisabled?: boolean;
  nextBusy?: boolean;
}) {
  return (
    <div className="nextbar">
      <div className="nextbar__l">
        {back && (
          <Link to={back} className="btn" data-variant="quiet">
            ← {backLabel}
          </Link>
        )}
      </div>
      {note && <p className="nextbar__note dim">{note}</p>}
      <div className="nextbar__r">
        {onNext ? (
          <button
            className="btn"
            data-variant="solid"
            disabled={nextDisabled}
            onClick={onNext}
          >
            {nextBusy ? "Starting…" : nextLabel} <span aria-hidden="true">→</span>
          </button>
        ) : (
          next && (
            <Link to={next} className="btn" data-variant="solid">
              {nextLabel} <span aria-hidden="true">→</span>
            </Link>
          )
        )}
      </div>
    </div>
  );
}
