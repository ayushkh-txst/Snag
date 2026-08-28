import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { generateSurfaces, toggleSurface } from "../api/client";
import { NextBar, StepHead } from "../components/Shell";
import { ErrorState, Loading, NotFound } from "../components/States";
import { useProject } from "../hooks/useProject";
import { SURFACE_GROUPS, surfaceTitle, type Surface } from "../data";

const RISK_WHY: Record<Surface["risk"], string> = {
  high: "anything can go in here",
  medium: "limited, but not tightly",
  low: "tightly limited",
  none: "nothing to put here",
};

export function Surfaces() {
  const { slug } = useParams();
  const { data: ex, loading, error, notFound } = useProject(slug);
  const [surfaces, setSurfaces] = useState<Surface[]>([]);
  // Keyed by slug (not a plain boolean) and set SYNCHRONOUSLY before the
  // `await` below, so React 18 StrictMode's dev-only double-invoke of this
  // effect (mount -> cleanup -> mount again, same refs, before the first
  // pass's promise even resolves) can't fire `generateSurfaces` a second
  // time. `generateSurfaces` (POST) wipes and reinserts the WHOLE surfaces
  // table — a second concurrent call deletes the first call's rows out
  // from under any `toggleSurface(..., {confirmed: true})` already in
  // flight, silently losing every confirmation but the last generation's.
  const generatedForRef = useRef<string | null>(null);
  const confirmedForRef = useRef<string | null>(null);

  useEffect(() => {
    if (!ex || !slug) return;
    let cancelled = false;

    (async () => {
      let list = ex.surfaces;
      // A brand new project only has the always-present `chat` row until
      // the full map is generated once — first visit to this step builds
      // it (POST regenerates the whole map from the latest prompt/tools).
      if (list.length <= 1 && generatedForRef.current !== slug) {
        generatedForRef.current = slug;
        try {
          list = await generateSurfaces(slug);
        } catch {
          generatedForRef.current = null; // allow a retry on the next render
        }
      }
      if (cancelled) return;
      setSurfaces(list);

      if (confirmedForRef.current !== slug) {
        confirmedForRef.current = slug;
        // SURFACE-03/the runner's own contract: a scan only ever dispatches
        // to a surface that is BOTH confirmed and user-controlled. Reaching
        // this step and seeing a surface's default state IS confirming it
        // — without this, a user who never touches a checkbox would get a
        // scan that silently attacks nothing.
        for (const s of list) {
          void toggleSurface(slug, s.id, { confirmed: true }).catch(() => {});
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [ex, slug]);

  if (loading) return <Loading label="Loading surfaces…" />;
  if (notFound) return <NotFound slug={slug} />;
  if (error) return <ErrorState error={error} />;
  if (!ex) return <Loading label="Loading surfaces…" />;

  const on = surfaces.filter((s) => s.userControlled && s.risk !== "none");
  const total = on.reduce((n, s) => n + s.tests, 0);
  const slots = surfaces.filter((s) => s.kind === "template_var");

  const toggle = (id: string) => {
    if (!slug) return;
    const target = surfaces.find((s) => s.id === id);
    if (!target) return;
    const next = !target.userControlled;
    setSurfaces((xs) => xs.map((x) => (x.id === id ? { ...x, userControlled: next } : x)));
    toggleSurface(slug, id, { userControlled: next, confirmed: true }).catch(() => {
      setSurfaces((xs) => xs.map((x) => (x.id === id ? { ...x, userControlled: !next } : x)));
    });
  };

  return (
    <>
      <StepHead
        n="03"
        title="Where can someone else's words reach your model?"
        lede="Not just the chat box. Untick anything that can't be influenced from outside — testing it only costs you money."
        aside={
          <div className="qstat">
            <div className="qstat__n mono">{total}</div>
            <div className="qstat__l">attacks across {on.length} places</div>
          </div>
        }
      />

      {slots.length > 0 && (
        <div className="warnband">
          <div>
            <p className="warnband__h">
              Your prompt has {slots.length === 1 ? "a slot" : `${slots.length} slots`} that
              get filled in at runtime.
            </p>
            <p className="warnband__b">
              Whatever lands in{" "}
              {slots.map((t, i) => (
                <span key={t.id}>
                  <code className="mono">{t.path}</code>
                  {i < slots.length - 1 ? ", " : " "}
                </span>
              ))}
              is read as part of your instructions, not as something you're quoting. One
              poisoned document there can undo every rule above it.
            </p>
          </div>
        </div>
      )}

      <div className="sgroups">
        {SURFACE_GROUPS.map((g) => {
          const rows = surfaces.filter((s) => s.kind === g.kind);
          if (!rows.length) return null;
          return (
            <section className="sgroup" key={g.kind}>
              <header className="sgroup__head">
                <h2 className="sgroup__h">{g.title}</h2>
                <p className="sgroup__n">{g.note}</p>
              </header>
              <ul className="sgroup__list">
                {rows.map((s) => {
                  const dead = s.risk === "none";
                  return (
                    <li key={s.id}>
                      <label className="splace" data-off={!s.userControlled || dead || undefined}>
                        <input
                          type="checkbox"
                          checked={s.userControlled && !dead}
                          disabled={dead}
                          onChange={() => toggle(s.id)}
                        />
                        <span className="splace__body">
                          <span className="splace__title mono">{surfaceTitle(s)}</span>
                          <span className="splace__why">{s.note}</span>
                        </span>
                        <span className="splace__right">
                          <span className="splace__risk" data-risk={s.risk}>
                            {s.risk === "none" ? "safe" : `${s.risk} risk`}
                          </span>
                          <span className="splace__sub">{RISK_WHY[s.risk]}</span>
                          <span className="splace__tests mono">
                            {dead || !s.userControlled ? "not tested" : `${s.tests} attacks`}
                          </span>
                        </span>
                      </label>
                    </li>
                  );
                })}
              </ul>
            </section>
          );
        })}
      </div>

      <NextBar
        back={`/e/${ex.slug}/questions`}
        backLabel="Questions"
        next={`/e/${ex.slug}/config`}
        nextLabel="Next"
      />
    </>
  );
}
