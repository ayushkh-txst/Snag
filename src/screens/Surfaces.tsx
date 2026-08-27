import { useState } from "react";
import { useParams } from "react-router-dom";
import { NextBar, StepHead } from "../components/Shell";
import { RiskDot } from "../components/ui";
import { byslug, type Surface } from "../data";

const KIND_WORD: Record<Surface["kind"], string> = {
  template_var: "prompt template",
  tool_param: "tool parameter",
  tool_return: "tool output",
  chat: "chat input",
};

const SHAPES = [
  { shape: "free-text string", risk: "high", why: "arbitrary text reaches the model" },
  { shape: "string with a pattern", risk: "medium", why: "constrained, but usually loosely" },
  { shape: "number with no bounds", risk: "medium", why: "can be pushed past a business limit" },
  { shape: "enum", risk: "low", why: "closed set" },
  { shape: "boolean", risk: "none", why: "two values" },
  { shape: "object / array", risk: "medium", why: "recursed into, field by field" },
] as const;

export function Surfaces() {
  const { slug } = useParams();
  const ex = byslug(slug);
  const [surfaces, setSurfaces] = useState<Surface[]>(ex.surfaces);

  const totalTests = surfaces.filter((s) => s.userControlled).reduce((n, s) => n + s.tests, 0);
  const templateVars = surfaces.filter((s) => s.kind === "template_var");

  return (
    <>
      <StepHead
        n="03"
        title="Every place text reaches your model."
        lede="Attacks do not only arrive as a chat message. They arrive in a retrieved document, a tool argument, a search result, an error string. Snag found these by reading your prompt template and your tool schemas. Untick anything that is not attacker-controlled — testing a session-derived user_id just spends money."
        aside={
          <div className="qstat">
            <div className="qstat__n mono">{totalTests}</div>
            <div className="qstat__l">tests queued across {surfaces.filter((s) => s.userControlled).length} surfaces</div>
          </div>
        }
      />

      {templateVars.length > 0 && (
        <div className="warnband">
          <div className="warnband__mark mono">highest severity</div>
          <div>
            <p className="warnband__h">
              {templateVars.length === 1 ? "One slot in" : `${templateVars.length} slots in`} your
              system prompt {templateVars.length === 1 ? "is" : "are"} filled at runtime.
            </p>
            <p className="warnband__b">
              Text landing in{" "}
              {templateVars.map((t, i) => (
                <span key={t.id}>
                  <code className="mono">{t.path}</code>
                  {i < templateVars.length - 1 ? ", " : " "}
                </span>
              ))}
              sits at the same level as your rules. There is no marker in the prompt telling
              the model where your instructions stop and the data starts, so it has no way
              to tell them apart.
            </p>
          </div>
        </div>
      )}

      <div className="tblwrap card surftbl">
        <table className="tbl">
          <thead>
            <tr>
              <th>Surface</th>
              <th>Source</th>
              <th>Risk</th>
              <th className="num">Tests</th>
              <th>Attacker-controlled</th>
            </tr>
          </thead>
          <tbody>
            {surfaces.map((s) => (
              <tr key={s.id} data-off={!s.userControlled || undefined}>
                <td>
                  <div className="surfrow__path mono">{s.path}</div>
                  <div className="surfrow__note">{s.note}</div>
                </td>
                <td className="dim">{KIND_WORD[s.kind]}</td>
                <td><RiskDot risk={s.risk} withLabel /></td>
                <td className="num mono">{s.userControlled ? s.tests : 0}</td>
                <td>
                  <label className="switch">
                    <input
                      type="checkbox"
                      checked={s.userControlled}
                      onChange={() =>
                        setSurfaces((xs) =>
                          xs.map((x) =>
                            x.id === s.id ? { ...x, userControlled: !x.userControlled } : x,
                          ),
                        )
                      }
                    />
                    <span>{s.userControlled ? "yes" : "no — skip"}</span>
                  </label>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <section className="shapes">
        <p className="eyebrow">how a parameter's risk is decided</p>
        <div className="shapes__grid">
          {SHAPES.map((s) => (
            <div className="shapes__row" key={s.shape}>
              <span className="mono">{s.shape}</span>
              <RiskDot risk={s.risk} withLabel />
              <span className="dim">{s.why}</span>
            </div>
          ))}
        </div>
      </section>

      <NextBar
        back={`/e/${ex.slug}/questions`}
        backLabel="Questions"
        next={`/e/${ex.slug}/config`}
        nextLabel="Configure the scan"
        note="Attacks are aimed at specific surfaces. That is the difference between a scan that finds real holes and one that finds none and means nothing."
      />
    </>
  );
}
